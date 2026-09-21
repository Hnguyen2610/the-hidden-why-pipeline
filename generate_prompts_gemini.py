#!/usr/bin/env python3
"""
Phase 3 (Automated): Gemini Visual Prompt Generator
Automation pipeline for "The Hidden Why" YouTube channel.

Reads script text section files (./script/part*.txt) and uses the Gemini API
to automatically generate cinematic, detailed AI image prompts for each section.
"""

import argparse
import os
import re
import sys
import time
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    def load_dotenv(dotenv_path=".env"):
        if os.path.exists(dotenv_path):
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip().strip("'\"")
                        if key not in os.environ:
                            os.environ[key] = val
    load_dotenv()


def natural_sort_key(s: str):
    """Sort strings containing numbers naturally."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def parse_args():
    proj_dir = os.getenv("PROJECT_DIR", ".")
    default_script = os.path.join(proj_dir, "script")
    default_out = os.path.join(proj_dir, "visual_prompts_gemini.txt")

    parser = argparse.ArgumentParser(
        description="Automatically generate visual image prompts from script files using Gemini API."
    )
    parser.add_argument(
        "--script-dir",
        default=default_script,
        help="Directory containing script .txt files",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=default_out,
        help="Output text file path for generated visual prompts",
    )
    parser.add_argument(
        "--model",
        default="gemini-3.5-flash",
        help="Gemini API Model ID (default: gemini-3.5-flash, fallback: gemini-2.5-flash / gemini-1.5-flash)",
    )
    return parser.parse_args()


def call_gemini_api(prompt_text: str, api_key: str, model_id: str) -> str:
    """Call Gemini REST API generateContent endpoint.

    For each candidate model, transient failures (429 rate limit / 503
    overloaded) are retried on the SAME model with backoff before falling
    through to the next model — a temporarily overloaded model shouldn't
    immediately be abandoned for a different (possibly weaker) one.
    """
    models_to_try = [model_id, "gemini-3.5-flash", "gemini-2.5-flash"]
    # De-duplicate while preserving order
    seen = set()
    models_to_try = [m for m in models_to_try if not (m in seen or seen.add(m))]

    errors = []

    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        generation_config = {
            "temperature": 0.7,
            "maxOutputTokens": 3000,
        }
        if not model.startswith("gemini-1.5"):
            # 2.5+ models spend part of maxOutputTokens on internal "thinking" by default.
            # Disable it here since it isn't needed for a short formatting task, and was
            # previously eating the whole token budget before any visible text was written.
            generation_config["thinkingConfig"] = {"thinkingBudget": 0}
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt_text}
                    ]
                }
            ],
            "generationConfig": generation_config,
        }

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=30)
            except requests.exceptions.RequestException as e:
                errors.append(f"{model}: {e}")
                break  # network-level issue, move on to the next model

            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "").strip()
                errors.append(f"{model}: HTTP 200 but no usable content in response")
                break

            if response.status_code in (429, 503) and attempt < max_attempts:
                wait_seconds = 2 * attempt
                print(f"\n[INFO] {model} returned {response.status_code} (temporarily overloaded/rate limited). "
                      f"Retrying in {wait_seconds}s (attempt {attempt + 1}/{max_attempts})...")
                time.sleep(wait_seconds)
                continue

            errors.append(f"{model}: HTTP {response.status_code}: {response.text[:200]}")
            break

    raise RuntimeError("Gemini API call failed across all models/attempts:\n" + "\n".join(errors))


def call_groq_api(prompt_text: str, api_key: str, model_id: str) -> str:
    """Call Groq's OpenAI-compatible chat completions API.

    Used only as a fallback when Gemini fails entirely (all models/attempts
    exhausted) — Groq is not required for normal operation.
    """
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt_text}],
        "temperature": 0.7,
        "max_tokens": 3000,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Groq API request failed: {e}")

    if response.status_code == 200:
        data = response.json()
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            if content:
                return content.strip()
        raise RuntimeError("Groq API: HTTP 200 but no usable content in response")

    raise RuntimeError(f"Groq API HTTP {response.status_code}: {response.text[:200]}")


def split_into_scene_lines(script_text: str) -> list:
    """Recover the original sentence/beat lines of a script section.

    Sections saved by app.py's script splitter join original sentence lines
    with a blank line ("\\n\\n".join(units)), so splitting on blank lines
    recovers them exactly. Falls back to sentence-punctuation splitting for
    a section pasted/edited as one unbroken paragraph with no blank lines.
    """
    lines = [p.strip() for p in re.split(r'\n\s*\n', script_text.strip()) if p.strip()]
    if len(lines) <= 1:
        sentence_split = [s.strip() for s in re.split(r'(?<=[.!?])\s+', script_text.strip()) if s.strip()]
        if len(sentence_split) > 1:
            lines = sentence_split
    return lines


def generate_prompts_for_section(
    section_name: str,
    script_text: str,
    api_key: str,
    model_id: str,
    groq_key: "str | None" = None,
    groq_model: str = "openai/gpt-oss-120b",
) -> str:
    """Construct prompt for Gemini to generate visual cues.

    If Gemini fails entirely and a Groq API key is configured, falls back to
    Groq so this step can still complete without Gemini quota/availability.
    """
    scene_lines = split_into_scene_lines(script_text)
    numbered_lines = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(scene_lines))

    scene_instructions = ""
    if len(scene_lines) > 1:
        scene_instructions = f"""

The section above is made of {len(scene_lines)} numbered sentences/beats, listed below in
order:
{numbered_lines}

After the STOCK FOOTAGE QUERY line, add exactly {len(scene_lines)} more lines — one REAL,
filmable stock-footage query (same rules as above: plain, literal, 4-8 words, no surreal/
art-direction language) for EACH numbered sentence, so B-roll can be matched to what's
being said at that specific moment instead of the section as a whole. Number them to match
the sentences exactly, one per line, in order, with no blank lines in between:
SCENE 1: [4-8 plain words for sentence 1]
SCENE 2: [4-8 plain words for sentence 2]
(... one SCENE line per numbered sentence, ending at SCENE {len(scene_lines)})
"""

    system_prompt = f"""You are an expert AI art director for a top-tier YouTube psychology & technology explainer channel called "The Hidden Why".

Analyze the following script section and create 2 to 3 vivid, highly detailed image generation prompts (for Midjourney / Imagen / DALL-E) that visually represent the concepts in this section.

Style Guidelines:
- Aesthetic: Modern dark mode, cinematic lighting, moody atmospheric tones, subtle neon highlights, psychological & technological metaphor.
- Do NOT use meta text or stage instructions.
- Provide each prompt clearly numbered with a short description of what scene it illustrates.

Script Section ({section_name}):
\"\"\"
{script_text}
\"\"\"

Format your output like:
Prompt 1: [Detailed cinematic visual prompt]
Prompt 2: [Detailed cinematic visual prompt]

Then, on a final separate line, add ONE stock footage search query for finding a REAL
(non-AI-generated) video clip on stock footage sites like Pexels that pairs well with
this section. This must describe something that actually exists and could be filmed —
real people, real places, real objects, real everyday actions. Do NOT use any of the
surreal/art-direction language from the prompts above (no "holographic", "neon",
"digital void", "glowing", "cinematic render", metaphors, etc.). Keep it to 4-8 plain
English words, nouns and actions only.
Format that line EXACTLY like:
STOCK FOOTAGE QUERY: [4-8 plain words]
{scene_instructions}"""
    try:
        return call_gemini_api(system_prompt, api_key, model_id)
    except RuntimeError as gemini_err:
        if not groq_key:
            raise
        print(f"\n[INFO] Gemini failed ({gemini_err}). Falling back to Groq ({groq_model})...")
        try:
            return call_groq_api(system_prompt, groq_key, groq_model)
        except RuntimeError as groq_err:
            raise RuntimeError(
                f"Both Gemini and Groq failed.\nGemini: {gemini_err}\nGroq: {groq_err}"
            )


def main():
    args = parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[ERROR] GEMINI_API_KEY is not set.")
        print("Please add GEMINI_API_KEY=your_key_here to your .env file.")
        sys.exit(1)

    groq_key = os.getenv("GROQ_API_KEY")
    groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    script_dir = os.path.abspath(args.script_dir)
    output_path = os.path.abspath(args.output)

    if not os.path.exists(script_dir):
        print(f"[ERROR] Script directory '{script_dir}' does not exist.")
        sys.exit(1)

    txt_files = [
        f for f in os.listdir(script_dir)
        if f.endswith(".txt") and not f.endswith(".example.txt")
    ]
    txt_files.sort(key=natural_sort_key)

    if not txt_files:
        print(f"[WARNING] No script files found in '{script_dir}'.")
        sys.exit(0)

    print("=" * 65)
    print(" [GEMINI] The Hidden Why - Automated Visual Prompt Generator")
    print("=" * 65)
    print(f"Script Directory : {script_dir}")
    print(f"Output File      : {output_path}")
    print(f"Model ID         : {args.model}")
    print(f"Groq Fallback    : {'enabled (' + groq_model + ')' if groq_key else 'disabled (no GROQ_API_KEY set)'}")
    print(f"Files to process : {len(txt_files)} section(s)")
    print("-" * 65)

    all_outputs = [
        "=" * 65,
        " [PROMPTS] Automatically Generated Visual Prompts (via Gemini API)",
        " Channel: The Hidden Why (Psychology & Tech Explainer)",
        "=" * 65,
        ""
    ]

    for filename in txt_files:
        file_path = os.path.join(script_dir, filename)
        file_base = os.path.splitext(filename)[0]

        print(f"[ANALYZING] {filename}... ", end="", flush=True)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()

            if not content:
                print("SKIPPED (empty file)")
                continue

            generated_prompts = generate_prompts_for_section(
                section_name=file_base,
                script_text=content,
                api_key=api_key,
                model_id=args.model,
                groq_key=groq_key,
                groq_model=groq_model,
            )

            all_outputs.append(f"[SECTION] {file_base}")
            all_outputs.append(generated_prompts)
            all_outputs.append("-" * 50 + "\n")

            print("DONE!")

        except RuntimeError as err:
            print("FAILED!")
            print(f"[ERROR] {err}")
            sys.exit(1)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(all_outputs))

    print("-" * 65)
    print("[SUCCESS] Successfully generated all visual prompts!")
    print(f"[OUTPUT] Saved prompts to: {output_path}")
    print("=" * 65)


if __name__ == "__main__":
    main()

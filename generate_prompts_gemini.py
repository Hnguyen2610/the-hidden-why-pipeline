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
    parser = argparse.ArgumentParser(
        description="Automatically generate visual image prompts from script files using Gemini API."
    )
    parser.add_argument(
        "--script-dir",
        default="./script",
        help="Directory containing script .txt files (default: ./script)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="visual_prompts_gemini.txt",
        help="Output text file path for generated visual prompts (default: visual_prompts_gemini.txt)",
    )
    parser.add_argument(
        "--model",
        default="gemini-2.5-flash",
        help="Gemini API Model ID (default: gemini-2.5-flash, fallback: gemini-1.5-flash)",
    )
    return parser.parse_args()


def call_gemini_api(prompt_text: str, api_key: str, model_id: str) -> str:
    """Call Gemini REST API generateContent endpoint."""
    models_to_try = [model_id, "gemini-2.5-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
    # De-duplicate while preserving order
    seen = set()
    models_to_try = [m for m in models_to_try if not (m in seen or seen.add(m))]

    last_error = ""

    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt_text}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 1000,
            }
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "").strip()
            else:
                last_error = f"HTTP {response.status_code}: {response.text}"
        except Exception as e:
            last_error = str(e)

    raise RuntimeError(f"Gemini API call failed across models. Last error: {last_error}")


def generate_prompts_for_section(section_name: str, script_text: str, api_key: str, model_id: str) -> str:
    """Construct prompt for Gemini to generate visual cues."""
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
"""
    return call_gemini_api(system_prompt, api_key, model_id)


def main():
    args = parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[ERROR] GEMINI_API_KEY is not set.")
        print("Please add GEMINI_API_KEY=your_key_here to your .env file.")
        sys.exit(1)

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

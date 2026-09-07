#!/usr/bin/env python3
"""
Phase 3c: Google Veo 2 B-roll Video Generator
Automation pipeline for "The Hidden Why" YouTube channel.

Uses Google Veo 2 (via Gemini API) to generate 5-8 second B-roll video clips
for each script section, saved to ./footage/<section_name>.mp4

Veo 2 is a long-running async operation:
  1. Submit generation request -> get operation ID
  2. Poll every ~5 seconds until done
  3. Download and save video bytes
"""

import base64
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
                        os.environ.setdefault(key.strip(), val.strip().strip("'\""))
    load_dotenv()


VEO_MODEL = "veo-3.0-generate-preview"          # Veo 3 (best quality)
VEO_MODELS_FALLBACK = ["veo-2.0-generate-001"]   # Fallback if Veo 3 not accessible
POLL_INTERVAL_SECONDS = 8
MAX_POLL_ATTEMPTS = 60  # Max ~8 min wait per clip


def natural_sort_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def submit_veo_generation(prompt: str, api_key: str) -> tuple[str, str]:
    """
    Submit a Veo video generation request.
    Tries Veo 3 first, falls back to Veo 2 if not accessible.
    Returns (operation_name, model_used).
    """
    models_to_try = [VEO_MODEL] + VEO_MODELS_FALLBACK

    for model in models_to_try:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:predictLongRunning?key={api_key}"
        )
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "aspectRatio": "16:9",
                "durationSeconds": 7,
                "sampleCount": 1,
                "enhancePrompt": True,
            }
        }
        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
        except Exception as e:
            print(f"  [WARN] {model} connection error: {e}, trying next...")
            continue

        if response.status_code == 200:
            data = response.json()
            operation_name = data.get("name")
            if not operation_name:
                raise RuntimeError(f"Veo API did not return an operation name. Response: {data}")
            print(f"  [MODEL] Using {model}")
            return operation_name, model

        elif response.status_code in (400, 404, 403):
            error_msg = ""
            try:
                error_msg = response.json().get("error", {}).get("message", "")
            except Exception:
                pass
            print(f"  [WARN] {model} not accessible ({response.status_code}: {error_msg[:80]}), trying fallback...")
            continue
        else:
            error_msg = ""
            try:
                error_msg = response.json().get("error", {}).get("message", response.text[:300])
            except Exception:
                error_msg = response.text[:300]
            raise RuntimeError(f"Veo API HTTP {response.status_code}: {error_msg}")

    raise RuntimeError("All Veo models failed. Check your GEMINI_API_KEY and Veo API access.")



def poll_veo_operation(operation_name: str, api_key: str) -> bytes:
    """
    Poll the long-running operation until done, then return raw MP4 bytes.
    """
    # Use the long-running operations endpoint
    poll_url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"{operation_name}?key={api_key}"
    )
    headers = {"Content-Type": "application/json"}

    for attempt in range(MAX_POLL_ATTEMPTS):
        time.sleep(POLL_INTERVAL_SECONDS)

        response = requests.get(poll_url, headers=headers, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"Polling failed HTTP {response.status_code}: {response.text[:200]}")

        data = response.json()
        done = data.get("done", False)

        if not done:
            elapsed = (attempt + 1) * POLL_INTERVAL_SECONDS
            print(f"  [WAITING] {elapsed}s elapsed... (Veo is generating)", end="\r", flush=True)
            continue

        # Check for errors
        if "error" in data:
            raise RuntimeError(f"Veo generation error: {data['error']}")

        # Extract video bytes from response
        response_payload = data.get("response", {})
        predictions = response_payload.get("predictions", [])

        if not predictions:
            raise RuntimeError("Veo returned no predictions in completed operation.")

        for pred in predictions:
            # Try different field names Veo API may use
            for field in ["bytesBase64Encoded", "videoBytes", "video"]:
                raw = pred.get(field)
                if raw:
                    if isinstance(raw, str):
                        return base64.b64decode(raw)
                    elif isinstance(raw, dict):
                        inner = raw.get("bytesBase64Encoded") or raw.get("videoBytes", "")
                        if inner:
                            return base64.b64decode(inner)

        raise RuntimeError("Could not extract video bytes from Veo response.")

    raise RuntimeError(f"Veo generation timed out after {MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS} seconds.")


def parse_prompts_file(prompts_path: str) -> list:
    """
    Parse visual_prompts_gemini.txt into list of (section_name, first_prompt_text).
    """
    results = []
    with open(prompts_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r'\[SECTION\]\s*', content)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if not lines:
            continue
        section_name = lines[0].strip()
        prompt_text = ""
        for line in lines[1:]:
            m = re.match(r'Prompt\s*\d+\s*:\s*(.+)', line, re.IGNORECASE)
            if m:
                prompt_text = m.group(1).strip()
                break
        if section_name and prompt_text:
            results.append((section_name, prompt_text))

    return results


def enhance_prompt_for_video(base_prompt: str) -> str:
    """
    Append cinematic motion language to improve Veo output quality.
    """
    motion_suffix = (
        ", slow cinematic camera movement, moody dark atmospheric lighting, "
        "subtle motion, professional film quality, 4K, no text, no subtitles"
    )
    return base_prompt + motion_suffix


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate B-roll video clips using Google Veo 2 API.")
    parser.add_argument("--force", "-f", action="store_true", help="Regenerate clips even if they already exist")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[ERROR] GEMINI_API_KEY is not set in .env")
        sys.exit(1)

    prompts_file = os.path.abspath("visual_prompts_gemini.txt")
    footage_dir = os.path.abspath("./footage")
    os.makedirs(footage_dir, exist_ok=True)

    if not os.path.exists(prompts_file):
        print("[INFO] visual_prompts_gemini.txt not found. Automatically generating visual prompts first...")
        import subprocess
        res = subprocess.run([sys.executable, "generate_prompts_gemini.py"], cwd=os.path.dirname(prompts_file))
        if res.returncode != 0 or not os.path.exists(prompts_file):
            print("[ERROR] Failed to generate visual_prompts_gemini.txt automatically.")
            sys.exit(1)

    sections = parse_prompts_file(prompts_file)

    if not sections:
        print("[WARNING] No valid prompts found in visual_prompts_gemini.txt.")
        sys.exit(0)

    print("=" * 65)
    print(" [VEO] The Hidden Why - Veo 3/2 B-roll Video Generator")
    print("=" * 65)
    print(f"Sections to generate: {len(sections)}")
    print(f"Footage directory   : {footage_dir}")
    print(f"Duration per clip   : ~7 seconds")
    print("[NOTE] Each clip takes ~1-3 minutes. Please be patient.")
    print("-" * 65)

    generated = 0
    skipped = 0

    for section_name, prompt_text in sections:
        output_path = os.path.join(footage_dir, f"{section_name}.mp4")

        if os.path.exists(output_path) and not args.force:
            print(f"[SKIP] {section_name}.mp4 already exists")
            skipped += 1
            continue

        enhanced_prompt = enhance_prompt_for_video(prompt_text)
        print(f"\n[GENERATING] {section_name}")
        print(f"  Prompt: {prompt_text[:80]}...")

        try:
            operation_name, model_used = submit_veo_generation(enhanced_prompt, api_key)
            print(f"  [SUBMITTED] Operation: {operation_name[:60]}...")

            video_bytes = poll_veo_operation(operation_name, api_key)
            print()  # newline after polling progress

            with open(output_path, "wb") as f:
                f.write(video_bytes)

            size_mb = len(video_bytes) / (1024 * 1024)
            generated += 1
            print(f"  [DONE] Saved -> {section_name}.mp4 ({size_mb:.1f} MB)")

        except RuntimeError as e:
            print(f"\n[ERROR] {e}")
            print("[FALLBACK] This section will use a static image when building video.")

    print("\n" + "=" * 65)
    print(f"[DONE] Generated {generated} clip(s), skipped {skipped}")
    print(f"Footage saved to: {footage_dir}")
    print("Run 'python build_video.py' to assemble the final video.")
    print("=" * 65)


if __name__ == "__main__":
    main()

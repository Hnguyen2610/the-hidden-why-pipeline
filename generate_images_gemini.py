#!/usr/bin/env python3
"""
Phase 3b: Gemini Image Generator
Automation pipeline for "The Hidden Why" YouTube channel.

Reads visual_prompts_gemini.txt, sends each prompt to Gemini Imagen API,
and saves generated images to ./images/ matching audio section filenames.
"""

import base64
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
                        os.environ.setdefault(key.strip(), val.strip().strip("'\""))
    load_dotenv()


IMAGEN_MODEL = "imagen-3.0-generate-001"


def natural_sort_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def generate_image_from_prompt(prompt: str, api_key: str) -> bytes:
    """Call Gemini Imagen API and return raw PNG bytes."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{IMAGEN_MODEL}:generateImages?key={api_key}"
    payload = {
        "prompt": {"text": prompt},
        "config": {
            "numberOfImages": 1,
            "aspectRatio": "16:9",
            "outputMimeType": "image/png",
        }
    }
    response = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=60)

    if response.status_code != 200:
        error_detail = ""
        try:
            error_detail = response.json().get("error", {}).get("message", response.text)
        except Exception:
            error_detail = response.text
        raise RuntimeError(f"Imagen API HTTP {response.status_code}: {error_detail}")

    data = response.json()
    images = data.get("generatedImages") or data.get("images") or []
    if not images:
        raise RuntimeError("Imagen API returned no images.")

    img_data = images[0]
    b64 = img_data.get("imageBytes") or img_data.get("bytesBase64Encoded") or img_data.get("image", {}).get("imageBytes", "")
    if not b64:
        raise RuntimeError("Could not extract image bytes from Imagen API response.")

    return base64.b64decode(b64)


def parse_prompts_file(prompts_path: str) -> list[tuple[str, str]]:
    """
    Parse visual_prompts_gemini.txt into list of (section_name, first_prompt_text).
    Returns list of (section_name, prompt_text).
    """
    results = []
    with open(prompts_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split by [SECTION] markers
    blocks = re.split(r'\[SECTION\]\s*', content)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if not lines:
            continue
        section_name = lines[0].strip()
        # Find the first "Prompt N:" line
        prompt_text = ""
        for line in lines[1:]:
            m = re.match(r'Prompt\s*\d+\s*:\s*(.+)', line, re.IGNORECASE)
            if m:
                prompt_text = m.group(1).strip()
                break
        if section_name and prompt_text:
            results.append((section_name, prompt_text))

    return results


def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[ERROR] GEMINI_API_KEY is not set in .env")
        sys.exit(1)

    prompts_file = os.path.abspath("visual_prompts_gemini.txt")
    images_dir = os.path.abspath("./images")
    os.makedirs(images_dir, exist_ok=True)

    if not os.path.exists(prompts_file):
        print("[ERROR] visual_prompts_gemini.txt not found.")
        print("Run 'python generate_prompts_gemini.py' first.")
        sys.exit(1)

    sections = parse_prompts_file(prompts_file)

    if not sections:
        print("[WARNING] No valid prompts found in visual_prompts_gemini.txt.")
        sys.exit(0)

    print("=" * 65)
    print(" [IMAGEN] The Hidden Why - Gemini Image Generator")
    print("=" * 65)
    print(f"Sections to generate: {len(sections)}")
    print("-" * 65)

    generated = 0
    for section_name, prompt_text in sections:
        output_path = os.path.join(images_dir, f"{section_name}.png")

        if os.path.exists(output_path):
            print(f"[SKIP] {section_name}.png already exists")
            continue

        print(f"[GENERATING] {section_name}... ", end="", flush=True)
        try:
            img_bytes = generate_image_from_prompt(prompt_text, api_key)
            with open(output_path, "wb") as f:
                f.write(img_bytes)
            generated += 1
            print(f"DONE! -> {section_name}.png")
        except RuntimeError as e:
            print("FAILED!")
            print(f"[ERROR] {e}")
            print("\n[NOTE] Imagen API requires a paid Google Cloud project with Imagen enabled.")
            print("If you don't have access, use the manual image upload feature in the Studio UI instead.")
            sys.exit(1)

    print("-" * 65)
    print(f"[SUCCESS] Generated {generated} image(s) saved to {images_dir}")
    print("=" * 65)


if __name__ == "__main__":
    main()

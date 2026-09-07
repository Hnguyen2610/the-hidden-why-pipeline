#!/usr/bin/env python3
"""
Phase 1: ElevenLabs Voiceover Generator
Automation pipeline for "The Hidden Why" YouTube channel.

Reads script text section files (e.g., ./script/part1.txt) and generates audio 
files (e.g., ./audio/part1.mp3) using ElevenLabs Text-to-Speech API.
"""

import argparse
import os
import re
import sys
import requests

# Try importing python-dotenv; if not present, use a built-in fallback parser.
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
    """Sort strings containing numbers naturally (e.g., part1, part2, part10)."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate ElevenLabs voiceovers for video script sections."
    )
    parser.add_argument(
        "--script-dir",
        default="./script",
        help="Directory containing section script .txt files (default: ./script)",
    )
    parser.add_argument(
        "--output-dir",
        default="./audio",
        help="Directory to save generated .mp3 audio files (default: ./audio)",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force regeneration of audio files even if they already exist",
    )
    parser.add_argument(
        "--voice-id",
        default=os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
        help="ElevenLabs Voice ID (defaults to ELEVENLABS_VOICE_ID env var)",
    )
    parser.add_argument(
        "--model-id",
        default="eleven_multilingual_v2",
        help="ElevenLabs Model ID (default: eleven_multilingual_v2)",
    )
    return parser.parse_args()


def get_available_voices(api_key: str) -> list:
    """Fetch available voices for the account from ElevenLabs API."""
    url = "https://api.elevenlabs.io/v1/voices"
    headers = {"xi-api-key": api_key}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("voices", [])
    except Exception:
        pass
    return []


def generate_audio_for_text(text: str, voice_id: str, api_key: str, model_id: str) -> bytes:
    """
    Call ElevenLabs TTS API v1 and return binary MP3 content.
    Raises RuntimeError with clean error messages on failures.
    """
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
    except requests.exceptions.Timeout:
        raise RuntimeError("API request timed out. Please check your internet connection.")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Failed to connect to ElevenLabs API. Please check your network connection.")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Network request failed: {e}")

    if response.status_code == 200:
        return response.content
    elif response.status_code == 401:
        raise RuntimeError(
            "Unauthorized (401): Invalid ElevenLabs API Key. "
            "Please verify ELEVENLABS_API_KEY in your .env file or environment."
        )
    elif response.status_code == 402:
        voices = get_available_voices(api_key)
        msg = (
            "Payment / Subscription Required (402): Free users cannot use certain library voices via API.\n"
            "Here are voices available in your ElevenLabs account:\n"
        )
        if voices:
            for v in voices[:10]:
                msg += f"  - {v.get('name', 'Unknown')}: {v.get('voice_id')}\n"
            msg += "\nTo fix this: Set ELEVENLABS_VOICE_ID=<voice_id> in .env or pass --voice-id <voice_id>"
        else:
            msg += "Run 'python list_voices.py' to inspect voices available for your account."
        raise RuntimeError(msg)
    elif response.status_code == 429:
        raise RuntimeError(
            "Rate Limit / Quota Exceeded (429): You have run out of ElevenLabs credits "
            "or hit rate limits."
        )
    elif response.status_code == 404:
        raise RuntimeError(
            f"Not Found (404): Voice ID '{voice_id}' was not found in ElevenLabs. "
            "Please check ELEVENLABS_VOICE_ID in your configuration."
        )
    else:
        # Attempt to parse detail from API JSON response
        error_msg = f"HTTP Error {response.status_code}"
        try:
            err_json = response.json()
            if "detail" in err_json:
                detail = err_json["detail"]
                if isinstance(detail, dict) and "message" in detail:
                    error_msg += f": {detail['message']}"
                else:
                    error_msg += f": {detail}"
        except Exception:
            pass
        raise RuntimeError(error_msg)


def main():
    args = parse_args()

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("[ERROR] ELEVENLABS_API_KEY is not set.")
        print("Please create a .env file based on .env.example or export ELEVENLABS_API_KEY in your terminal.")
        sys.exit(1)

    script_dir = os.path.abspath(args.script_dir)
    output_dir = os.path.abspath(args.output_dir)

    if not os.path.exists(script_dir):
        print(f"[INFO] Script directory '{script_dir}' does not exist. Creating it now...")
        os.makedirs(script_dir, exist_ok=True)
        print(f"[INFO] Please place your script section text files (e.g. part1.txt) in '{script_dir}'.")
        sys.exit(0)

    os.makedirs(output_dir, exist_ok=True)

    # Find all .txt files (excluding .example or backup files)
    txt_files = [
        f for f in os.listdir(script_dir)
        if f.endswith(".txt") and not f.endswith(".example.txt")
    ]
    txt_files.sort(key=natural_sort_key)

    if not txt_files:
        print(f"[WARNING] No .txt files found in script directory '{script_dir}'.")
        print("Add script section files like part1.txt, part2.txt and run again.")
        sys.exit(0)

    print("=" * 60)
    print(" 🎬  The Hidden Why - ElevenLabs Voiceover Generator")
    print("=" * 60)
    print(f"Script Directory : {script_dir}")
    print(f"Output Directory : {output_dir}")
    print(f"Voice ID         : {args.voice_id}")
    print(f"Model ID         : {args.model_id}")
    print(f"Force Overwrite  : {args.force}")
    print(f"Files Found      : {len(txt_files)} file(s)")
    print("-" * 60)

    running_total_credits = 0
    generated_count = 0
    skipped_count = 0

    for filename in txt_files:
        file_base = os.path.splitext(filename)[0]
        script_file_path = os.path.join(script_dir, filename)
        output_file_path = os.path.join(output_dir, f"{file_base}.mp3")

        # Skip check if file exists and --force is not specified
        if os.path.exists(output_file_path) and not args.force:
            print(f"[SKIP] {output_file_path} (already exists, use --force to overwrite)")
            skipped_count += 1
            continue

        # Read text content
        try:
            with open(script_file_path, "r", encoding="utf-8") as f:
                text_content = f.read().strip()
        except Exception as e:
            print(f"[ERROR] Could not read file '{filename}': {e}")
            continue

        if not text_content:
            print(f"[SKIP] {filename} is empty.")
            skipped_count += 1
            continue

        char_count = len(text_content)
        print(f"[GENERATING] {filename} ({char_count} chars)... ", end="", flush=True)

        try:
            audio_bytes = generate_audio_for_text(
                text=text_content,
                voice_id=args.voice_id,
                api_key=api_key,
                model_id=args.model_id,
            )
            with open(output_file_path, "wb") as f:
                f.write(audio_bytes)

            running_total_credits += char_count
            generated_count += 1
            print(f"DONE! -> {file_base}.mp3 | Used: {char_count} credits | Running Total: {running_total_credits} credits")

        except RuntimeError as err:
            print("FAILED!")
            print(f"[ERROR] {err}")
            print("\nProcessing stopped to protect your API quota.")
            sys.exit(1)

    print("-" * 60)
    print("✨ Summary:")
    print(f"   Generated : {generated_count} audio file(s)")
    print(f"   Skipped   : {skipped_count} file(s)")
    print(f"   Credits   : {running_total_credits} characters/credits used in this run")
    print("=" * 60)


if __name__ == "__main__":
    main()

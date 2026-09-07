#!/usr/bin/env python3
"""
Utility script to list all available ElevenLabs voices for your account.
"""

import os
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


def main():
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("[ERROR] ELEVENLABS_API_KEY is not set in .env or environment.")
        sys.exit(1)

    url = "https://api.elevenlabs.io/v1/voices"
    headers = {"xi-api-key": api_key}

    print("Fetching available voices from ElevenLabs...")
    try:
        response = requests.get(url, headers=headers, timeout=15)
    except Exception as e:
        print(f"[ERROR] Failed to fetch voices: {e}")
        sys.exit(1)

    if response.status_code != 200:
        print(f"[ERROR] API returned HTTP {response.status_code}: {response.text}")
        sys.exit(1)

    voices = response.json().get("voices", [])
    if not voices:
        print("No voices found for this account.")
        return

    print("=" * 70)
    print(f" {'NAME':<20} | {'CATEGORY':<12} | {'VOICE ID'}")
    print("=" * 70)

    premade_voices = []
    other_voices = []

    for v in voices:
        name = v.get("name", "Unknown")
        voice_id = v.get("voice_id", "")
        category = v.get("category", "unknown")
        if category == "premade":
            premade_voices.append((name, category, voice_id))
        else:
            other_voices.append((name, category, voice_id))

    print("--- Premade / Default Voices (Recommended for Free Tier) ---")
    for name, category, voice_id in premade_voices:
        print(f" {name:<20} | {category:<12} | {voice_id}")

    if other_voices:
        print("\n--- Other / Custom Voices ---")
        for name, category, voice_id in other_voices:
            print(f" {name:<20} | {category:<12} | {voice_id}")

    print("=" * 70)
    print("💡 Tip: Copy a Voice ID above and paste it into your .env file:")
    print("   ELEVENLABS_VOICE_ID=<voice_id>")
    print("=" * 70)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Phase 1: ElevenLabs Voiceover Generator
Automation pipeline for "The Hidden Why" YouTube channel.

Reads script text section files (e.g., ./script/part1.txt) and generates audio 
files (e.g., ./audio/part1.mp3) using ElevenLabs Text-to-Speech API.
"""

import argparse
import asyncio
import os
import re
import sys
import requests

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False

DEFAULT_EDGE_TTS_VOICE = "en-US-AndrewNeural"  # Warm, Confident, Authentic, Honest — closest free match to the ElevenLabs "George" voice

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


def clean_script_text(text: str) -> str:
    """
    Strip markdown/citation artifacts that sometimes end up in pasted script
    text (e.g. from an AI-written draft) but were never meant to be spoken:
    '**bold**' markers, reference links like '([OpenAI][1])' or '[1]', and
    standalone '---' horizontal-rule lines. Left in, these get sent to the TTS
    engine as literal text, producing odd pronunciation/pauses, and also
    inflate the character count used for ElevenLabs quota/credit tracking.
    """
    text = re.sub(r'\(\[[^\]]*\]\[[^\]]*\]\)', '', text)
    text = re.sub(r'\[\d+\]', '', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'(?<!\w)\*(.+?)\*(?!\w)', r'\1', text, flags=re.DOTALL)

    lines = []
    for line in text.splitlines():
        stripped = re.sub(r'[ \t]+', ' ', line.strip())
        if stripped and not re.fullmatch(r'-{3,}', stripped):
            lines.append(stripped)
    return "\n".join(lines)


def parse_args():
    proj_dir = os.getenv("PROJECT_DIR", ".")
    default_script = os.path.join(proj_dir, "script")
    default_audio = os.path.join(proj_dir, "audio")

    parser = argparse.ArgumentParser(
        description="Generate ElevenLabs voiceovers for video script sections."
    )
    parser.add_argument(
        "--script-dir",
        default=default_script,
        help="Directory containing section script .txt files",
    )
    parser.add_argument(
        "--output-dir",
        default=default_audio,
        help="Directory to save generated .mp3 audio files",
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


def get_api_keys() -> list:
    """
    Read ElevenLabs API keys from environment. Supports a comma-separated
    ELEVENLABS_API_KEYS for multi-key fallback, falling back to the single
    ELEVENLABS_API_KEY for backward compatibility.
    """
    multi = os.getenv("ELEVENLABS_API_KEYS", "")
    keys = [k.strip() for k in multi.split(",") if k.strip()]
    if keys:
        return keys
    single = os.getenv("ELEVENLABS_API_KEY", "").strip()
    return [single] if single else []


def get_elevenlabs_remaining_quota(key_pool: "KeyPool") -> int:
    """
    Sum remaining character quota across all configured ElevenLabs keys, by
    querying each key's subscription info. Keys that fail to query (invalid
    key, network error) contribute 0 rather than aborting the estimate.
    """
    total_remaining = 0
    for key in key_pool.keys:
        try:
            res = requests.get(
                "https://api.elevenlabs.io/v1/user/subscription",
                headers={"xi-api-key": key},
                timeout=10,
            )
            if res.status_code == 200:
                data = res.json()
                limit = data.get("character_limit", 0) or 0
                used = data.get("character_count", 0) or 0
                total_remaining += max(0, limit - used)
        except Exception:
            pass
    return total_remaining


def generate_audio_edge_tts(text: str, voice: str, output_path: str, max_attempts: int = 3) -> None:
    """
    Generate audio using Microsoft Edge's free neural TTS (no API key, no quota).
    The underlying service occasionally drops a request with a generic "No audio
    was received" error unrelated to the text content (confirmed by retrying the
    exact same input immediately after and having it succeed) — retry a couple
    times with a short backoff before giving up, instead of failing the whole run.
    """
    async def _run():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)

    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            asyncio.run(_run())
            return
        except Exception as e:
            last_error = e
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)  # clean up empty/partial file left by the failed attempt
                except Exception:
                    pass
            if attempt < max_attempts:
                print(f"\n[INFO] edge-tts attempt {attempt}/{max_attempts} failed ({e}). Retrying...")
                import time
                time.sleep(1.5 * attempt)
    raise last_error


class KeyPool:
    """Tracks which configured API key is currently active, advancing forward
    (never back) as keys are found to be exhausted/invalid."""

    def __init__(self, keys: list):
        self.keys = keys
        self.index = 0

    @property
    def total(self) -> int:
        return len(self.keys)

    def current(self) -> str:
        return self.keys[self.index]

    def advance(self) -> bool:
        """Move to the next key. Returns False if there is no next key left."""
        if self.index + 1 < len(self.keys):
            self.index += 1
            return True
        return False


def _classify_response(response, api_key: str, voice_id: str):
    """
    Interpret an ElevenLabs API response.
    Returns (audio_bytes, error_message, retryable_with_other_key).
    Exactly one of audio_bytes/error_message is set.
    """
    if response.status_code == 200:
        return response.content, None, False

    if response.status_code == 401:
        detail_status = ""
        try:
            detail_status = response.json().get("detail", {}).get("status", "")
        except Exception:
            pass
        if detail_status == "quota_exceeded":
            return None, (
                "Quota Exceeded (401): This section's text is longer than the remaining "
                "ElevenLabs character quota for this key's billing period."
            ), True
        return None, (
            "Unauthorized (401): Invalid ElevenLabs API Key."
        ), True

    if response.status_code == 429:
        return None, (
            "Rate Limit / Quota Exceeded (429): This key has run out of ElevenLabs credits "
            "or hit rate limits."
        ), True

    if response.status_code == 402:
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
        return None, msg, False

    if response.status_code == 404:
        return None, (
            f"Not Found (404): Voice ID '{voice_id}' was not found in ElevenLabs. "
            "Please check ELEVENLABS_VOICE_ID in your configuration."
        ), False

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
    return None, error_msg, False


def generate_audio_for_text(text: str, voice_id: str, key_pool: KeyPool, model_id: str) -> bytes:
    """
    Call ElevenLabs TTS API v1 and return binary MP3 content.
    If the active key fails with an auth/quota error and more keys are
    configured in key_pool, automatically retries with the next key.
    Raises RuntimeError with a clean error message once all keys are exhausted.
    """
    url_tmpl = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    while True:
        api_key = key_pool.current()
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }

        try:
            response = requests.post(url_tmpl, json=payload, headers=headers, timeout=30)
        except requests.exceptions.Timeout:
            raise RuntimeError("API request timed out. Please check your internet connection.")
        except requests.exceptions.ConnectionError:
            raise RuntimeError("Failed to connect to ElevenLabs API. Please check your network connection.")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Network request failed: {e}")

        audio_bytes, error, retryable = _classify_response(response, api_key, voice_id)
        if audio_bytes is not None:
            return audio_bytes

        if retryable and key_pool.advance():
            print(
                f"\n[INFO] API key #{key_pool.index} failed ({error}) "
                f"Switching to key #{key_pool.index + 1}/{key_pool.total}..."
            )
            continue

        if retryable and key_pool.total > 1:
            raise RuntimeError(f"All {key_pool.total} configured ElevenLabs API key(s) failed. Last error: {error}")
        raise RuntimeError(error)


def main():
    args = parse_args()
    edge_voice = os.getenv("EDGE_TTS_VOICE", DEFAULT_EDGE_TTS_VOICE)

    api_keys = get_api_keys()
    if not api_keys and not EDGE_TTS_AVAILABLE:
        print("[ERROR] No ElevenLabs API key configured, and the free 'edge-tts' fallback is not installed.")
        print("Either set ELEVENLABS_API_KEY / ELEVENLABS_API_KEYS in .env, or run: pip install edge-tts")
        sys.exit(1)
    key_pool = KeyPool(api_keys) if api_keys else None

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

    # Read every section's text once, and work out which ones actually need
    # generation this run (respecting --force / already-existing .mp3 files).
    texts_by_file = {}
    for filename in txt_files:
        try:
            with open(os.path.join(script_dir, filename), "r", encoding="utf-8") as f:
                texts_by_file[filename] = clean_script_text(f.read().strip())
        except Exception as e:
            print(f"[ERROR] Could not read file '{filename}': {e}")
            texts_by_file[filename] = ""

    pending_files = []
    already_generated_files = []
    for filename in txt_files:
        file_base = os.path.splitext(filename)[0]
        output_file_path = os.path.join(output_dir, f"{file_base}.mp3")
        if os.path.exists(output_file_path) and not args.force:
            already_generated_files.append(filename)
        elif texts_by_file[filename]:
            pending_files.append(filename)

    pending_chars = sum(len(texts_by_file[f]) for f in pending_files)

    # Decide ONE engine for this entire run so a single video never mixes
    # ElevenLabs and edge-tts voices mid-way through.
    engine = "elevenlabs"
    remaining_quota = None
    if not api_keys:
        engine = "edge_tts"
    elif pending_files:
        remaining_quota = get_elevenlabs_remaining_quota(key_pool)
        if pending_chars > remaining_quota:
            engine = "edge_tts"

    if engine == "edge_tts" and not EDGE_TTS_AVAILABLE:
        print("[WARNING] Wanted to fall back to the free edge-tts engine, but the 'edge-tts' package "
              "is not installed (pip install edge-tts). Falling back to ElevenLabs anyway — "
              "this run may fail partway if quota runs out.")
        engine = "elevenlabs"

    print("=" * 60)
    print(" 🎬  The Hidden Why - Voiceover Generator")
    print("=" * 60)
    print(f"Script Directory : {script_dir}")
    print(f"Output Directory : {output_dir}")
    print(f"Force Overwrite  : {args.force}")
    print(f"Files Found      : {len(txt_files)} file(s), {len(pending_files)} to generate, "
          f"{len(already_generated_files)} already done")

    if engine == "elevenlabs":
        print(f"Voice ID         : {args.voice_id}")
        print(f"Model ID         : {args.model_id}")
        print(f"API Keys         : {key_pool.total} configured" + (" (fallback enabled)" if key_pool.total > 1 else ""))
        if remaining_quota is not None:
            print(f"ElevenLabs Quota : need {pending_chars}, {remaining_quota} remaining across {key_pool.total} key(s) — OK")
        print("Engine           : ElevenLabs (paid credits)")
    else:
        print(f"Engine           : edge-tts FREE fallback (voice: {edge_voice})")
        if remaining_quota is not None:
            print(f"[WARNING] Not enough ElevenLabs quota for this video: need {pending_chars} characters, "
                  f"only {remaining_quota} remaining across {key_pool.total} key(s).")
        print(f"[WARNING] Using the FREE edge-tts engine for all {len(pending_files)} section(s) being generated "
              "this run, so this video keeps one consistent voice instead of mixing ElevenLabs and edge-tts.")
        if already_generated_files:
            print(f"[WARNING] {len(already_generated_files)} section(s) already have audio from a previous run "
                  "(possibly ElevenLabs) and were NOT regenerated. If this video needs a single consistent voice "
                  "throughout, re-run with --force to regenerate ALL sections with edge-tts.")
    print("-" * 60)

    running_total_credits = 0
    generated_count = 0
    skipped_count = 0

    for filename in txt_files:
        file_base = os.path.splitext(filename)[0]
        output_file_path = os.path.join(output_dir, f"{file_base}.mp3")
        text_content = texts_by_file[filename]

        if os.path.exists(output_file_path) and not args.force:
            print(f"[SKIP] {output_file_path} (already exists, use --force to overwrite)")
            skipped_count += 1
            continue

        if not text_content:
            print(f"[SKIP] {filename} is empty.")
            skipped_count += 1
            continue

        char_count = len(text_content)
        print(f"[GENERATING] {filename} ({char_count} chars)... ", end="", flush=True)

        try:
            if engine == "edge_tts":
                generate_audio_edge_tts(text_content, edge_voice, output_file_path)
            else:
                audio_bytes = generate_audio_for_text(
                    text=text_content,
                    voice_id=args.voice_id,
                    key_pool=key_pool,
                    model_id=args.model_id,
                )
                with open(output_file_path, "wb") as f:
                    f.write(audio_bytes)

            running_total_credits += char_count
            generated_count += 1
            unit = "credits" if engine == "elevenlabs" else "chars (free)"
            print(f"DONE! -> {file_base}.mp3 | Used: {char_count} {unit} | Running Total: {running_total_credits} {unit}")

        except RuntimeError as err:
            print("FAILED!")
            print(f"[ERROR] {err}")
            print("\nProcessing stopped to protect your API quota.")
            sys.exit(1)
        except Exception as err:
            print("FAILED!")
            print(f"[ERROR] edge-tts generation failed: {err}")
            sys.exit(1)

    print("-" * 60)
    print("✨ Summary:")
    print(f"   Engine    : {'ElevenLabs' if engine == 'elevenlabs' else 'edge-tts (free)'}")
    print(f"   Generated : {generated_count} audio file(s)")
    print(f"   Skipped   : {skipped_count} file(s)")
    print(f"   Credits   : {running_total_credits} characters/credits used in this run")
    print("=" * 60)


if __name__ == "__main__":
    main()

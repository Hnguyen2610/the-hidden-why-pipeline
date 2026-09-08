#!/usr/bin/env python3
"""
Phase 2c: Pexels Automatic B-roll Video Downloader
Automation pipeline for "The Hidden Why" YouTube channel.

Uses Pexels API (or direct HD fetch) to automatically search and download
free HD cinematic B-roll video clips (.mp4) for each script section into ./footage/
"""

import glob
import os
import re
import subprocess
import sys
import json
import urllib.parse
import urllib.request

try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    import shutil
    FFMPEG_PATH = shutil.which("ffmpeg")

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

# Fallback keywords for "The Hidden Why" psychology/tech explainer themes
DEFAULT_KEYWORDS = {
    "part0_cold_open": "person scrolling smartphone dark room night",
    "part1_the_loop": "digital abstract glowing loop technology",
    "part2_the_easy_answer": "brain thinking psychology dopamine concept",
    "part3_the_missing_stopping_point": "infinite social media feed hands typing",
    "part4_four_features": "smartphone screen notification glowing cinematic",
    "part5_the_real_question": "thoughtful person looking out window dark cinematic"
}


def get_media_duration_seconds(path: str) -> float:
    """Probe a media file's duration (seconds) using ffmpeg. Returns 0.0 if unknown."""
    if not FFMPEG_PATH:
        return 0.0
    cmd = [FFMPEG_PATH, "-i", path, "-f", "null", "-"]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stderr = res.stderr.decode("utf-8", errors="ignore")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
    if not m:
        return 0.0
    hours, minutes, seconds = m.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def search_pexels_videos(query: str, api_key: "str | None" = None, per_page: int = 15,
                          orientation: str = "landscape") -> list:
    """Search Pexels and return the raw list of video result objects (may be empty)."""
    encoded_query = urllib.parse.quote(query)
    url = f"https://api.pexels.com/videos/search?query={encoded_query}&per_page={per_page}&orientation={orientation}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if api_key:
        headers["Authorization"] = api_key
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("videos", [])
    except Exception as e:
        print(f"  [API WARN] Pexels search failed: {e}")
        return []


def download_pexels_video(video: dict, output_path: str) -> bool:
    """Download the best-quality HD mp4 file for a single Pexels video result object."""
    video_files = video.get("video_files", [])
    hd_files = [f for f in video_files if f.get("file_type") == "video/mp4"]
    hd_files.sort(key=lambda x: x.get("width", 0), reverse=True)
    for vf in hd_files:
        link = vf.get("link")
        if not link:
            continue
        try:
            down_req = urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(down_req, timeout=30) as down_resp:
                with open(output_path, "wb") as out_f:
                    out_f.write(down_resp.read())
            return True
        except Exception as e:
            print(f"  [API WARN] Download failed: {e}")
    return False


def search_and_download_pexels_clips(query: str, output_prefix: str, target_duration: float,
                                      api_key: "str | None" = None, max_clips: int = 6,
                                      used_video_ids: "set | None" = None,
                                      orientation: str = "landscape") -> list:
    """
    Download enough DISTINCT Pexels clips to cover target_duration, instead of
    a single clip that build_video.py would otherwise have to loop over and
    over for long sections. Returns the list of output file paths actually
    written (empty if nothing could be downloaded).

    Files are named '{output_prefix}.mp4' when exactly one clip is used
    (matches the original single-clip naming), or '{output_prefix}_1.mp4',
    '{output_prefix}_2.mp4', ... when multiple clips are needed.

    `used_video_ids`, if given, is a set shared across ALL sections in this run —
    many sections end up with very similar search queries (this script always
    searches for "person + phone + dark room" variants), so without this,
    Pexels happily returns the exact same top result for two different
    sections. Video ids already in the set are skipped so no two sections end
    up showing the same clip, and any video actually used here is added to it.
    """
    if used_video_ids is None:
        used_video_ids = set()

    results = search_pexels_videos(query, api_key, per_page=max(15, max_clips * 3), orientation=orientation)
    if not results:
        return []

    downloaded_paths = []
    covered = 0.0
    skipped_duplicates = 0
    for video in results:
        if len(downloaded_paths) >= max_clips or covered >= target_duration:
            break
        video_id = video.get("id")
        if video_id is not None and video_id in used_video_ids:
            skipped_duplicates += 1
            continue
        tmp_path = f"{output_prefix}__tmp_{len(downloaded_paths) + 1}.mp4"
        if download_pexels_video(video, tmp_path):
            duration = video.get("duration") or get_media_duration_seconds(tmp_path)
            downloaded_paths.append((tmp_path, duration or 1.0))
            covered += duration or 1.0
            if video_id is not None:
                used_video_ids.add(video_id)

    if not downloaded_paths:
        if skipped_duplicates:
            print(f"[all {skipped_duplicates} match(es) already used by another section] ", end="", flush=True)
        return []

    final_paths = []
    if len(downloaded_paths) == 1:
        final_path = f"{output_prefix}.mp4"
        os.replace(downloaded_paths[0][0], final_path)
        final_paths.append(final_path)
    else:
        for idx, (tmp_path, _dur) in enumerate(downloaded_paths, start=1):
            final_path = f"{output_prefix}_{idx}.mp4"
            os.replace(tmp_path, final_path)
            final_paths.append(final_path)

    return final_paths


def clear_existing_footage_for_section(footage_dir: str, sec_name: str):
    """Remove any previously downloaded footage for a section (single or multi-clip
    naming) so switching between the two schemes across runs never leaves stale files."""
    for path in [os.path.join(footage_dir, f"{sec_name}.mp4")] + \
                glob.glob(os.path.join(footage_dir, f"{sec_name}_*.mp4")):
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def parse_sections(script_dir: str) -> list[tuple[str, str]]:
    """
    Find script sections. Try parsing visual_prompts_gemini.txt for tailored English search queries,
    or fallback to DEFAULT_KEYWORDS.
    """
    proj_dir = os.getenv("PROJECT_DIR", ".")
    prompts_file = os.path.abspath(os.path.join(proj_dir, "visual_prompts_gemini.txt"))
    queries_map = {}

    if os.path.exists(prompts_file):
        try:
            with open(prompts_file, "r", encoding="utf-8") as f:
                content = f.read()
            blocks = re.split(r'\[SECTION\]\s*', content)
            for block in blocks:
                lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
                if len(lines) >= 2:
                    sec_name = lines[0]

                    # Preferred: Gemini-generated "STOCK FOOTAGE QUERY:" line —
                    # a short, literal, real-world search phrase written
                    # specifically for stock footage sites, as opposed to the
                    # surreal/AI-art prompts above it which Pexels has no
                    # matching real footage for.
                    query_line = next(
                        (l for l in lines if re.match(r'STOCK FOOTAGE QUERY\s*:', l, re.IGNORECASE)),
                        None,
                    )
                    if query_line:
                        q = re.sub(r'STOCK FOOTAGE QUERY\s*:\s*', '', query_line, flags=re.IGNORECASE).strip()
                        q = q.strip('*_ ')
                        if q:
                            queries_map[sec_name] = q
                            continue

                    # Fallback (older visual_prompts_gemini.txt without the query
                    # line): find the first "Prompt N: <title>" line, then use the
                    # DESCRIPTIVE lines that follow it (not the poetic title
                    # itself) as the Pexels search query — titles like "The
                    # Dopamine Slot Machine" are bad literal search terms,
                    # while the description that follows ("hyper-realistic
                    # transparent human brain...") has concrete, searchable nouns.
                    STOPWORDS = {
                        "a", "an", "the", "of", "in", "on", "at", "into", "onto", "with",
                        "and", "or", "but", "is", "are", "was", "were", "to", "for", "by",
                        "their", "its", "his", "her", "as", "that", "this", "these", "those",
                    }
                    for idx, l in enumerate(lines[1:], start=1):
                        m = re.match(r'Prompt\s*\d+\s*:\s*(.*)', l, re.IGNORECASE)
                        if m:
                            title_text = m.group(1)
                            desc_lines = []
                            j = idx + 1
                            while j < len(lines):
                                nxt = lines[j]
                                if re.match(r'Prompt\s*\d+\s*:', nxt, re.IGNORECASE) or re.match(r'^-{5,}$', nxt):
                                    break
                                desc_lines.append(nxt)
                                j += 1
                            body_text = " ".join(desc_lines).strip() or title_text
                            clean_text = re.sub(r"[^a-zA-Z0-9\s]", " ", body_text)
                            words = [w for w in clean_text.split() if w.lower() not in STOPWORDS]
                            words = words[:8]
                            if words:
                                queries_map[sec_name] = " ".join(words)
                            break
        except Exception:
            pass

    sections = []
    if os.path.exists(script_dir):
        files = [f for f in os.listdir(script_dir) if f.endswith(".txt") and not f.endswith(".example.txt")]
        files.sort()
        for fname in files:
            sec_name = os.path.splitext(fname)[0]
            kw = queries_map.get(sec_name) or DEFAULT_KEYWORDS.get(sec_name, "dark tech cinematic psychology")
            sections.append((sec_name, kw))
    return sections


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Download HD B-roll video clips from Pexels.")
    parser.add_argument("--force", "-f", action="store_true", help="Overwrite existing clips")
    parser.add_argument("--portrait", action="store_true",
                         help="Download vertical 9:16 clips (for YouTube Shorts) instead of landscape 16:9")
    args = parser.parse_args()
    orientation = "portrait" if args.portrait else "landscape"

    proj_dir = os.getenv("PROJECT_DIR", ".")
    pexels_key = os.getenv("PEXELS_API_KEY", "")
    script_dir = os.path.abspath(os.path.join(proj_dir, "script"))
    audio_dir = os.path.abspath(os.path.join(proj_dir, "audio"))
    footage_dir = os.path.abspath(os.path.join(proj_dir, "footage"))
    os.makedirs(footage_dir, exist_ok=True)

    sections = parse_sections(script_dir)
    if not sections:
        print("[ERROR] No script files found in ./script/")
        sys.exit(1)

    print("=" * 65)
    print(" [B-ROLL] The Hidden Why - Automatic HD Video Downloader")
    print("=" * 65)
    print(f"Footage directory: {footage_dir}")
    print(f"Orientation      : {'Portrait 9:16 (Shorts)' if args.portrait else 'Landscape 16:9'}")
    print(f"Sections found   : {len(sections)}")
    print("-" * 65)

    downloaded = 0
    skipped = 0
    DEFAULT_TARGET_DURATION = 20.0  # used only if the section has no audio yet
    used_video_ids = set()  # shared across all sections so no clip repeats between them

    for sec_name, keyword in sections:
        existing_single = os.path.join(footage_dir, f"{sec_name}.mp4")
        existing_multi = glob.glob(os.path.join(footage_dir, f"{sec_name}_*.mp4"))
        already_have_footage = os.path.exists(existing_single) or bool(existing_multi)

        if already_have_footage and not args.force:
            print(f"[SKIP] {sec_name} already has footage "
                  f"({len(existing_multi) or 1} clip(s))")
            skipped += 1
            continue

        if already_have_footage:
            clear_existing_footage_for_section(footage_dir, sec_name)

        audio_path = os.path.join(audio_dir, f"{sec_name}.mp3")
        target_duration = (
            get_media_duration_seconds(audio_path)
            if os.path.exists(audio_path) else 0.0
        ) or DEFAULT_TARGET_DURATION

        print(f"[DOWNLOADING B-ROLL] {sec_name} (Search: '{keyword}', "
              f"need ~{target_duration:.0f}s)... ", end="", flush=True)

        output_prefix = os.path.join(footage_dir, sec_name)
        clip_paths = search_and_download_pexels_clips(
            keyword, output_prefix, target_duration, pexels_key,
            used_video_ids=used_video_ids, orientation=orientation,
        )
        if clip_paths:
            downloaded += 1
            total_mb = sum(os.path.getsize(p) for p in clip_paths) / (1024 * 1024)
            if len(clip_paths) == 1:
                print(f"DONE! -> {sec_name}.mp4 ({total_mb:.1f} MB)")
            else:
                print(f"DONE! -> {len(clip_paths)} clips ({total_mb:.1f} MB total, "
                      f"varied footage instead of one looped clip)")
        else:
            print("SKIPPED (Use manual mp4 or static image fallback)")

    print("-" * 65)
    print(f"[SUCCESS] Downloaded {downloaded} B-roll clip(s), skipped {skipped}")
    print("Run 'python build_video.py' to assemble final video with B-roll motion clips.")
    print("=" * 65)


if __name__ == "__main__":
    main()

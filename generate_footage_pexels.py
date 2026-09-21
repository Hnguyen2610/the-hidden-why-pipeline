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
import sys
import json
import urllib.error
import urllib.parse
import urllib.request

from audio_timing import (
    align_text_to_segments,
    detect_speech_segments,
    get_media_duration_seconds as _shared_get_media_duration_seconds,
)
from generate_prompts_gemini import split_into_scene_lines

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
    return _shared_get_media_duration_seconds(FFMPEG_PATH, path)


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


def search_pixabay_videos(query: str, api_key: "str | None" = None, per_page: int = 15) -> "list | None":
    """Search Pixabay and return the raw list of video hit objects (may be empty).

    Second free source alongside Pexels — different library, so it fills in
    matches Pexels doesn't have and increases how many distinct clips are
    available per section. Unlike Pexels, Pixabay's video endpoint has no
    server-side orientation filter, so orientation is applied client-side
    when normalizing results (see _normalize_pixabay_hit).

    Returns None (instead of []) specifically when Cloudflare — not Pixabay's
    own API — is the one rejecting the request (see _is_cloudflare_challenge),
    so callers can tell "blocked at the edge, retrying won't help this run"
    apart from "no matches" or a genuine transient error.
    """
    if not api_key:
        return []
    encoded_query = urllib.parse.quote(query)
    url = (f"https://pixabay.com/api/videos/?key={api_key}&q={encoded_query}"
           f"&per_page={max(3, min(per_page, 200))}&safesearch=true")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("hits", [])
    except urllib.error.HTTPError as e:
        if _is_cloudflare_challenge(e):
            return None
        print(f"  [API WARN] Pixabay search failed: {e}")
        return []
    except Exception as e:
        print(f"  [API WARN] Pixabay search failed: {e}")
        return []


def _is_cloudflare_challenge(err: "urllib.error.HTTPError") -> bool:
    """True when a 429 is Cloudflare's bot-challenge page in front of pixabay.com
    rejecting the request at the edge, rather than Pixabay's own API rate limiter.

    Confirmed by direct reproduction: even a single, freshly-made request with a
    valid key and a browser User-Agent gets this — the response is HTML titled
    "Just a moment..." with a `Cf-Mitigated: challenge` header, not Pixabay's
    normal JSON error body. No amount of retrying, backing off, or spacing out
    requests fixes this from a plain HTTP client — there's no JS engine here to
    solve the challenge — so it's treated as a distinct, unrecoverable-this-run
    condition instead of being retried like a real rate limit.
    """
    return err.code == 429 and err.headers.get("Cf-Mitigated") is not None


def _normalize_pexels_result(video: dict) -> "dict | None":
    """Reshape a raw Pexels video object into the common candidate shape
    shared with Pixabay, so downloading/dedup logic doesn't care which
    provider a clip came from."""
    video_id = video.get("id")
    if video_id is None:
        return None
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")]
    files.sort(key=lambda f: f.get("width", 0), reverse=True)
    if not files:
        return None
    return {
        "source": "pexels",
        "id": f"pexels_{video_id}",
        "duration": video.get("duration"),
        "download_urls": [f["link"] for f in files],
    }


def _normalize_pixabay_hit(hit: dict, orientation: str) -> "dict | None":
    """Reshape a raw Pixabay video hit into the common candidate shape,
    filtering out hits that don't match the requested orientation (Pixabay
    has no server-side orientation param, unlike Pexels)."""
    hit_id = hit.get("id")
    videos = hit.get("videos") or {}
    if hit_id is None:
        return None
    ordered = [videos[k] for k in ("large", "medium", "small", "tiny") if videos.get(k, {}).get("url")]
    if not ordered:
        return None
    width, height = ordered[0].get("width", 0), ordered[0].get("height", 0)
    if width and height:
        is_landscape = width >= height
        if (orientation == "landscape") != is_landscape:
            return None
    return {
        "source": "pixabay",
        "id": f"pixabay_{hit_id}",
        "duration": hit.get("duration"),
        "download_urls": [v["url"] for v in ordered],
    }


def download_clip(candidate: dict, output_path: str) -> bool:
    """Download the best-available rendition for a normalized video candidate
    (from either Pexels or Pixabay — both just resolve to direct mp4 URLs)."""
    for link in candidate["download_urls"]:
        try:
            down_req = urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(down_req, timeout=30) as down_resp:
                with open(output_path, "wb") as out_f:
                    out_f.write(down_resp.read())
            return True
        except Exception as e:
            print(f"  [API WARN] Download failed: {e}")
    return False


def search_and_download_clips(query: str, output_prefix: str, target_duration: float,
                               pexels_api_key: "str | None" = None, pixabay_api_key: "str | None" = None,
                               max_clips: int = 6, used_video_ids: "set | None" = None,
                               orientation: str = "landscape") -> tuple:
    """
    Download enough DISTINCT clips (from Pexels, then Pixabay to fill any gap)
    to cover target_duration, instead of a single clip that build_video.py
    would otherwise have to loop over and over for long sections.

    Returns (file_paths, source_info) — file_paths is the list of output files
    actually written (empty if nothing could be downloaded); source_info is
    {"pexels_found", "pixabay_found", "pexels_used", "pixabay_used"} so the
    caller can tell (and log) whenever one source found few/no matches for a
    query and the other had to fill in — the whole point of querying both.

    Pexels and Pixabay are queried in parallel and treated as equal peers —
    their two relevance-ranked result lists are interleaved (best-of-Pexels,
    best-of-Pixabay, 2nd-best-of-Pexels, ...) rather than exhausting one
    library before ever trying the other. This is what actually fixes "too
    few / poorly matching results": a query that starves on one library
    often has a hit on the other, and neither is assumed to be generally
    better than the other.

    Files are named '{output_prefix}.mp4' when exactly one clip is used
    (matches the original single-clip naming), or '{output_prefix}_1.mp4',
    '{output_prefix}_2.mp4', ... when multiple clips are needed.

    `used_video_ids`, if given, is a set shared across ALL sections in this run —
    many sections end up with very similar search queries (this script always
    searches for "person + phone + dark room" variants), so without this, a
    provider happily returns the exact same top result for two different
    sections. Video ids already in the set are skipped so no two sections end
    up showing the same clip, and any video actually used here is added to it
    (ids are prefixed by source so a Pexels and a Pixabay id never collide).
    """
    if used_video_ids is None:
        used_video_ids = set()

    per_page = max(15, max_clips * 3)
    pexels_raw = search_pexels_videos(query, pexels_api_key, per_page=per_page, orientation=orientation)
    pixabay_raw = search_pixabay_videos(query, pixabay_api_key, per_page=per_page)
    pixabay_blocked = pixabay_raw is None  # Cloudflare challenge, not a real "no matches" — see _is_cloudflare_challenge
    if pixabay_blocked:
        pixabay_raw = []

    pexels_candidates = [c for c in (_normalize_pexels_result(v) for v in pexels_raw) if c]
    pixabay_candidates = [c for c in (_normalize_pixabay_hit(h, orientation) for h in pixabay_raw) if c]

    # Interleave round-robin instead of concatenating — neither source is
    # favored, so a top Pixabay match gets tried before a 2nd/3rd-tier Pexels
    # one instead of only ever showing up once Pexels alone is exhausted.
    candidates = []
    for i in range(max(len(pexels_candidates), len(pixabay_candidates))):
        if i < len(pexels_candidates):
            candidates.append(pexels_candidates[i])
        if i < len(pixabay_candidates):
            candidates.append(pixabay_candidates[i])

    source_info = {
        "pexels_found": len(pexels_raw),
        "pixabay_found": len(pixabay_raw),
        "pexels_used": 0,
        "pixabay_used": 0,
        "pixabay_blocked": pixabay_blocked,
    }

    if not candidates:
        return [], source_info

    downloaded_paths = []
    covered = 0.0
    skipped_duplicates = 0
    for candidate in candidates:
        if len(downloaded_paths) >= max_clips or covered >= target_duration:
            break
        video_id = candidate["id"]
        if video_id in used_video_ids:
            skipped_duplicates += 1
            continue
        tmp_path = f"{output_prefix}__tmp_{len(downloaded_paths) + 1}.mp4"
        if download_clip(candidate, tmp_path):
            duration = candidate.get("duration") or get_media_duration_seconds(tmp_path)
            downloaded_paths.append((tmp_path, duration or 1.0, candidate["source"]))
            covered += duration or 1.0
            used_video_ids.add(video_id)
            source_info[f"{candidate['source']}_used"] += 1

    if not downloaded_paths:
        if skipped_duplicates:
            print(f"[all {skipped_duplicates} match(es) already used by another section] ", end="", flush=True)
        return [], source_info

    final_paths = []
    if len(downloaded_paths) == 1:
        final_path = f"{output_prefix}.mp4"
        os.replace(downloaded_paths[0][0], final_path)
        final_paths.append(final_path)
    else:
        for idx, (tmp_path, _dur, _src) in enumerate(downloaded_paths, start=1):
            final_path = f"{output_prefix}_{idx}.mp4"
            os.replace(tmp_path, final_path)
            final_paths.append(final_path)

    return final_paths, source_info


def search_and_download_pexels_video(query: str, output_path: str, pexels_api_key: str = "",
                                      pixabay_api_key: "str | None" = None) -> bool:
    """Single-clip convenience wrapper around search_and_download_clips, used
    by generate_footage_veo.py's Pexels fallback path when Veo generation
    fails for a section."""
    if pixabay_api_key is None:
        pixabay_api_key = os.getenv("PIXABAY_API_KEY", "")
    output_prefix = re.sub(r'\.mp4$', '', output_path, flags=re.IGNORECASE)
    paths, _source_info = search_and_download_clips(
        query, output_prefix, target_duration=9999.0,
        pexels_api_key=pexels_api_key, pixabay_api_key=pixabay_api_key, max_clips=1,
    )
    return bool(paths)


def clear_existing_footage_for_section(footage_dir: str, sec_name: str):
    """Remove any previously downloaded footage for a section (single, multi-clip,
    or Scene Planner naming) so switching between schemes across runs never
    leaves stale files."""
    paths = [os.path.join(footage_dir, f"{sec_name}.mp4")] + \
        glob.glob(os.path.join(footage_dir, f"{sec_name}_*.mp4")) + \
        [os.path.join(footage_dir, f"{sec_name}_scenes.json")]
    for path in paths:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _note_pixabay_blocked_once(source_info: dict, notice_state: dict):
    """Print the Cloudflare-block explanation at most once per run, regardless
    of how many times (once per section, or once per scene) it's checked."""
    if source_info.get("pixabay_blocked") and not notice_state["shown"]:
        print("\n  [NOTE] Pixabay is intermittently blocking this network with a "
              "Cloudflare challenge (confirmed: not a real rate limit, key, or quota "
              "issue — some requests still succeed). Affected scenes/sections fall "
              "back to Pexels only.")
        notice_state["shown"] = True


def download_scene_footage(sec_name: str, script_path: str, audio_path: str,
                            scene_queries: list, footage_dir: str,
                            pexels_key: str, pixabay_key: str, used_video_ids: set,
                            orientation: str, notice_state: dict) -> "dict | None":
    """
    Scene Planner: download one clip per original sentence in a section, timed
    to when that sentence is actually spoken (via the same real-speech-pause
    detection build_video.py uses for subtitles), so cuts land where the
    narration changes instead of at an arbitrary duration boundary.

    Returns None if scene mode isn't usable for this section (sentence count
    doesn't match the SCENE query count, or audio timing can't be determined)
    so the caller falls back to the section-level flow. Otherwise returns a
    summary dict and writes '{sec_name}_scene{N}.mp4' clips (missing ones left
    as None — build_video.py holds the previous scene's clip through those)
    plus '{sec_name}_scenes.json' recording exactly what time window and clip
    each scene resolved to, so build_video.py cuts at the same times these
    clips were fetched for.
    """
    with open(script_path, "r", encoding="utf-8") as f:
        script_text = f.read()
    scene_lines = split_into_scene_lines(script_text)
    if len(scene_lines) != len(scene_queries):
        return None

    total_duration = get_media_duration_seconds(audio_path)
    if not total_duration:
        return None
    segments = detect_speech_segments(FFMPEG_PATH, audio_path, total_duration)
    if not segments:
        return None
    windows = align_text_to_segments(scene_lines, segments)
    if len(windows) != len(scene_queries):
        return None

    scenes_json = []
    clips_downloaded = 0
    total_bytes = 0
    pexels_used = 0
    pixabay_used = 0
    weak_scenes = []  # scene numbers where Pexels found <3 matches for its specific query
    for i, ((start, end, _line), query) in enumerate(zip(windows, scene_queries), start=1):
        output_prefix = os.path.join(footage_dir, f"{sec_name}_scene{i}")
        clip_paths, source_info = search_and_download_clips(
            query, output_prefix, target_duration=max(0.1, end - start),
            pexels_api_key=pexels_key, pixabay_api_key=pixabay_key,
            max_clips=1, used_video_ids=used_video_ids, orientation=orientation,
        )
        _note_pixabay_blocked_once(source_info, notice_state)
        clip_name = None
        source = None
        if clip_paths:
            clip_name = os.path.basename(clip_paths[0])
            source = "pixabay" if source_info["pixabay_used"] else "pexels"
            clips_downloaded += 1
            total_bytes += os.path.getsize(clip_paths[0])
        pexels_used += source_info["pexels_used"]
        pixabay_used += source_info["pixabay_used"]
        if source_info["pexels_found"] < 3:
            weak_scenes.append(i)
        scenes_json.append({
            "scene": i, "start": round(start, 2), "end": round(end, 2),
            "query": query, "clip": clip_name, "source": source,
        })

    scenes_json_path = os.path.join(footage_dir, f"{sec_name}_scenes.json")
    with open(scenes_json_path, "w", encoding="utf-8") as f:
        json.dump(scenes_json, f, indent=2)

    return {
        "scenes": len(scenes_json),
        "clips_downloaded": clips_downloaded,
        "total_mb": total_bytes / (1024 * 1024),
        "pexels_used": pexels_used,
        "pixabay_used": pixabay_used,
        "weak_scenes": weak_scenes,
    }


def parse_sections(script_dir: str) -> "tuple[list[tuple[str, str]], dict]":
    """
    Find script sections. Try parsing visual_prompts_gemini.txt for tailored English search queries,
    or fallback to DEFAULT_KEYWORDS.

    Also extracts per-scene "SCENE N: <query>" lines when present (Scene
    Planner) — one stock-footage query per original sentence in the section,
    for matching B-roll to what's being said at that exact moment instead of
    the section as a whole. Returns (sections, scene_queries_map) where
    scene_queries_map only contains a section if its SCENE lines form a
    complete, gapless 1..N sequence — anything malformed is dropped entirely
    so the caller falls back to the section-level query instead of guessing.
    """
    proj_dir = os.getenv("PROJECT_DIR", ".")
    prompts_file = os.path.abspath(os.path.join(proj_dir, "visual_prompts_gemini.txt"))
    queries_map = {}
    scene_queries_map = {}

    if os.path.exists(prompts_file):
        try:
            with open(prompts_file, "r", encoding="utf-8") as f:
                content = f.read()
            blocks = re.split(r'\[SECTION\]\s*', content)
            for block in blocks:
                lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
                if len(lines) >= 2:
                    sec_name = lines[0]

                    # Scene Planner: one "SCENE N: <query>" line per original
                    # sentence, in addition to (not instead of) the section-level
                    # query below. Extracted unconditionally (before the section-
                    # level query's own 'continue' below) since it must run for
                    # every section regardless of which query branch is taken.
                    # Kept only if it's a complete, gapless 1..N sequence — a
                    # partial/malformed set is worse than none, since it would
                    # misalign scenes to the wrong sentences.
                    scene_lines_found = [l for l in lines if re.match(r'SCENE\s*\d+\s*:', l, re.IGNORECASE)]
                    if scene_lines_found:
                        parsed_scenes = {}
                        valid = True
                        for l in scene_lines_found:
                            m = re.match(r'SCENE\s*(\d+)\s*:\s*(.*)', l, re.IGNORECASE)
                            num = int(m.group(1))
                            q = m.group(2).strip().strip('*_ ')
                            if not q or num in parsed_scenes:
                                valid = False
                                break
                            parsed_scenes[num] = q
                        if valid and sorted(parsed_scenes.keys()) == list(range(1, len(parsed_scenes) + 1)):
                            scene_queries_map[sec_name] = [parsed_scenes[i] for i in range(1, len(parsed_scenes) + 1)]

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
    return sections, scene_queries_map


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
    pixabay_key = os.getenv("PIXABAY_API_KEY", "")
    script_dir = os.path.abspath(os.path.join(proj_dir, "script"))
    audio_dir = os.path.abspath(os.path.join(proj_dir, "audio"))
    footage_dir = os.path.abspath(os.path.join(proj_dir, "footage"))
    os.makedirs(footage_dir, exist_ok=True)

    sections, scene_queries_map = parse_sections(script_dir)
    if not sections:
        print("[ERROR] No script files found in ./script/")
        sys.exit(1)

    print("=" * 65)
    print(" [B-ROLL] The Hidden Why - Automatic HD Video Downloader")
    print("=" * 65)
    print(f"Footage directory: {footage_dir}")
    print(f"Orientation      : {'Portrait 9:16 (Shorts)' if args.portrait else 'Landscape 16:9'}")
    print(f"Sources          : Pexels{' + Pixabay' if pixabay_key else ' only (set PIXABAY_API_KEY for a 2nd free source)'}")
    print(f"Sections found   : {len(sections)}")
    print("-" * 65)

    downloaded = 0
    skipped = 0
    weak_pexels_sections = 0
    DEFAULT_TARGET_DURATION = 20.0  # used only if the section has no audio yet
    used_video_ids = set()  # shared across all sections so no clip repeats between them
    notice_state = {"shown": False}

    for sec_name, keyword in sections:
        existing_single = os.path.join(footage_dir, f"{sec_name}.mp4")
        existing_multi = glob.glob(os.path.join(footage_dir, f"{sec_name}_*.mp4"))
        existing_scenes_json = os.path.join(footage_dir, f"{sec_name}_scenes.json")
        already_have_footage = (
            os.path.exists(existing_single) or bool(existing_multi) or os.path.exists(existing_scenes_json)
        )

        if already_have_footage and not args.force:
            print(f"[SKIP] {sec_name} already has footage "
                  f"({len(existing_multi) or 1} clip(s))")
            skipped += 1
            continue

        if already_have_footage:
            clear_existing_footage_for_section(footage_dir, sec_name)

        audio_path = os.path.join(audio_dir, f"{sec_name}.mp3")
        script_path = os.path.join(script_dir, f"{sec_name}.txt")

        scene_queries = scene_queries_map.get(sec_name)
        if scene_queries and os.path.exists(script_path) and os.path.exists(audio_path):
            print(f"[DOWNLOADING B-ROLL] {sec_name} (Scene Planner: {len(scene_queries)} scene(s))... ",
                  end="", flush=True)
            scene_result = download_scene_footage(
                sec_name, script_path, audio_path, scene_queries, footage_dir,
                pexels_key, pixabay_key, used_video_ids, orientation, notice_state,
            )
            if scene_result:
                downloaded += 1
                scene_breakdown = " + ".join(
                    f"{scene_result[f'{s}_used']} {s.capitalize()}"
                    for s in ("pexels", "pixabay") if scene_result[f"{s}_used"]
                )
                print(f"DONE! -> {scene_result['clips_downloaded']}/{scene_result['scenes']} scene clip(s) "
                      f"({scene_result['total_mb']:.1f} MB total, {scene_breakdown}, cuts timed to narration)")
                if scene_result["weak_scenes"]:
                    weak_pexels_sections += 1
                    scene_list = ", ".join(str(n) for n in scene_result["weak_scenes"])
                    print(f"  [NOTE] Weak/no Pexels matches on scene(s) {scene_list} "
                          f"(few results for their specific queries).")
                continue
            print("\n  [NOTE] Scene timing unavailable for this section, falling back to section-level query.")

        target_duration = (
            get_media_duration_seconds(audio_path)
            if os.path.exists(audio_path) else 0.0
        ) or DEFAULT_TARGET_DURATION

        print(f"[DOWNLOADING B-ROLL] {sec_name} (Search: '{keyword}', "
              f"need ~{target_duration:.0f}s)... ", end="", flush=True)

        output_prefix = os.path.join(footage_dir, sec_name)
        clip_paths, source_info = search_and_download_clips(
            keyword, output_prefix, target_duration, pexels_key, pixabay_key,
            used_video_ids=used_video_ids, orientation=orientation,
        )

        _note_pixabay_blocked_once(source_info, notice_state)

        breakdown = " + ".join(
            f"{source_info[f'{s}_used']} {s.capitalize()}"
            for s in ("pexels", "pixabay") if source_info[f"{s}_used"]
        )

        if clip_paths:
            downloaded += 1
            total_mb = sum(os.path.getsize(p) for p in clip_paths) / (1024 * 1024)
            if len(clip_paths) == 1:
                print(f"DONE! -> {sec_name}.mp4 ({total_mb:.1f} MB, {breakdown})")
            else:
                print(f"DONE! -> {len(clip_paths)} clips ({total_mb:.1f} MB total, {breakdown}, "
                      f"varied footage instead of one looped clip)")
        else:
            print("SKIPPED (Use manual mp4 or static image fallback)")

        # Flag WHY, so it's obvious from the log alone when Pexels is the
        # weak link for this query and Pixabay had to (or couldn't) help.
        if source_info["pexels_found"] == 0:
            weak_pexels_sections += 1
            print(f"  [NOTE] Pexels: 0 matches for '{keyword}'."
                  + (" Pixabay covered it." if source_info["pixabay_used"] else " Pixabay had no match either."))
        elif source_info["pexels_found"] < 3:
            weak_pexels_sections += 1
            print(f"  [NOTE] Pexels: only {source_info['pexels_found']} weak match(es) for '{keyword}'."
                  + (f" Pixabay contributed {source_info['pixabay_used']} clip(s)." if source_info["pixabay_used"] else ""))

    print("-" * 65)
    print(f"[SUCCESS] Downloaded {downloaded} B-roll clip(s), skipped {skipped}")
    if weak_pexels_sections:
        print(f"[INFO] Pexels had weak/no matches on {weak_pexels_sections} section(s) this run "
              f"(see [NOTE] lines above) — {'Pixabay helped cover them.' if pixabay_key else 'add PIXABAY_API_KEY to cover these better.'}")
    print("Run 'python build_video.py' to assemble final video with B-roll motion clips.")
    print("=" * 65)


if __name__ == "__main__":
    main()

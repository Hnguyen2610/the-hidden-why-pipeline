#!/usr/bin/env python3
"""
Automated Video Assembler
Automation pipeline for "The Hidden Why" YouTube channel.

Combines generated audio section files (./audio/*.mp3) and section image files (./images/*.png/.jpg)
into a final video file (./video/final_video.mp4) using FFmpeg.

If custom images are not yet provided in ./images, automatically generates sleek, dark-mode 
fallback posters so you can assemble a draft video instantly!
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

from audio_timing import (
    align_text_to_segments,
    detect_speech_segments,
    get_media_duration_seconds as _shared_get_media_duration_seconds,
)

# Try importing Pillow for fallback poster generation
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Try importing imageio-ffmpeg for portable FFmpeg executable
try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FFMPEG_PATH = shutil.which("ffmpeg")


def natural_sort_key(s: str):
    """Sort strings containing numbers naturally."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def parse_args():
    proj_dir = os.getenv("PROJECT_DIR", ".")
    default_audio = os.path.join(proj_dir, "audio")
    default_image = os.path.join(proj_dir, "images")
    default_footage = os.path.join(proj_dir, "footage")
    default_script = os.path.join(proj_dir, "script")
    default_output = os.path.join(proj_dir, "video")

    parser = argparse.ArgumentParser(
        description="Assemble section audio files and images/footage into a final YouTube MP4 video."
    )
    parser.add_argument(
        "--audio-dir",
        default=default_audio,
        help="Directory containing section .mp3 audio files",
    )
    parser.add_argument(
        "--image-dir",
        default=default_image,
        help="Directory containing section image files",
    )
    parser.add_argument(
        "--footage-dir",
        default=default_footage,
        help="Directory containing Veo-generated .mp4 B-roll clips",
    )
    parser.add_argument(
        "--script-dir",
        default=default_script,
        help="Directory containing section .txt script files (used to burn in subtitles)",
    )
    parser.add_argument(
        "--output-dir",
        default=default_output,
        help="Directory to save the assembled final video",
    )
    parser.add_argument(
        "--output-name",
        default="final_video.mp4",
        help="Final video output filename (default: final_video.mp4)",
    )
    parser.add_argument(
        "--no-subtitles",
        action="store_true",
        help="Skip burning subtitles into the video (subtitles are ON by default)",
    )
    parser.add_argument(
        "--vertical",
        action="store_true",
        help="Render as vertical 1080x1920 (YouTube Shorts) instead of landscape 1920x1080. "
             "Footage/images are center-cropped to fill the vertical frame.",
    )
    parser.add_argument(
        "--sections",
        default=None,
        help="Comma-separated list of section base names (e.g. part1_section,part3_section) to "
             "include, in the given order — for cutting a Short out of a subset of an existing "
             "project's already-generated audio/footage. Default: all sections, natural-sorted.",
    )
    return parser.parse_args()


def create_fallback_image(output_image_path: str, title_text: str, width: int = 1920, height: int = 1080):
    """Generate a sleek, dark-mode fallback banner image for missing visuals."""
    if not HAS_PIL:
        raise RuntimeError("Pillow library is required for generating fallback images. Run: pip install Pillow")

    img = Image.new("RGB", (width, height), color="#0F172A") # Deep dark navy/slate
    draw = ImageDraw.Draw(img)

    # Draw subtle ambient background accents
    band_h = height / 100
    for i in range(100):
        color = (15, 23 + i // 4, 42 + i // 2)
        draw.rectangle([0, i * band_h, width, (i + 1) * band_h], fill=color)

    # Clean title typography
    clean_title = title_text.replace("_", " ").title()

    # Try using default font
    try:
        font_large = ImageFont.truetype("arial.ttf", 64)
        font_sub = ImageFont.truetype("arial.ttf", 36)
    except Exception:
        font_large = ImageFont.load_default()
        font_sub = font_large

    text_x = int(width * 0.05)
    text_y = int(height * 0.44)
    draw.text((text_x, text_y), "THE HIDDEN WHY", fill="#38BDF8", font=font_sub) # Neon cyan accent
    draw.text((text_x, text_y + 60), clean_title, fill="#F8FAFC", font=font_large)   # Crisp white text

    img.save(output_image_path)


def get_audio_duration_seconds(audio_path: str) -> float:
    """Probe an audio file's duration (seconds) using ffmpeg (no ffprobe dependency)."""
    duration = _shared_get_media_duration_seconds(FFMPEG_PATH, audio_path)
    if not duration:
        raise RuntimeError(f"Could not determine duration of '{audio_path}'")
    return duration


def clean_text_for_captions(text: str) -> str:
    """
    Strip markdown/citation artifacts that sometimes end up in pasted script
    text (e.g. from an AI-written draft) but are never actually spoken by the
    TTS voice: '**bold**' markers, reference links like '([OpenAI][1])' or
    '[1]', and standalone '---' horizontal-rule lines. Left in, these inflate
    the character count used for proportional subtitle timing (throwing off
    sync) and show up as literal junk characters in the burned-in captions.
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


def split_text_into_clauses(text: str, max_chars: int = 60) -> list[str]:
    """Split text at natural pause points (. ! ? , ;) so clause boundaries line
    up with where a TTS voice actually pauses, wrapping overly long clauses at
    word boundaries so each caption still stays readable on screen."""
    text = clean_text_for_captions(text)
    raw_parts = re.split(r'(?<=[.!?,;])\s+', text.strip())
    clauses = []
    for part in raw_parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= max_chars:
            clauses.append(part)
            continue
        words = part.split()
        current, current_len = [], 0
        for w in words:
            added = len(w) + (1 if current else 0)
            if current and current_len + added > max_chars:
                clauses.append(" ".join(current))
                current, current_len = [w], len(w)
            else:
                current.append(w)
                current_len += added
        if current:
            clauses.append(" ".join(current))
    return clauses


def compute_caption_timings(text: str, audio_path: str, duration_seconds: float,
                             max_chars_per_caption: int = 60) -> list[tuple[float, float, str]]:
    """
    Build subtitle timing from REAL detected pauses in the audio rather than
    assuming a constant speaking rate, so captions stay in sync even in long
    sections with lots of comma/period pauses. Falls back to simple
    character-proportional timing if no silence gaps could be detected.
    Returns a list of (start_seconds, end_seconds, caption_text).
    """
    clauses = split_text_into_clauses(text, max_chars_per_caption)
    if not clauses:
        return []

    segments = detect_speech_segments(FFMPEG_PATH, audio_path, duration_seconds)
    if segments:
        return align_text_to_segments(clauses, segments)

    total_chars = sum(len(c) for c in clauses) or 1
    timed = []
    cursor = 0.0
    for clause in clauses:
        piece = duration_seconds * (len(clause) / total_chars)
        start = cursor
        end = min(duration_seconds, cursor + piece)
        cursor = end
        timed.append((start, end, clause))
    return timed


def format_ass_timestamp(seconds: float) -> str:
    """Format seconds as an ASS timestamp: H:MM:SS.cc (centiseconds)."""
    seconds = max(0.0, seconds)
    total_cs = round(seconds * 100)
    hours, rem_cs = divmod(total_cs, 360000)
    minutes, rem_cs = divmod(rem_cs, 6000)
    secs, cs = divmod(rem_cs, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def generate_ass_content(text: str, audio_path: str, duration_seconds: float,
                          video_w: int, video_h: int, max_chars_per_caption: int = 60,
                          font_size: int = 20, margin_v: int = 60) -> str:
    """
    Build a complete, standalone .ass subtitle file (style baked in, not via
    ffmpeg's '-force_style') with PlayResX/PlayResY set to the EXACT output
    frame size. Letting ffmpeg's 'subtitles' filter auto-convert a plain .srt
    left MarginV's scaling ambiguous (confirmed by testing: it did not scale
    1:1 with the real frame size, pushing captions off the top in vertical
    mode) — writing the ASS ourselves removes that ambiguity entirely.
    """
    timed = compute_caption_timings(text, audio_path, duration_seconds, max_chars_per_caption)
    if not timed:
        return ""

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_w}\n"
        f"PlayResY: {video_h}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        f"1,0,0,0,100,100,0,0,1,3,1,2,20,20,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines = [header]
    for start, end, clause in timed:
        clause_ass = clause.replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{format_ass_timestamp(start)},{format_ass_timestamp(end)},"
            f"Default,,0,0,0,,{clause_ass}"
        )
    return "\n".join(lines)


def escape_path_for_ffmpeg_filter(path: str) -> str:
    """Escape a filesystem path for safe use inside an ffmpeg -vf filter argument
    (e.g. subtitles=<path>), which is sensitive to ':', '\\' and special chars."""
    p = os.path.abspath(path).replace("\\", "/")
    p = p.replace(":", "\\:")
    return p


def find_visual_for_section(file_base: str, footage_dir: str, image_dir: str,
                             fallback_width: int = 1920, fallback_height: int = 1080) -> tuple[list, str]:
    """
    Find the best available visual(s) for a section.
    Priority: Scene Planner set > multi-clip B-roll set > single B-roll clip > Static image > fallback poster.
    Returns (paths, type). For type 'video'/'image', paths is a list of file
    paths (more than one entry only for a multi-clip B-roll set — see
    generate_footage_pexels.py). For type 'scenes', paths is a list of
    {"start", "end", "clip"} dicts (one per sentence, absolute clip paths,
    already gap-filled — see below) for cutting exactly when the narration
    changes instead of at an arbitrary duration boundary.
    """
    # Priority 0: Scene Planner set ({file_base}_scenes.json + {file_base}_scene1.mp4, ...).
    # Checked before the legacy multi-clip glob below, which would otherwise treat
    # '{file_base}_scene1.mp4' etc. as an old-style duration-cycled clip set.
    scenes_json_path = os.path.join(footage_dir, f"{file_base}_scenes.json")
    if os.path.exists(scenes_json_path):
        try:
            with open(scenes_json_path, "r", encoding="utf-8") as f:
                raw_scenes = json.load(f)
        except Exception:
            raw_scenes = []

        resolved = []
        last_clip = None
        for s in raw_scenes:
            clip_name = s.get("clip")
            if clip_name:
                last_clip = os.path.join(footage_dir, clip_name)
            resolved.append({"start": s["start"], "end": s["end"], "clip": last_clip})

        # Backfill any leading scenes that had no clip yet when the first real
        # clip appears, so every scene has *some* clip — never gaps.
        first_clip = next((r["clip"] for r in resolved if r["clip"]), None)
        if first_clip:
            for r in resolved:
                if r["clip"] is not None:
                    break
                r["clip"] = first_clip
            return resolved, "scenes"
        # No scene got any clip at all — fall through to the priorities below.

    # Priority 1: multi-clip B-roll set ({file_base}_1.mp4, {file_base}_2.mp4, ...)
    # downloaded to cover a long section without looping one clip over and over.
    multi_paths = sorted(
        glob.glob(os.path.join(footage_dir, f"{file_base}_*.mp4")),
        key=lambda p: natural_sort_key(os.path.basename(p)),
    )
    if multi_paths:
        return multi_paths, "video"

    # Priority 1b: legacy single B-roll footage clip
    footage_path = os.path.join(footage_dir, f"{file_base}.mp4")
    if os.path.exists(footage_path):
        return [footage_path], "video"

    # Priority 2: Static image file
    extensions = [".png", ".jpg", ".jpeg", ".webp"]
    for ext in extensions:
        candidate = os.path.join(image_dir, f"{file_base}{ext}")
        if os.path.exists(candidate):
            return [candidate], "image"

    # Priority 3: Generate fallback dark-mode poster
    fallback_path = os.path.join(image_dir, f"{file_base}_fallback.png")
    create_fallback_image(fallback_path, file_base, fallback_width, fallback_height)
    return [fallback_path], "image"


def build_clip_playlist(clip_paths: list[str], target_duration: float, buffer_seconds: float = 1.5) -> list[str]:
    """
    Cycle through the available clips (in order) until their combined duration
    covers target_duration (+ a small buffer so '-shortest' always has enough
    to trim from). With a single clip this just repeats it enough times to
    reach the target — equivalent to the old '-stream_loop -1' behavior, but
    expressed as explicit repeated inputs so it composes with concat/subtitles.
    """
    if not clip_paths:
        return []
    durations = [get_audio_duration_seconds(p) or 1.0 for p in clip_paths]
    playlist = []
    cum = 0.0
    i = 0
    needed = target_duration + buffer_seconds
    while cum < needed and i < len(clip_paths) * 50:
        idx = i % len(clip_paths)
        playlist.append(clip_paths[idx])
        cum += durations[idx]
        i += 1
    return playlist or clip_paths[:1]


def main():
    args = parse_args()

    if not FFMPEG_PATH:
        print("[ERROR] FFmpeg executable not found!")
        print("Please install imageio-ffmpeg by running: pip install imageio-ffmpeg")
        sys.exit(1)

    audio_dir = os.path.abspath(args.audio_dir)
    image_dir = os.path.abspath(args.image_dir)
    footage_dir = os.path.abspath(args.footage_dir)
    script_dir = os.path.abspath(args.script_dir)
    output_dir = os.path.abspath(args.output_dir)
    burn_subtitles = not args.no_subtitles
    vertical = args.vertical
    video_w, video_h = (1080, 1920) if vertical else (1920, 1080)

    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(footage_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(audio_dir):
        print(f"[ERROR] Audio directory '{audio_dir}' does not exist.")
        print("Please run generate_audio.py first to generate voiceover files.")
        sys.exit(1)

    mp3_files = [f for f in os.listdir(audio_dir) if f.endswith(".mp3")]
    mp3_files.sort(key=natural_sort_key)

    if args.sections:
        wanted = [s.strip() for s in args.sections.split(",") if s.strip()]
        by_base = {os.path.splitext(f)[0]: f for f in mp3_files}
        missing = [s for s in wanted if s not in by_base]
        if missing:
            print(f"[ERROR] Requested section(s) not found in '{audio_dir}': {', '.join(missing)}")
            sys.exit(1)
        mp3_files = [by_base[s] for s in wanted]  # keep the user-requested order

    if not mp3_files:
        print(f"[ERROR] No .mp3 audio files found in '{audio_dir}'.")
        sys.exit(1)

    final_video_path = os.path.join(output_dir, args.output_name)

    print("=" * 65)
    print(" [VIDEO] The Hidden Why - Automated Video Assembler")
    print("=" * 65)
    print(f"Audio Directory : {audio_dir}")
    print(f"Footage Dir     : {footage_dir} (B-roll priority)")
    print(f"Image Directory : {image_dir} (fallback)")
    print(f"Output Video    : {final_video_path}")
    print(f"FFmpeg Path     : {FFMPEG_PATH}")
    print(f"Orientation     : {'Vertical 1080x1920 (Shorts)' if vertical else 'Landscape 1920x1080'}")
    print(f"Sections Found  : {len(mp3_files)} audio file(s)" + (" (filtered by --sections)" if args.sections else ""))
    print(f"Subtitles       : {'ON (burned into video)' if burn_subtitles else 'OFF'}")
    print("-" * 65)

    temp_segments = []
    temp_srt_files = []

    # Sized around ~4% of frame height (roughly YouTube's own auto-caption
    # size) instead of the old 20/26px, which read as noticeably small on a
    # 1920x1080/1080x1920 frame — especially on a phone screen. max_chars is
    # trimmed down alongside the font size since WrapStyle 2 (see
    # generate_ass_content) never auto-wraps a line that's too wide for the
    # frame; it would just run off the sides instead of wrapping.
    subtitle_font_size = 60 if vertical else 46
    subtitle_margin_v = 260 if vertical else 70
    subtitle_max_chars = 24 if vertical else 50

    try:
        for idx, mp3_file in enumerate(mp3_files):
            file_base = os.path.splitext(mp3_file)[0]
            audio_path = os.path.join(audio_dir, mp3_file)
            visual_paths, visual_type = find_visual_for_section(
                file_base, footage_dir, image_dir, fallback_width=video_w, fallback_height=video_h
            )

            segment_path = os.path.join(output_dir, f"_temp_segment_{idx:02d}.mp4")
            temp_segments.append(segment_path)

            if visual_type == "scenes":
                type_label = f"[SCENES x{len(visual_paths)}]"
            elif visual_type == "video" and len(visual_paths) > 1:
                type_label = f"[B-ROLL x{len(visual_paths)}]"
            else:
                type_label = "[B-ROLL]" if visual_type == "video" else "[IMAGE] "
            print(f"[RENDERING {idx+1}/{len(mp3_files)}] {type_label} {file_base}... ", end="", flush=True)

            # Probed once and reused for subtitle timing, the clip playlist, and the
            # explicit '-t' cutoff below (see note there on why '-shortest' alone
            # isn't reliable).
            audio_duration = get_audio_duration_seconds(audio_path)

            subtitle_expr = ""
            if burn_subtitles:
                script_path = os.path.join(script_dir, f"{file_base}.txt")
                if os.path.exists(script_path):
                    with open(script_path, "r", encoding="utf-8") as f:
                        script_text = f.read().strip()
                    if script_text:
                        try:
                            ass_content = generate_ass_content(
                                script_text, audio_path, audio_duration,
                                video_w, video_h,
                                max_chars_per_caption=subtitle_max_chars,
                                font_size=subtitle_font_size,
                                margin_v=subtitle_margin_v,
                            )
                            if ass_content:
                                ass_path = os.path.join(output_dir, f"_temp_subs_{idx:02d}.ass")
                                with open(ass_path, "w", encoding="utf-8") as f:
                                    f.write(ass_content)
                                temp_srt_files.append(ass_path)
                                escaped_ass = escape_path_for_ffmpeg_filter(ass_path)
                                subtitle_expr = f"subtitles='{escaped_ass}'"
                        except Exception as e:
                            print(f"\n  [WARN] Could not burn subtitles for {file_base}: {e}")

            if visual_type == "video":
                # Cycle through the section's clip(s) to cover the audio's full length
                # (repeats a single clip if that's all we have — same end result as the
                # old '-stream_loop -1', just expressed as repeated inputs so it can be
                # concatenated and have subtitles burned into the combined output).
                playlist = build_clip_playlist(visual_paths, audio_duration)

                inputs = []
                for p in playlist:
                    inputs += ["-i", p]
                inputs += ["-i", audio_path]
                audio_input_idx = len(playlist)

                if vertical:
                    # Fill the vertical frame (center-crop) — footage is normally
                    # landscape, and letterboxing it into a 9:16 frame would leave
                    # large black bars top/bottom, which looks broken for Shorts.
                    per_clip_scale = f"scale={video_w}:{video_h}:force_original_aspect_ratio=increase,crop={video_w}:{video_h}"
                else:
                    per_clip_scale = f"scale={video_w}:{video_h}:force_original_aspect_ratio=decrease,pad={video_w}:{video_h}:(ow-iw)/2:(oh-ih)/2"

                filter_parts = []
                concat_labels = ""
                for i in range(len(playlist)):
                    filter_parts.append(f"[{i}:v]{per_clip_scale},setsar=1,fps=25[v{i}]")
                    concat_labels += f"[v{i}]"
                filter_parts.append(f"{concat_labels}concat=n={len(playlist)}:v=1:a=0[vcat]")
                final_label = "vcat"
                if subtitle_expr:
                    filter_parts.append(f"[vcat]{subtitle_expr}[vout]")
                    final_label = "vout"

                cmd = [
                    FFMPEG_PATH,
                    "-y",
                    *inputs,
                    "-filter_complex", ";".join(filter_parts),
                    "-map", f"[{final_label}]",
                    "-map", f"{audio_input_idx}:a:0",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-pix_fmt", "yuv420p",
                    "-shortest",              # safety net
                    "-t", str(audio_duration),  # '-shortest' alone can overshoot by 1-2s with
                                                 # this filter graph (confirmed by testing) — an
                                                 # explicit hard cutoff is what actually keeps
                                                 # segment length == audio length, which subtitle
                                                 # sync depends on for every section after this one.
                    segment_path,
                ]
            elif visual_type == "scenes":
                # One input per sentence, each trimmed to exactly that sentence's
                # detected speech window and concatenated in order — cuts land
                # where the narration changes instead of at a duration boundary.
                # '-stream_loop -1' on every scene input covers the rare case
                # where a fetched clip is shorter than its scene's window; the
                # trim filter below still cuts it to the exact length needed.
                if vertical:
                    per_clip_scale = f"scale={video_w}:{video_h}:force_original_aspect_ratio=increase,crop={video_w}:{video_h}"
                else:
                    per_clip_scale = f"scale={video_w}:{video_h}:force_original_aspect_ratio=decrease,pad={video_w}:{video_h}:(ow-iw)/2:(oh-ih)/2"

                inputs = []
                filter_parts = []
                concat_labels = ""
                for i, scene in enumerate(visual_paths):
                    scene_duration = max(0.1, scene["end"] - scene["start"])
                    inputs += ["-stream_loop", "-1", "-i", scene["clip"]]
                    filter_parts.append(
                        f"[{i}:v]{per_clip_scale},setsar=1,fps=25,"
                        f"trim=duration={scene_duration:.3f},setpts=PTS-STARTPTS[v{i}]"
                    )
                    concat_labels += f"[v{i}]"
                inputs += ["-i", audio_path]
                audio_input_idx = len(visual_paths)

                filter_parts.append(f"{concat_labels}concat=n={len(visual_paths)}:v=1:a=0[vcat]")
                final_label = "vcat"
                if subtitle_expr:
                    filter_parts.append(f"[vcat]{subtitle_expr}[vout]")
                    final_label = "vout"

                cmd = [
                    FFMPEG_PATH,
                    "-y",
                    *inputs,
                    "-filter_complex", ";".join(filter_parts),
                    "-map", f"[{final_label}]",
                    "-map", f"{audio_input_idx}:a:0",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-pix_fmt", "yuv420p",
                    "-shortest",
                    "-t", str(audio_duration),
                    segment_path,
                ]
            else:
                # Static image + audio with Cinematic Slow Camera Zoom Motion
                subtitle_suffix = f",{subtitle_expr}" if subtitle_expr else ""
                cmd = [
                    FFMPEG_PATH,
                    "-y",
                    "-loop", "1",
                    "-i", visual_paths[0],
                    "-i", audio_path,
                    "-vf", f"scale={video_w}:{video_h}:force_original_aspect_ratio=increase,crop={video_w}:{video_h},zoompan=z='min(zoom+0.0008,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={video_w}x{video_h}:fps=25" + subtitle_suffix,
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-pix_fmt", "yuv420p",
                    "-shortest",
                    "-t", str(audio_duration),
                    segment_path,
                ]

            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode != 0:
                print("FAILED!")
                print(f"[ERROR] FFmpeg segment rendering failed:\n{res.stderr.decode('utf-8', errors='ignore')}")
                sys.exit(1)

            print("DONE!")

        # Concatenate all segment videos
        concat_list_path = os.path.join(output_dir, "_concat_list.txt")
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for seg in temp_segments:
                # Format file path for FFmpeg concat demuxer
                escaped = os.path.abspath(seg).replace("\\", "/")
                f.write(f"file '{escaped}'\n")

        print("-" * 65)
        print("[CONCATENATING] Assembling all segments into final MP4 video... ", end="", flush=True)

        concat_cmd = [
            FFMPEG_PATH,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            final_video_path,
        ]

        res = subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            print("FAILED!")
            print(f"[ERROR] FFmpeg concatenation failed:\n{res.stderr.decode('utf-8', errors='ignore')}")
            sys.exit(1)

        print("DONE!")

    finally:
        # Cleanup temporary segment and subtitle files
        for seg in temp_segments:
            if os.path.exists(seg):
                try:
                    os.remove(seg)
                except Exception:
                    pass
        for srt in temp_srt_files:
            if os.path.exists(srt):
                try:
                    os.remove(srt)
                except Exception:
                    pass
        concat_list_p = os.path.join(output_dir, "_concat_list.txt")
        if os.path.exists(concat_list_p):
            try:
                os.remove(concat_list_p)
            except Exception:
                pass

    video_size_mb = os.path.getsize(final_video_path) / (1024 * 1024)
    print("=" * 65)
    print(" [DONE] Final Video Successfully Assembled!")
    print("=" * 65)
    print(f" Video Path : {final_video_path}")
    print(f" File Size  : {video_size_mb:.2f} MB")
    print(" Next Step: Run 'python upload_youtube.py --video ./video/final_video.mp4'")
    print("=" * 65)


if __name__ == "__main__":
    main()

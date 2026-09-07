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
import os
import re
import shutil
import subprocess
import sys

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
    parser = argparse.ArgumentParser(
        description="Assemble section audio files and images/footage into a final YouTube MP4 video."
    )
    parser.add_argument(
        "--audio-dir",
        default="./audio",
        help="Directory containing section .mp3 audio files (default: ./audio)",
    )
    parser.add_argument(
        "--image-dir",
        default="./images",
        help="Directory containing section image files (default: ./images)",
    )
    parser.add_argument(
        "--footage-dir",
        default="./footage",
        help="Directory containing Veo-generated .mp4 B-roll clips (default: ./footage)",
    )
    parser.add_argument(
        "--output-dir",
        default="./video",
        help="Directory to save the assembled final video (default: ./video)",
    )
    parser.add_argument(
        "--output-name",
        default="final_video.mp4",
        help="Final video output filename (default: final_video.mp4)",
    )
    return parser.parse_args()


def create_fallback_image(output_image_path: str, title_text: str):
    """Generate a sleek, dark-mode fallback banner image (1920x1080) for missing visuals."""
    if not HAS_PIL:
        raise RuntimeError("Pillow library is required for generating fallback images. Run: pip install Pillow")

    width, height = 1920, 1080
    img = Image.new("RGB", (width, height), color="#0F172A") # Deep dark navy/slate
    draw = ImageDraw.Draw(img)

    # Draw subtle ambient background accents
    for i in range(100):
        color = (15, 23 + i // 4, 42 + i // 2)
        draw.rectangle([0, i * 10.8, width, (i + 1) * 10.8], fill=color)

    # Clean title typography
    clean_title = title_text.replace("_", " ").title()
    
    # Try using default font
    try:
        font_large = ImageFont.truetype("arial.ttf", 64)
        font_sub = ImageFont.truetype("arial.ttf", 36)
    except Exception:
        font_large = ImageFont.load_default()
        font_sub = font_large

    draw.text((100, 480), "THE HIDDEN WHY", fill="#38BDF8", font=font_sub) # Neon cyan accent
    draw.text((100, 540), clean_title, fill="#F8FAFC", font=font_large)   # Crisp white text

    img.save(output_image_path)


def find_visual_for_section(file_base: str, footage_dir: str, image_dir: str) -> tuple[str, str]:
    """
    Find the best available visual for a section.
    Priority: Veo footage clip (.mp4) > Static image (.png/.jpg) > Generated fallback poster.
    Returns (path, type) where type is 'video' or 'image'.
    """
    # Priority 1: Veo B-roll footage clip
    footage_path = os.path.join(footage_dir, f"{file_base}.mp4")
    if os.path.exists(footage_path):
        return footage_path, "video"

    # Priority 2: Static image file
    extensions = [".png", ".jpg", ".jpeg", ".webp"]
    for ext in extensions:
        candidate = os.path.join(image_dir, f"{file_base}{ext}")
        if os.path.exists(candidate):
            return candidate, "image"

    # Priority 3: Generate fallback dark-mode poster
    fallback_path = os.path.join(image_dir, f"{file_base}_fallback.png")
    create_fallback_image(fallback_path, file_base)
    return fallback_path, "image"


def main():
    args = parse_args()

    if not FFMPEG_PATH:
        print("[ERROR] FFmpeg executable not found!")
        print("Please install imageio-ffmpeg by running: pip install imageio-ffmpeg")
        sys.exit(1)

    audio_dir = os.path.abspath(args.audio_dir)
    image_dir = os.path.abspath(args.image_dir)
    footage_dir = os.path.abspath(args.footage_dir)
    output_dir = os.path.abspath(args.output_dir)

    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(footage_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(audio_dir):
        print(f"[ERROR] Audio directory '{audio_dir}' does not exist.")
        print("Please run generate_audio.py first to generate voiceover files.")
        sys.exit(1)

    mp3_files = [f for f in os.listdir(audio_dir) if f.endswith(".mp3")]
    mp3_files.sort(key=natural_sort_key)

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
    print(f"Sections Found  : {len(mp3_files)} audio file(s)")
    print("-" * 65)

    temp_segments = []

    try:
        for idx, mp3_file in enumerate(mp3_files):
            file_base = os.path.splitext(mp3_file)[0]
            audio_path = os.path.join(audio_dir, mp3_file)
            visual_path, visual_type = find_visual_for_section(file_base, footage_dir, image_dir)

            segment_path = os.path.join(output_dir, f"_temp_segment_{idx:02d}.mp4")
            temp_segments.append(segment_path)

            type_label = "[B-ROLL]" if visual_type == "video" else "[IMAGE] "
            print(f"[RENDERING {idx+1}/{len(mp3_files)}] {type_label} {file_base}... ", end="", flush=True)

            if visual_type == "video":
                # Overlay audio onto existing footage clip, trim/extend to match audio length
                cmd = [
                    FFMPEG_PATH,
                    "-y",
                    "-i", visual_path,    # B-roll video
                    "-i", audio_path,     # Voiceover audio
                    "-map", "0:v:0",      # Video from footage
                    "-map", "1:a:0",      # Audio from voiceover
                    "-c:v", "libx264",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-pix_fmt", "yuv420p",
                    "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
                    "-shortest",          # Cut to whichever is shorter
                    segment_path,
                ]
            else:
                # Static image + audio
                cmd = [
                    FFMPEG_PATH,
                    "-y",
                    "-loop", "1",
                    "-i", visual_path,
                    "-i", audio_path,
                    "-c:v", "libx264",
                    "-tune", "stillimage",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-pix_fmt", "yuv420p",
                    "-shortest",
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
        # Cleanup temporary segment files
        for seg in temp_segments:
            if os.path.exists(seg):
                try:
                    os.remove(seg)
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

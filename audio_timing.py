#!/usr/bin/env python3
"""
Shared audio-timing helpers for "The Hidden Why" pipeline.

Used by both build_video.py (subtitle timing) and generate_footage_pexels.py
(per-scene B-roll timing) — both need to map a list of text pieces (subtitle
clauses, or Scene Planner sentences) onto real wall-clock timestamps within a
section's audio file, based on actually-detected speech pauses rather than a
constant speaking-rate assumption.
"""

import re
import subprocess


def get_media_duration_seconds(ffmpeg_path: str, path: str) -> float:
    """Probe a media file's duration (seconds) using ffmpeg. Returns 0.0 if unknown."""
    if not ffmpeg_path:
        return 0.0
    cmd = [ffmpeg_path, "-i", path, "-f", "null", "-"]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stderr = res.stderr.decode("utf-8", errors="ignore")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
    if not m:
        return 0.0
    hours, minutes, seconds = m.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def detect_speech_segments(ffmpeg_path: str, audio_path: str, total_duration: float,
                            noise_db: str = "-30dB", min_silence: float = 0.15) -> list:
    """
    Find actual speech-active time ranges by detecting real silence gaps in the
    audio (ffmpeg silencedetect) — TTS voiceovers pause at commas/periods, and
    those pauses are NOT proportional to character count, so timing based
    purely on character count drifts more and more over a long section.
    Returns (start, end) tuples covering every non-silent stretch, in order.
    """
    cmd = [ffmpeg_path, "-i", audio_path, "-af", f"silencedetect=noise={noise_db}:d={min_silence}", "-f", "null", "-"]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stderr = res.stderr.decode("utf-8", errors="ignore")

    silence_starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", stderr)]
    silence_ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", stderr)]

    segments = []
    cursor = 0.0
    for s_start, s_end in zip(silence_starts, silence_ends):
        if s_start > cursor:
            segments.append((cursor, s_start))
        cursor = max(cursor, s_end)
    if cursor < total_duration:
        segments.append((cursor, total_duration))
    return segments


def build_speech_timeline(segments: list):
    """
    Build a mapping from a "speech-only" cumulative timeline (silence gaps
    removed) back to real wall-clock time. Returns (mapping, total_speech_time)
    where mapping is a list of (wall_start, wall_end, speech_start, speech_end).
    """
    mapping = []
    cum = 0.0
    for wall_start, wall_end in segments:
        length = wall_end - wall_start
        mapping.append((wall_start, wall_end, cum, cum + length))
        cum += length
    return mapping, cum


def speech_time_to_wallclock(t: float, mapping) -> float:
    """Convert a point on the cumulative speech-only timeline to a real timestamp."""
    if not mapping:
        return t
    for wall_start, wall_end, speech_start, speech_end in mapping:
        if t <= speech_end:
            span = speech_end - speech_start
            frac = 0.0 if span <= 0 else (t - speech_start) / span
            return wall_start + frac * (wall_end - wall_start)
    return mapping[-1][1]


def align_text_to_segments(pieces: list, segments: list) -> list:
    """
    Distribute text pieces (subtitle clauses, or Scene Planner sentences)
    proportionally (by character count) over the audio's TOTAL SPEECH TIME
    ONLY — silence gaps between segments are skipped over rather than
    counted, so drift never accumulates from pauses at commas/periods. This
    does not require piece count to match segment count, since it works on
    total speech-time share rather than 1:1 mapping.
    Returns a list of (start_seconds, end_seconds, piece_text).
    """
    if not pieces or not segments:
        return []

    mapping, total_speech_time = build_speech_timeline(segments)
    total_chars = sum(len(p) for p in pieces) or 1

    result = []
    cursor = 0.0
    for piece in pieces:
        share = total_speech_time * (len(piece) / total_chars)
        speech_start = cursor
        speech_end = min(total_speech_time, cursor + share)
        cursor = speech_end
        start = speech_time_to_wallclock(speech_start, mapping)
        end = speech_time_to_wallclock(speech_end, mapping)
        result.append((start, end, piece))

    return result

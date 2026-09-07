#!/usr/bin/env python3
"""
Phase 3: Visual Cue Extractor
Automation pipeline for "The Hidden Why" YouTube channel.

Extracts [VISUAL: ...] lines from script section text files in ./script
and formats them into a clean, numbered prompt list saved to visual_prompts.txt.
"""

import argparse
import os
import re
import sys


def natural_sort_key(s: str):
    """Sort strings containing numbers naturally."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract [VISUAL: ...] prompts from video script section files."
    )
    parser.add_argument(
        "--script-dir",
        default="./script",
        help="Directory containing script .txt files (default: ./script)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="visual_prompts.txt",
        help="Output text file path for extracted prompts (default: visual_prompts.txt)",
    )
    return parser.parse_args()


def extract_visual_prompts(script_dir: str):
    """Scan script files and extract all [VISUAL: ...] lines."""
    visual_pattern = re.compile(r'\[VISUAL:\s*(.*?)\]', re.IGNORECASE | re.DOTALL)
    prompts_by_file = []

    txt_files = [
        f for f in os.listdir(script_dir)
        if f.endswith(".txt") and not f.endswith(".example.txt")
    ]
    txt_files.sort(key=natural_sort_key)

    total_prompts = 0

    for filename in txt_files:
        filepath = os.path.join(script_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"[WARNING] Could not read '{filename}': {e}")
            continue

        matches = visual_pattern.findall(content)
        if matches:
            cleaned_matches = [m.strip().replace("\n", " ") for m in matches if m.strip()]
            if cleaned_matches:
                prompts_by_file.append((filename, cleaned_matches))
                total_prompts += len(cleaned_matches)

    return prompts_by_file, total_prompts


def main():
    args = parse_args()
    script_dir = os.path.abspath(args.script_dir)
    output_path = os.path.abspath(args.output)

    if not os.path.exists(script_dir):
        print(f"[ERROR] Script directory '{script_dir}' does not exist.")
        sys.exit(1)

    prompts_by_file, total_prompts = extract_visual_prompts(script_dir)

    print("=" * 60)
    print(" 🎨  The Hidden Why - Visual Cue Extractor (Phase 3)")
    print("=" * 60)

    if total_prompts == 0:
        print(f"[INFO] No [VISUAL: ...] cues found in '{script_dir}'.")
        print("💡 Hint: Add cue lines like '[VISUAL: Dark bedroom with phone glowing]' in your script text files.")
        return

    output_lines = [
        "=" * 60,
        " 🎨 Visual Prompts List — The Hidden Why",
        "=" * 60,
        f"Total Prompts Extracted: {total_prompts}\n",
    ]

    prompt_counter = 1
    for filename, prompts in prompts_by_file:
        output_lines.append(f"📁 {filename}:")
        for prompt in prompts:
            output_lines.append(f"  {prompt_counter}. {prompt}")
            prompt_counter += 1
        output_lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    print(f"[SUCCESS] Extracted {total_prompts} prompt(s) from {len(prompts_by_file)} file(s).")
    print(f"[OUTPUT] Saved prompt list to: {output_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()

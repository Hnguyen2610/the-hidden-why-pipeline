#!/usr/bin/env python3
"""
The Hidden Why — Local Web Control Panel (UI)
Flask-backed Web Application providing a sleek, modern GUI for the YouTube Automation Pipeline.
Supports Multi-Project / Multi-Video management (Worktrees).
"""

import os
import re
import sys
import json
import shutil
import subprocess
import threading

# Fix Windows console emoji / Unicode encoding
if sys.platform == "win32":
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
    sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)

from flask import Flask, render_template, request, jsonify, send_from_directory

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from generate_prompts_gemini import call_gemini_api
from generate_images_gemini import generate_image_from_prompt
from youtube_analytics import (
    attach_video_titles,
    default_analytics_scopes,
    fetch_video_analytics,
    has_required_scopes,
    parse_iso8601_duration,
    summarize_channel_overview,
)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config['TEMPLATES_AUTO_RELOAD'] = True

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
PROJECT_SUBDIRS = ["script", "audio", "images", "footage", "video"]
os.makedirs(PROJECTS_DIR, exist_ok=True)

# Migration check: If projects directory is empty, migrate existing root folders into Video_1
existing_projects = [d for d in os.listdir(PROJECTS_DIR) if os.path.isdir(os.path.join(PROJECTS_DIR, d))]
if not existing_projects:
    default_proj_name = "Video_1_Why_You_Cant_Stop_Scrolling"
    default_proj_path = os.path.join(PROJECTS_DIR, default_proj_name)
    os.makedirs(default_proj_path, exist_ok=True)

    for sub_name in PROJECT_SUBDIRS:
        old_dir = os.path.join(BASE_DIR, sub_name)
        new_dir = os.path.join(default_proj_path, sub_name)
        if os.path.exists(old_dir):
            shutil.move(old_dir, new_dir)
        else:
            os.makedirs(new_dir, exist_ok=True)

    old_prompts = os.path.join(BASE_DIR, "visual_prompts_gemini.txt")
    if os.path.exists(old_prompts):
        shutil.move(old_prompts, os.path.join(default_proj_path, "visual_prompts_gemini.txt"))

    existing_projects = [default_proj_name]

existing_projects.sort()
ACTIVE_PROJECT = existing_projects[0]


_dirs_verified_for = None


def get_active_proj_dir() -> str:
    global ACTIVE_PROJECT, _dirs_verified_for
    pdir = os.path.join(PROJECTS_DIR, ACTIVE_PROJECT)
    if _dirs_verified_for != ACTIVE_PROJECT:
        os.makedirs(pdir, exist_ok=True)
        for sub in PROJECT_SUBDIRS:
            os.makedirs(os.path.join(pdir, sub), exist_ok=True)
        _dirs_verified_for = ACTIVE_PROJECT
    return pdir


def get_proj_subdir(name: str) -> str:
    return os.path.join(get_active_proj_dir(), name)


def get_script_dir() -> str:
    return get_proj_subdir("script")


def get_audio_dir() -> str:
    return get_proj_subdir("audio")


def get_images_dir() -> str:
    return get_proj_subdir("images")


def get_footage_dir() -> str:
    return get_proj_subdir("footage")


def get_video_dir() -> str:
    return get_proj_subdir("video")


def get_packaging_path() -> str:
    return os.path.join(get_active_proj_dir(), "packaging.json")


def is_vertical_project() -> bool:
    """Whether the active project was created as a YouTube Shorts (vertical) project."""
    return os.path.exists(os.path.join(get_active_proj_dir(), ".vertical"))


# Global process logger for UI output logs
logs_lock = threading.Lock()
activity_logs = []
pipeline_state = {
    "running": False,
    "project": None,
    "failed_stage": None,
    "failed_stage_index": None,
}


def add_log(msg, log_type="info"):
    with logs_lock:
        activity_logs.append({"message": msg, "type": log_type})
        if len(activity_logs) > 300:
            activity_logs.pop(0)


def run_pipeline_script(cmd, env_vars=None, error_keywords=("ERROR",),
                         warning_keywords=(), success_keywords=("DONE", "SUCCESS")):
    """
    Run a pipeline stage script as a subprocess, streaming each stdout line to
    add_log() classified by keyword match (checked in order: error > warning >
    success > info). Shared by every /api/generate-*, /api/build-video and
    /api/upload-youtube route so they don't each hand-roll the same
    Popen + line-streaming loop with slightly different classification rules.
    """
    proc = subprocess.Popen(
        cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=env_vars,
    )
    for line in proc.stdout or []:
        line_str = line.strip()
        if not line_str:
            continue
        if any(kw in line_str for kw in error_keywords):
            log_type = "error"
        elif any(kw in line_str for kw in warning_keywords):
            log_type = "warning"
        elif any(kw in line_str for kw in success_keywords):
            log_type = "success"
        else:
            log_type = "info"
        add_log(line_str, log_type)
    proc.wait()
    return proc.returncode

def get_video_pipeline_stages(voice_id: str, force: bool = False, vertical: bool = False):
    stages = [
        ("Voiceover", [sys.executable, "generate_audio.py", "--voice-id", voice_id]),
        ("Visual prompts", [sys.executable, "generate_prompts_gemini.py"]),
        ("B-roll footage", [sys.executable, "generate_footage_pexels.py"]),
        ("Video assembly", [sys.executable, "build_video.py"]),
    ]
    if force:
        stages[0][1].append("--force")
        stages[2][1].append("--force")
    if vertical:
        stages[2][1].append("--portrait")
        stages[3][1].append("--vertical")
    return stages


def run_video_pipeline(proj_dir: str, proj_name: str, voice_id: str, force: bool = False,
                       vertical: bool = False, start_stage: int = 0):
    """Run video stages from start_stage, leaving YouTube upload separate."""
    env_vars = {**os.environ, "PROJECT_DIR": proj_dir, "PYTHONUTF8": "1"}
    stages = get_video_pipeline_stages(voice_id, force, vertical)
    pipeline_state.update({"running": True, "project": proj_name, "failed_stage": None, "failed_stage_index": None})

    for stage_index, (stage_name, command) in enumerate(stages[start_stage:], start=start_stage):
        add_log(f"[{proj_name}] Starting {stage_name}...", "info")
        return_code = run_pipeline_script(
            command,
            env_vars,
            error_keywords=("ERROR", "FAILED"),
            warning_keywords=("WARNING",),
            success_keywords=("DONE", "SUCCESS"),
        )
        if return_code != 0:
            pipeline_state.update({"running": False, "project": proj_name,
                                   "failed_stage": stage_name, "failed_stage_index": stage_index})
            add_log(f"[{proj_name}] {stage_name} failed; pipeline stopped.", "error")
            return
        add_log(f"[{proj_name}] {stage_name} finished.", "success")

    pipeline_state.update({"running": False, "project": proj_name, "failed_stage": None, "failed_stage_index": None})
    add_log(f"[{proj_name}] Video is ready. YouTube upload remains a separate action.", "success")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/projects", methods=["GET"])
def list_projects():
    global ACTIVE_PROJECT
    projs = [d for d in os.listdir(PROJECTS_DIR) if os.path.isdir(os.path.join(PROJECTS_DIR, d))]
    projs.sort()
    if ACTIVE_PROJECT not in projs and projs:
        ACTIVE_PROJECT = projs[0]
    return jsonify({
        "projects": projs,
        "active_project": ACTIVE_PROJECT
    })


@app.route("/api/projects/switch", methods=["POST"])
def switch_project():
    global ACTIVE_PROJECT
    data = request.json or {}
    proj_name = data.get("name")
    if not proj_name:
        return jsonify({"error": "Project name is required"}), 400
    existing = [d for d in os.listdir(PROJECTS_DIR) if os.path.isdir(os.path.join(PROJECTS_DIR, d))]
    if proj_name not in existing:
        return jsonify({"error": f"Project '{proj_name}' does not exist."}), 404
    ACTIVE_PROJECT = proj_name
    add_log(f"Switched active video project to: {proj_name}", "success")
    return jsonify({"message": f"Switched to {proj_name}", "active_project": ACTIVE_PROJECT})


@app.route("/api/projects/create", methods=["POST"])
def create_project():
    global ACTIVE_PROJECT
    data = request.json or {}
    raw_name = data.get("name", "").strip()
    if not raw_name:
        return jsonify({"error": "Vui lòng nhập tên Video mới!"}), 400

    # Clean name for directory
    clean_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in raw_name).strip()
    clean_name = clean_name.replace(" ", "_")
    if not clean_name:
        clean_name = "New_Video"

    vertical = bool(data.get("vertical"))

    pdir = os.path.join(PROJECTS_DIR, clean_name)
    os.makedirs(pdir, exist_ok=True)
    for sub in PROJECT_SUBDIRS:
        os.makedirs(os.path.join(pdir, sub), exist_ok=True)
    if vertical:
        open(os.path.join(pdir, ".vertical"), "w").close()
    else:
        vertical_marker = os.path.join(pdir, ".vertical")
        if os.path.exists(vertical_marker):
            os.remove(vertical_marker)

    ACTIVE_PROJECT = clean_name
    kind = "Shorts (dọc)" if vertical else "video thường"
    add_log(f"Created & switched to new Video Project ({kind}): {clean_name}", "success")
    return jsonify({"message": f"Đã tạo Video mới ({kind}): {clean_name}", "active_project": ACTIVE_PROJECT})


@app.route("/api/status")
def get_status():
    eleven_key = bool(os.getenv("ELEVENLABS_API_KEY"))
    gemini_key = bool(os.getenv("GEMINI_API_KEY"))
    youtube_secret = os.path.exists(os.path.join(BASE_DIR, "client_secret.json")) or os.path.exists(os.path.join(BASE_DIR, "client_secret.json.json"))
    youtube_token = os.path.exists(os.path.join(BASE_DIR, "token.json"))

    s_dir = get_script_dir()
    a_dir = get_audio_dir()
    i_dir = get_images_dir()
    v_dir = get_video_dir()

    scripts = [f for f in os.listdir(s_dir) if f.endswith(".txt") and not f.endswith(".example.txt")]
    audios = [f for f in os.listdir(a_dir) if f.endswith(".mp3")]
    images = [f for f in os.listdir(i_dir) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
    videos = [f for f in os.listdir(v_dir) if f.endswith(".mp4")]

    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")

    return jsonify({
        "elevenlabs_key": eleven_key,
        "gemini_key": gemini_key,
        "youtube_secret": youtube_secret,
        "youtube_token": youtube_token,
        "script_count": len(scripts),
        "audio_count": len(audios),
        "image_count": len(images),
        "video_count": len(videos),
        "voice_id": voice_id,
        "active_project": ACTIVE_PROJECT,
        "is_vertical": is_vertical_project(),
        "pipeline": dict(pipeline_state),
        "logs": activity_logs[-30:]
    })


@app.route("/api/scripts", methods=["GET"])
def list_scripts():
    s_dir = get_script_dir()
    scripts_data = []
    files = [f for f in os.listdir(s_dir) if f.endswith(".txt") and not f.endswith(".example.txt")]
    files.sort()
    for fname in files:
        fpath = os.path.join(s_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        scripts_data.append({
            "filename": fname,
            "char_count": len(content),
            "content": content
        })
    return jsonify({"scripts": scripts_data})


@app.route("/api/scripts/save", methods=["POST"])
def save_script():
    data = request.json
    filename = data.get("filename")
    content = data.get("content", "")
    if not filename:
        return jsonify({"error": "Filename is required"}), 400
    if not filename.endswith(".txt"):
        filename += ".txt"
    fpath = os.path.join(get_script_dir(), filename)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)
    add_log(f"Saved script file: {filename}", "success")
    return jsonify({"message": f"Saved {filename}"})


@app.route("/api/scripts/delete", methods=["POST"])
def delete_script():
    data = request.json
    filename = data.get("filename")
    fpath = os.path.join(get_script_dir(), filename)
    if os.path.exists(fpath):
        os.remove(fpath)
        add_log(f"Deleted script file: {filename}", "warning")
    return jsonify({"message": "Deleted"})


def _build_split_units(full_text: str):
    """Break the script into the smallest natural chunks we can split on.

    Normally one line == one sentence/beat (this pipeline's scripts are
    written that way). If the whole script was pasted as a single unbroken
    blob with no line breaks at all, fall back to sentence-punctuation
    splitting so there's still something granular enough to chunk.
    """
    units = [p.strip() for p in full_text.split("\n") if p.strip()]
    if len(units) <= 1:
        sentence_split = [s.strip() for s in re.split(r'(?<=[.!?])\s+', full_text.strip()) if s.strip()]
        if len(sentence_split) > 1:
            units = sentence_split
    return units


def _dangles(line: str) -> bool:
    """True if a line grammatically continues into the next one (a setup
    line for a question/cliffhanger/quote), so it's a bad place to cut."""
    return line.rstrip().endswith(("...", "…", ":"))


def _rule_based_chunks(units, target_len: int = 350, max_lookahead: int = 5):
    """Accumulate units into ~target_len-character chunks, same as before,
    but never cut right after a dangling line — pull following lines in
    until the setup/payoff pair lands in the same chunk (capped so a run of
    dangling lines can't swallow the rest of the script)."""
    chunks = []
    current = []
    current_len = 0
    i = 0
    n = len(units)

    while i < n:
        current.append(units[i])
        current_len += len(units[i])
        i += 1
        if current_len >= target_len:
            lookahead = 0
            while _dangles(current[-1]) and i < n and lookahead < max_lookahead:
                current.append(units[i])
                current_len += len(units[i])
                i += 1
                lookahead += 1
            chunks.append(current)
            current = []
            current_len = 0

    if current:
        chunks.append(current)

    return ["\n\n".join(chunk) for chunk in chunks]


def _parse_split_boundaries(raw_text: str, total: int):
    """Extract & validate the JSON array of ascending line-boundaries the
    AI splitter is asked to return. None on anything malformed."""
    match = re.search(r'\[[\d,\s]+\]', raw_text)
    if not match:
        return None
    try:
        boundaries = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not boundaries or not all(isinstance(b, int) for b in boundaries):
        return None
    if boundaries[-1] != total:
        return None
    prev = 0
    for b in boundaries:
        if b <= prev:
            return None
        prev = b
    return boundaries


def ai_split_sections(units, api_key: str, model_id: str = "gemini-3.5-flash"):
    """Ask Gemini to decide section boundaries (returns only line numbers,
    never rewritten text), then rebuild sections from the ORIGINAL units so
    the spoken script text is guaranteed byte-for-byte unchanged.

    Returns a list of section text blocks, or None if Gemini is unavailable
    or its response can't be trusted (caller should fall back to
    _rule_based_chunks in that case).
    """
    numbered_text = "\n".join(f"{i + 1}: {u}" for i, u in enumerate(units))
    prompt = f"""You are splitting a narration script into sections for video editing.

Below is the full script, broken into {len(units)} numbered lines (each line is one sentence or short phrase, in original order).

Group these numbered lines into consecutive sections of roughly 350 to 500 characters of spoken text each (about 30-45 seconds of narration). It is more important to end each section at a natural narrative/topic boundary than to hit the character count exactly. In particular, NEVER end a section right after a line that sets up a question, cliffhanger, or incomplete thought (e.g. a line ending in "...", ":", or a rhetorical question) when the very next line is its direct payoff or answer — keep such setup/payoff pairs in the same section.

Numbered lines:
{numbered_text}

Respond with ONLY a JSON array of ascending integers — the line number that ends each section — nothing else. The last number MUST equal {len(units)} (every line covered exactly once, no gaps, no repeats). Example format: [7, 15, 23, 31]"""

    raw = call_gemini_api(prompt, api_key, model_id)
    boundaries = _parse_split_boundaries(raw, len(units))
    if boundaries is None:
        return None

    sections = []
    prev = 0
    for b in boundaries:
        sections.append("\n\n".join(units[prev:b]))
        prev = b
    return sections


@app.route("/api/scripts/split-and-save", methods=["POST"])
def split_and_save_script():
    data = request.json or {}
    full_text = data.get("full_script", "").strip()
    if not full_text:
        return jsonify({"error": "Vui lòng nhập nội dung kịch bản!"}), 400

    s_dir = get_script_dir()
    p_dir = get_active_proj_dir()

    # Smart split logic into distinct video sections (~30-45s spoken per section, ~350-500 chars)
    # Parsed before touching disk so a bad input never wipes existing project data.
    sections = []
    split_method = "marker"
    raw_blocks = re.split(r'(?m)^(?:\[|\#|\bPART\s*\d+\b|\bPart\s*\d+\b)', full_text)
    cleaned_blocks = [b.strip() for b in raw_blocks if b.strip()]

    if len(cleaned_blocks) > 1:
        for idx, block in enumerate(cleaned_blocks):
            lines = block.splitlines()
            title_slug = f"part{idx}"
            if lines:
                first_line = re.sub(r'[^a-zA-Z0-9_\s]', '', lines[0]).strip().lower()
                first_line = re.sub(r'\s+', '_', first_line)[:20]
                if first_line:
                    title_slug = f"part{idx}_{first_line}"
            sections.append((title_slug, block.strip()))
    else:
        units = _build_split_units(full_text)

        chunk_texts = None
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key and len(units) > 1:
            try:
                chunk_texts = ai_split_sections(units, api_key)
            except Exception as e:
                add_log(f"AI Smart-Split failed ({e}), falling back to rule-based splitter.", "warning")
                chunk_texts = None

        split_method = "AI (Gemini)" if chunk_texts else "rule-based"
        if not chunk_texts:
            chunk_texts = _rule_based_chunks(units)

        for idx, text in enumerate(chunk_texts):
            slug = f"part{idx}_cold_open" if idx == 0 else f"part{idx}_section"
            sections.append((slug, text))

    if not sections:
        return jsonify({"error": "Không thể tách kịch bản thành các phần hợp lệ."}), 400

    # Clean existing script directory files
    for fname in os.listdir(s_dir):
        if fname.endswith(".txt") and not fname.endswith(".example.txt"):
            try:
                os.remove(os.path.join(s_dir, fname))
            except Exception:
                pass

    # Clean old generated output files for current project
    for dir_path in [get_audio_dir(), get_images_dir(), get_footage_dir()]:
        if os.path.exists(dir_path):
            for fname in os.listdir(dir_path):
                try:
                    f_full = os.path.join(dir_path, fname)
                    if os.path.isfile(f_full):
                        os.remove(f_full)
                except Exception:
                    pass

    prompts_file = os.path.join(p_dir, "visual_prompts_gemini.txt")
    if os.path.exists(prompts_file):
        try:
            os.remove(prompts_file)
        except Exception:
            pass

    # Save created section files
    created_files = []
    for slug, text in sections:
        fname = f"{slug}.txt"
        fpath = os.path.join(s_dir, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(text)
        created_files.append(fname)

    add_log(f"⚡ Smart Script Auto-Splitter ({split_method}): Created {len(created_files)} section(s) for {ACTIVE_PROJECT}!", "success")
    return jsonify({
        "message": f"Đã tự động chia kịch bản thành {len(created_files)} phần (section) cho dự án [{ACTIVE_PROJECT}] (phương thức: {split_method})!",
        "files": created_files
    })


@app.route("/api/generate-audio", methods=["POST"])
def run_generate_audio():
    data = request.json or {}
    force = data.get("force", False)
    voice_id = data.get("voice_id", os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb"))
    proj_dir = get_active_proj_dir()
    proj_name = ACTIVE_PROJECT

    def worker():
        add_log(f"[{proj_name}] Starting ElevenLabs Voiceover Generation...", "info")
        cmd = [sys.executable, "generate_audio.py", "--voice-id", voice_id]
        if force:
            cmd.append("--force")
        env_vars = {**os.environ, "PROJECT_DIR": proj_dir, "PYTHONUTF8": "1"}
        run_pipeline_script(cmd, env_vars, error_keywords=("ERROR", "FAILED"), warning_keywords=("WARNING",))
        add_log("Voiceover Generation process finished.", "success")

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": f"Voice generation started for project [{ACTIVE_PROJECT}]."})


@app.route("/api/generate-prompts", methods=["POST"])
def run_generate_prompts():
    proj_dir = get_active_proj_dir()
    proj_name = ACTIVE_PROJECT

    def worker():
        add_log(f"[{proj_name}] Starting Gemini Visual Prompt Generator...", "info")
        cmd = [sys.executable, "generate_prompts_gemini.py"]
        env_vars = {**os.environ, "PROJECT_DIR": proj_dir, "PYTHONUTF8": "1"}
        run_pipeline_script(cmd, env_vars)
        add_log("Prompt generation process finished.", "success")

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": "Prompt generation started."})


@app.route("/api/generate-images", methods=["POST"])
def run_generate_images():
    proj_dir = get_active_proj_dir()
    proj_name = ACTIVE_PROJECT

    def worker():
        add_log(f"[{proj_name}] Starting Imagen API Image Generator...", "info")
        cmd = [sys.executable, "generate_images_gemini.py"]
        env_vars = {**os.environ, "PROJECT_DIR": proj_dir, "PYTHONUTF8": "1"}
        run_pipeline_script(cmd, env_vars, error_keywords=("ERROR", "FAILED"))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": "Image generation started."})


@app.route("/api/generate-footage", methods=["POST"])
def run_generate_footage():
    data = request.json or {}
    force = data.get("force", False)
    proj_dir = get_active_proj_dir()
    proj_name = ACTIVE_PROJECT
    vertical = is_vertical_project()

    def worker():
        add_log(f"[{proj_name}] Starting HD B-roll Footage Generator...", "info")
        cmd = [sys.executable, "generate_footage_pexels.py"]
        if force:
            cmd.append("--force")
        if vertical:
            cmd.append("--portrait")
        env_vars = {**os.environ, "PROJECT_DIR": proj_dir, "PYTHONUTF8": "1"}
        run_pipeline_script(cmd, env_vars)

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": "B-roll Footage generation started. Watch the console log."})


@app.route("/api/prompts", methods=["GET"])
def get_prompts():
    path = os.path.join(get_active_proj_dir(), "visual_prompts_gemini.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return jsonify({"content": f.read()})
    return jsonify({"content": "No generated prompts found yet. Click 'Generate Prompts' to create them."})


@app.route("/api/build-video", methods=["POST"])
def run_build_video():
    proj_dir = get_active_proj_dir()
    proj_name = ACTIVE_PROJECT
    vertical = is_vertical_project()

    def worker():
        add_log(f"[{proj_name}] Starting Automated Video Assembly...", "info")
        cmd = [sys.executable, "build_video.py"]
        if vertical:
            cmd.append("--vertical")
        env_vars = {**os.environ, "PROJECT_DIR": proj_dir, "PYTHONUTF8": "1"}
        run_pipeline_script(cmd, env_vars, success_keywords=("DONE",))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": "Video assembly started."})

@app.route("/api/create-video", methods=["POST"])
def create_video():
    """Start the complete generation pipeline for the active project."""
    data = request.json or {}
    voice_id = data.get("voice_id", os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb"))
    force = bool(data.get("force", False))
    proj_dir = get_active_proj_dir()
    proj_name = ACTIVE_PROJECT

    if not any(fname.endswith(".txt") for fname in os.listdir(get_script_dir())):
        return jsonify({"error": "Hãy tạo hoặc dán kịch bản trước khi tạo video."}), 400

    def worker():
        run_video_pipeline(proj_dir, proj_name, voice_id, force, is_vertical_project())

    threading.Thread(target=worker, daemon=True).start()
    kind = "Short" if is_vertical_project() else "video thường"
    return jsonify({"message": f"Đã bắt đầu tạo {kind} theo toàn bộ quy trình. Theo dõi Console Log."})


@app.route("/api/create-video/retry", methods=["POST"])
def retry_video_pipeline():
    """Retry the failed stage and continue the remaining video stages."""
    if pipeline_state.get("running"):
        return jsonify({"error": "Pipeline đang chạy, chưa thể retry."}), 409
    failed_stage_index = pipeline_state.get("failed_stage_index")
    if failed_stage_index is None:
        return jsonify({"error": "Không có bước nào đang chờ retry."}), 400

    data = request.json or {}
    voice_id = data.get("voice_id", os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb"))
    proj_dir = get_active_proj_dir()
    proj_name = ACTIVE_PROJECT
    if pipeline_state.get("project") != proj_name:
        return jsonify({"error": "Project hiện tại không trùng với project bị lỗi."}), 409

    def worker():
        run_video_pipeline(proj_dir, proj_name, voice_id, False, is_vertical_project(), failed_stage_index)

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": f"Đang retry bước {pipeline_state['failed_stage']} và các bước tiếp theo."})


@app.route("/api/analytics", methods=["GET"])
def get_analytics():
    """Return a simple YouTube analytics comparison table for the active channel."""
    scope_list = default_analytics_scopes()
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        token_path = os.path.join(BASE_DIR, "token.json")
        if not os.path.exists(token_path):
            return jsonify({"videos": [], "issue_type": "no_token", "message": "No YouTube OAuth token yet. Re-authenticate in the upload flow."})

        creds = Credentials.from_authorized_user_file(token_path, scope_list)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                return jsonify({"videos": [], "issue_type": "reauth", "message": "Token invalid or expired. Re-authenticate with the new analytics scope."})

        if not has_required_scopes(creds):
            return jsonify({"videos": [], "issue_type": "reauth", "message": "Token is missing the required YouTube scopes. Please re-authenticate to grant yt-analytics.readonly and youtube.upload."})

        service = build("youtubeAnalytics", "v2", credentials=creds)
        videos = fetch_video_analytics(service, max_results=25, start_days=30)

        titles_by_id = {}
        durations_by_id = {}
        video_ids = [v["video_id"] for v in videos if v.get("video_id")]
        if video_ids:
            try:
                yt_service = build("youtube", "v3", credentials=creds)
                resp = yt_service.videos().list(part="snippet,contentDetails", id=",".join(video_ids)).execute()
                for item in resp.get("items", []):
                    titles_by_id[item["id"]] = item.get("snippet", {}).get("title", "")
                    raw_duration = item.get("contentDetails", {}).get("duration", "")
                    durations_by_id[item["id"]] = parse_iso8601_duration(raw_duration)
            except Exception:
                pass  # Titles/duration are a nice-to-have — show raw ids rather than fail the whole table.
        videos = attach_video_titles(videos, titles_by_id, durations_by_id)

        channel_stats = {}
        try:
            yt_service = build("youtube", "v3", credentials=creds)
            channel_resp = yt_service.channels().list(part="statistics,snippet", mine=True).execute()
            channel = (channel_resp.get("items") or [{}])[0]
            stats = channel.get("statistics", {}) or {}
            channel_stats = {
                "viewCount": stats.get("viewCount", 0),
                "subscriberCount": stats.get("subscriberCount", 0),
                "videoCount": stats.get("videoCount", len(videos)),
            }
        except Exception:
            channel_stats = {}

        summary = summarize_channel_overview(videos, channel_stats)

        return jsonify({"videos": videos, "summary": summary})
    except PermissionError as exc:
        return jsonify({
            "videos": [],
            "issue_type": getattr(exc, "issue_type", "reauth"),
            "message": str(exc),
            "enable_url": getattr(exc, "enable_url", ""),
        }), 403
    except Exception as exc:
        return jsonify({"videos": [], "issue_type": "error", "message": f"Analytics unavailable: {exc}"}), 500


@app.route("/api/youtube/auth/reset", methods=["POST"])
def reset_youtube_auth():
    """Delete expired/stale token evidence so the next login requests the new analytics scope."""
    token_path = os.path.join(BASE_DIR, "token.json")
    if os.path.exists(token_path):
        os.remove(token_path)
    return jsonify({"message": "YouTube OAuth token was reset. Re-authenticate to grant the new required scopes."})


@app.route("/api/youtube/auth/login", methods=["POST"])
def start_youtube_auth_login():
    """Start the browser-based Google OAuth flow using the standard local server callback flow."""
    def worker():
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            from youtube_analytics import default_analytics_scopes

            client_secret = os.path.join(BASE_DIR, "client_secret.json")
            if not os.path.exists(client_secret):
                add_log("[SYSTEM] OAuth failed: client_secret.json not found.", "error")
                return

            add_log("[SYSTEM] Starting YouTube OAuth re-authentication in the browser...", "info")
            flow = InstalledAppFlow.from_client_secrets_file(client_secret, default_analytics_scopes())
            creds = flow.run_local_server(port=0)

            with open(os.path.join(BASE_DIR, "token.json"), "w", encoding="utf-8") as f:
                f.write(creds.to_json())

            add_log("[SYSTEM] YouTube OAuth login completed successfully.", "success")
        except Exception as exc:
            add_log(f"[SYSTEM] YouTube OAuth login failed: {exc}", "error")

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({
        "message": "YouTube OAuth login flow started. Complete the Google authorization window that opens in your browser."
    })


@app.route("/api/youtube/auth/status", methods=["GET"])
def youtube_auth_status():
    """Return the connected Google account and current OAuth scope status."""
    token_path = os.path.join(BASE_DIR, "token.json")
    if not os.path.exists(token_path):
        return jsonify({
            "connected": False,
            "message": "No YouTube OAuth token yet. Re-authenticate to grant the required permissions.",
            "scopes": [],
            "account": None,
            "channel": None,
        })

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from youtube_analytics import default_analytics_scopes, has_required_scopes

        creds = Credentials.from_authorized_user_file(token_path, default_analytics_scopes())
        scopes = list(getattr(creds, "scopes", []) or [])
        payload = {
            "connected": bool(creds and getattr(creds, "valid", False)),
            "message": "YouTube OAuth is active.",
            "scopes": scopes,
            "account": None,
            "channel": None,
        }

        if not has_required_scopes(creds):
            payload["connected"] = False
            payload["message"] = "YouTube OAuth token is missing required scopes. Please re-authenticate."
            return jsonify(payload)

        try:
            oauth_service = build("oauth2", "v2", credentials=creds)
            userinfo = oauth_service.userinfo().get().execute()
            payload["account"] = userinfo.get("email") or userinfo.get("name")
        except Exception:
            payload["account"] = "Google account detected"

        try:
            yt_service = build("youtube", "v3", credentials=creds)
            channels = yt_service.channels().list(part="snippet", mine=True).execute()
            items = channels.get("items") or []
            if items:
                payload["channel"] = items[0].get("snippet", {}).get("title")
        except Exception:
            payload["channel"] = None

        return jsonify(payload)
    except Exception as exc:
        return jsonify({
            "connected": False,
            "message": str(exc),
            "scopes": [],
            "account": None,
            "channel": None,
        }), 500


@app.route("/api/packaging", methods=["GET"])
def get_packaging():
    path = get_packaging_path()
    if not os.path.exists(path):
        return jsonify({"packages": []})
    try:
        with open(path, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except (OSError, json.JSONDecodeError):
        return jsonify({"packages": []})


@app.route("/api/packaging/generate", methods=["POST"])
def generate_packaging():
    """Generate several title/thumbnail packages for the active project's script."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"error": "Chưa cấu hình GEMINI_API_KEY để tạo title/thumbnail."}), 400

    script_dir = get_script_dir()
    script_files = sorted(
        [f for f in os.listdir(script_dir) if f.endswith(".txt") and not f.endswith(".example.txt")],
        key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)],
    )
    if not script_files:
        return jsonify({"error": "Chưa có kịch bản để tạo title/thumbnail."}), 400

    script_blocks = []
    for filename in script_files:
        with open(os.path.join(script_dir, filename), "r", encoding="utf-8") as f:
            script_blocks.append(f"SECTION {filename}:\n{f.read().strip()}")
    script_text = "\n\n".join(script_blocks)
    prompt = f"""You are a YouTube packaging strategist for a psychology and technology explainer channel.
Create three genuinely different packaging options for the script below.
Optimize for click-through rate without misleading clickbait.
Each option must include:
- a concise YouTube title (preferably under 65 characters)
- the curiosity angle
- thumbnail text of 2 to 5 words, not a sentence and not repeating the title
- a detailed 16:9 thumbnail image prompt with one clear focal subject, strong contrast, and empty space for text; do not render any text in the image
- a one-sentence description opening

Return ONLY this JSON object:
{{"packages":[{{"title":"...","angle":"...","thumbnail_text":"...","image_prompt":"...","description_opening":"..."}}]}}

Script:
{script_text}"""

    try:
        result = _parse_json_object(call_gemini_api(prompt, api_key, "gemini-3.5-flash")) or {}
        packages = result.get("packages")
        if not isinstance(packages, list):
            raise ValueError("Gemini did not return packages")
        cleaned = []
        for package in packages[:3]:
            if not isinstance(package, dict):
                continue
            required = ("title", "angle", "thumbnail_text", "image_prompt", "description_opening")
            if all(isinstance(package.get(key), str) and package[key].strip() for key in required):
                cleaned.append({key: package[key].strip()[:1000] for key in required})
        if not cleaned:
            raise ValueError("Gemini returned no valid packaging options")
    except Exception as e:
        add_log(f"Packaging generation failed: {e}", "warning")
        return jsonify({"error": "Không tạo được bộ title/thumbnail hợp lệ."}), 502

    payload = {"packages": cleaned}
    with open(get_packaging_path(), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return jsonify(payload)


@app.route("/api/packaging/thumbnail", methods=["POST"])
def generate_packaging_thumbnail():
    data = request.json or {}
    raw_index = data.get("index")
    if not isinstance(raw_index, (int, str)):
        return jsonify({"error": "Thiếu số thứ tự concept thumbnail."}), 400
    try:
        package_index = int(raw_index)
    except (TypeError, ValueError):
        return jsonify({"error": "Thiếu số thứ tự concept thumbnail."}), 400

    path = get_packaging_path()
    if not os.path.exists(path):
        return jsonify({"error": "Hãy tạo bộ title/thumbnail trước."}), 400
    try:
        with open(path, "r", encoding="utf-8") as f:
            packages = json.load(f).get("packages", [])
        package = packages[package_index]
        prompt = package["image_prompt"]
    except (OSError, json.JSONDecodeError, IndexError, KeyError, TypeError):
        return jsonify({"error": "Concept thumbnail không hợp lệ."}), 400

    output_name = f"thumbnail_{package_index + 1:02d}.png"
    output_path = os.path.join(get_images_dir(), output_name)
    proj_name = ACTIVE_PROJECT

    def worker():
        add_log(f"[{proj_name}] Generating {output_name} with Imagen...", "info")
        try:
            image_bytes = generate_image_from_prompt(prompt, os.getenv("GEMINI_API_KEY", ""))
            with open(output_path, "wb") as f:
                f.write(image_bytes)
            add_log(f"[{proj_name}] Thumbnail ready: {output_name}", "success")
        except Exception as e:
            add_log(f"[{proj_name}] Thumbnail generation failed: {e}", "error")

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": f"Đang tạo {output_name} bằng Imagen.", "filename": output_name})


@app.route("/api/shorts/sections", methods=["GET"])
def list_shorts_sections():
    """Sections in the active project that already have generated audio (and
    therefore can be cut into a vertical Short reusing their existing footage)."""
    audio_dir = get_audio_dir()
    footage_dir = get_footage_dir()
    section_names = sorted(
        (os.path.splitext(f)[0] for f in os.listdir(audio_dir) if f.endswith(".mp3")),
        key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)],
    )
    sections = [
        {
            "name": name,
            "has_footage": os.path.exists(os.path.join(footage_dir, f"{name}.mp4"))
            or bool([f for f in os.listdir(footage_dir) if f.startswith(f"{name}_") and f.endswith(".mp4")]),
        }
        for name in section_names
    ]
    return jsonify({"sections": sections})


def _parse_json_object(raw_text: str):
    """Parse a JSON object even when the model wraps it in a markdown fence."""
    match = re.search(r'\{.*\}', raw_text or "", re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


@app.route("/api/shorts/recommend", methods=["POST"])
def recommend_short_sections():
    """Ask Gemini to select an ordered, compact hook/payoff subset for a Short."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"error": "Chưa cấu hình GEMINI_API_KEY để AI chọn section."}), 400

    audio_dir = get_audio_dir()
    script_dir = get_script_dir()
    available = sorted(
        (os.path.splitext(f)[0] for f in os.listdir(audio_dir) if f.endswith(".mp3")),
        key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)],
    )
    scripts = []
    for name in available:
        script_path = os.path.join(script_dir, f"{name}.txt")
        if os.path.exists(script_path):
            with open(script_path, "r", encoding="utf-8") as f:
                scripts.append({"name": name, "text": f.read().strip()})

    if not scripts:
        return jsonify({"error": "Chưa có section nào có cả audio và kịch bản."}), 400

    script_context = "\n\n".join(
        f"SECTION {item['name']}:\n{item['text']}" for item in scripts
    )
    prompt = f"""You are an expert short-form video editor.
Select the strongest consecutive-or-nonconsecutive sections from this existing narration to make one coherent YouTube Short.
Prioritize: an immediate hook, escalating curiosity or tension, and a satisfying payoff.
Do not rewrite the narration. Select 2 to 5 section names only from the allowed list.
Preserve the original narrative order. Prefer a total spoken duration of roughly 20 to 90 seconds.
Do not select a section just because it is early; select the strongest story arc.

Allowed sections and narration:
{script_context}

Respond with ONLY this JSON object:
{{"sections":["exact_section_name"],"reason":"brief explanation","suggested_title":"short title"}}"""

    try:
        raw = call_gemini_api(prompt, api_key, "gemini-3.5-flash")
        result = _parse_json_object(raw)
        selected = result.get("sections") if result else None
        allowed = set(available)
        if not isinstance(selected, list) or not selected:
            raise ValueError("Gemini did not return a section list")
        selected = [name for name in selected if isinstance(name, str) and name in allowed]
        selected = sorted(set(selected), key=available.index)
        if not selected:
            raise ValueError("Gemini returned no valid section names")
        result = result or {}
    except Exception as e:
        add_log(f"AI Short recommendation failed: {e}", "warning")
        return jsonify({"error": "AI không tạo được đề xuất hợp lệ. Bạn có thể chọn section thủ công."}), 502

    return jsonify({
        "sections": selected,
        "reason": str(result.get("reason", ""))[:500],
        "suggested_title": str(result.get("suggested_title", ""))[:120],
    })


@app.route("/api/shorts/create", methods=["POST"])
def create_short():
    data = request.json or {}
    sections = data.get("sections") or []
    raw_name = (data.get("name") or "").strip()
    if not sections:
        return jsonify({"error": "Chọn ít nhất 1 section để tạo Short."}), 400

    clean_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in raw_name).strip().replace(" ", "_")
    if not clean_name:
        clean_name = "short_" + "_".join(sections[:2])

    proj_dir = get_active_proj_dir()
    proj_name = ACTIVE_PROJECT
    shorts_dir = os.path.join(proj_dir, "video", "shorts")
    os.makedirs(shorts_dir, exist_ok=True)

    def worker():
        add_log(f"[{proj_name}] Cutting Short '{clean_name}' from {len(sections)} section(s)...", "info")
        cmd = [
            sys.executable, "build_video.py",
            "--vertical",
            "--sections", ",".join(sections),
            "--output-dir", shorts_dir,
            "--output-name", f"{clean_name}.mp4",
        ]
        env_vars = {**os.environ, "PROJECT_DIR": proj_dir, "PYTHONUTF8": "1"}
        run_pipeline_script(cmd, env_vars, success_keywords=("DONE",))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": f"Đang tạo Short '{clean_name}'...", "filename": f"{clean_name}.mp4"})


@app.route("/api/shorts/list", methods=["GET"])
def list_shorts():
    shorts_dir = os.path.join(get_active_proj_dir(), "video", "shorts")
    if not os.path.exists(shorts_dir):
        return jsonify({"shorts": []})
    files = sorted(f for f in os.listdir(shorts_dir) if f.endswith(".mp4"))
    return jsonify({"shorts": files})


@app.route("/media/shorts/<filename>")
def serve_short(filename):
    shorts_dir = os.path.join(get_active_proj_dir(), "video", "shorts")
    return send_from_directory(shorts_dir, filename)


@app.route("/api/upload-youtube", methods=["POST"])
def run_upload_youtube():
    data = request.json or {}
    title = data.get("title", "Why You Can't Stop Scrolling | The Hidden Why")
    description = data.get("description", "")
    privacy = data.get("privacy", "private")
    is_short = bool(data.get("is_short"))
    proj_name = ACTIVE_PROJECT

    if is_short:
        # basename() strips any path components the client might send, so this
        # can only ever resolve to a file directly inside this project's shorts dir.
        short_filename = os.path.basename(data.get("video", "").strip())
        if not short_filename:
            return jsonify({"error": "Thiếu tên file Short cần upload."}), 400
        video_path = os.path.join(get_video_dir(), "shorts", short_filename)
    else:
        video_path = os.path.join(get_video_dir(), "final_video.mp4")

    if not os.path.exists(video_path):
        return jsonify({"error": f"Video file for project [{proj_name}] not found. Please assemble video first."}), 400

    def worker():
        add_log(f"[{proj_name}] Starting YouTube Upload (Privacy: {privacy.upper()}"
                 f"{', SHORT' if is_short else ''})...", "info")
        cmd = [sys.executable, "upload_youtube.py", "--video", video_path, "--title", title, "--privacy", privacy]
        if description:
            cmd.extend(["--description", description])
        if is_short:
            cmd.append("--shorts")
        run_pipeline_script(cmd, success_keywords=("SUCCESS", "Completed"))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": "YouTube upload process started."})


@app.route("/api/upload-image/<section_name>", methods=["POST"])
def upload_image_section(section_name):
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    ext = os.path.splitext(file.filename)[1] or ".png"
    target_filename = f"{section_name}{ext}"
    target_path = os.path.join(get_images_dir(), target_filename)
    file.save(target_path)
    add_log(f"Uploaded custom image for {section_name}: {target_filename}", "success")
    return jsonify({"message": f"Uploaded {target_filename}"})


@app.route("/media/audio/<filename>")
def serve_audio(filename):
    return send_from_directory(get_audio_dir(), filename)


@app.route("/media/video/<filename>")
def serve_video(filename):
    return send_from_directory(get_video_dir(), filename)


if __name__ == "__main__":
    print("=" * 65)
    print(" 🎬 The Hidden Why — Pipeline Control Center UI (Multi-Project)")
    print(" Access URL: http://localhost:5000")
    print("=" * 65)
    app.run(host="0.0.0.0", port=5000, debug=False)

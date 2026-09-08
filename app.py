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


def is_vertical_project() -> bool:
    """Whether the active project was created as a YouTube Shorts (vertical) project."""
    return os.path.exists(os.path.join(get_active_proj_dir(), ".vertical"))


# Global process logger for UI output logs
logs_lock = threading.Lock()
activity_logs = []


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
        paragraphs = [p.strip() for p in full_text.split("\n") if p.strip()]
        current_chunk = []
        current_len = 0
        part_idx = 0

        for p in paragraphs:
            current_chunk.append(p)
            current_len += len(p)
            if current_len >= 350:
                slug = f"part{part_idx}_cold_open" if part_idx == 0 else f"part{part_idx}_section"
                sections.append((slug, "\n\n".join(current_chunk)))
                current_chunk = []
                current_len = 0
                part_idx += 1

        if current_chunk:
            slug = f"part{part_idx}_cold_open" if part_idx == 0 else f"part{part_idx}_section"
            sections.append((slug, "\n\n".join(current_chunk)))

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

    add_log(f"⚡ Smart Script Auto-Splitter: Created {len(created_files)} section(s) for {ACTIVE_PROJECT}!", "success")
    return jsonify({
        "message": f"Đã tự động chia kịch bản thành {len(created_files)} phần (section) cho dự án [{ACTIVE_PROJECT}]!",
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
    video_path = os.path.join(get_video_dir(), "final_video.mp4")
    proj_name = ACTIVE_PROJECT

    if not os.path.exists(video_path):
        return jsonify({"error": f"Video file for project [{proj_name}] not found. Please assemble video first."}), 400

    def worker():
        add_log(f"[{proj_name}] Starting YouTube Upload (Privacy: {privacy.upper()})...", "info")
        cmd = [sys.executable, "upload_youtube.py", "--video", video_path, "--title", title, "--privacy", privacy]
        if description:
            cmd.extend(["--description", description])
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

#!/usr/bin/env python3
"""
The Hidden Why — Local Web Control Panel (UI)
Flask-backed Web Application providing a sleek, modern GUI for the YouTube Automation Pipeline.
"""

import os
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
SCRIPT_DIR = os.path.join(BASE_DIR, "script")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
VIDEO_DIR = os.path.join(BASE_DIR, "video")

for d in [SCRIPT_DIR, AUDIO_DIR, IMAGES_DIR, VIDEO_DIR]:
    os.makedirs(d, exist_ok=True)

# Global process logger for UI output logs
logs_lock = threading.Lock()
activity_logs = []

def add_log(msg, log_type="info"):
    with logs_lock:
        activity_logs.append({"message": msg, "type": log_type})
        if len(activity_logs) > 300:
            activity_logs.pop(0)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def get_status():
    eleven_key = bool(os.getenv("ELEVENLABS_API_KEY"))
    gemini_key = bool(os.getenv("GEMINI_API_KEY"))
    youtube_secret = os.path.exists(os.path.join(BASE_DIR, "client_secret.json")) or os.path.exists(os.path.join(BASE_DIR, "client_secret.json.json"))
    youtube_token = os.path.exists(os.path.join(BASE_DIR, "token.json"))

    scripts = [f for f in os.listdir(SCRIPT_DIR) if f.endswith(".txt") and not f.endswith(".example.txt")]
    audios = [f for f in os.listdir(AUDIO_DIR) if f.endswith(".mp3")]
    images = [f for f in os.listdir(IMAGES_DIR) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
    videos = [f for f in os.listdir(VIDEO_DIR) if f.endswith(".mp4")]

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
        "logs": activity_logs[-30:]
    })

@app.route("/api/scripts", methods=["GET"])
def list_scripts():
    scripts_data = []
    files = [f for f in os.listdir(SCRIPT_DIR) if f.endswith(".txt") and not f.endswith(".example.txt")]
    files.sort()
    for fname in files:
        fpath = os.path.join(SCRIPT_DIR, fname)
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
    fpath = os.path.join(SCRIPT_DIR, filename)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)
    add_log(f"Saved script file: {filename}", "success")
    return jsonify({"message": f"Saved {filename}"})

@app.route("/api/scripts/delete", methods=["POST"])
def delete_script():
    data = request.json
    filename = data.get("filename")
    fpath = os.path.join(SCRIPT_DIR, filename)
    if os.path.exists(fpath):
        os.remove(fpath)
        add_log(f"Deleted script file: {filename}", "warning")
    return jsonify({"message": "Deleted"})

@app.route("/api/generate-audio", methods=["POST"])
def run_generate_audio():
    data = request.json or {}
    force = data.get("force", False)
    voice_id = data.get("voice_id", os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb"))

    def worker():
        add_log("Starting ElevenLabs Voiceover Generation...", "info")
        cmd = [sys.executable, "generate_audio.py", "--voice-id", voice_id]
        if force:
            cmd.append("--force")
        proc = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            line_str = line.strip()
            if line_str:
                log_type = "error" if "ERROR" in line_str or "FAILED" in line_str else "info"
                if "DONE!" in line_str:
                    log_type = "success"
                add_log(line_str, log_type)
        proc.wait()
        add_log("Voiceover Generation process finished.", "success")

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": "Voice generation started in background."})

@app.route("/api/generate-prompts", methods=["POST"])
def run_generate_prompts():
    def worker():
        add_log("Starting Gemini Visual Prompt Generator...", "info")
        cmd = [sys.executable, "generate_prompts_gemini.py"]
        proc = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, env={**os.environ, "PYTHONUTF8": "1"})
        for line in proc.stdout:
            line_str = line.strip()
            if line_str:
                log_type = "error" if "ERROR" in line_str else ("success" if "SUCCESS" in line_str else "info")
                add_log(line_str, log_type)
        proc.wait()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": "Gemini Prompt generation started."})


@app.route("/api/generate-images", methods=["POST"])
def run_generate_images():
    def worker():
        add_log("Starting Gemini Imagen Image Generator...", "info")
        cmd = [sys.executable, "generate_images_gemini.py"]
        proc = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, env={**os.environ, "PYTHONUTF8": "1"})
        for line in proc.stdout:
            line_str = line.strip()
            if line_str:
                log_type = "error" if "ERROR" in line_str else ("success" if "SUCCESS" in line_str or "DONE" in line_str else "info")
                add_log(line_str, log_type)
        proc.wait()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": "Image generation started."})


@app.route("/api/generate-footage", methods=["POST"])
def run_generate_footage():
    data = request.json or {}
    force = data.get("force", False)

    def worker():
        add_log("Starting Veo 2 B-roll Footage Generator...", "info")
        cmd = [sys.executable, "generate_footage_veo.py"]
        if force:
            cmd.append("--force")
        proc = subprocess.Popen(
            cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env={**os.environ, "PYTHONUTF8": "1"}
        )
        for line in proc.stdout:
            line_str = line.strip()
            if line_str:
                log_type = "error" if "ERROR" in line_str else ("success" if "DONE" in line_str else "info")
                add_log(line_str, log_type)
        proc.wait()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": "Veo 2 footage generation started. Each clip takes ~1-3 minutes — watch the console log."})

@app.route("/api/prompts", methods=["GET"])
def get_prompts():
    path = os.path.join(BASE_DIR, "visual_prompts_gemini.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return jsonify({"content": f.read()})
    return jsonify({"content": "No generated prompts found yet. Click 'Generate Prompts' to create them."})

@app.route("/api/build-video", methods=["POST"])
def run_build_video():
    def worker():
        add_log("Starting Automated Video Assembly...", "info")
        cmd = [sys.executable, "build_video.py"]
        proc = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, env={**os.environ, "PYTHONUTF8": "1"})
        for line in proc.stdout:
            line_str = line.strip()
            if line_str:
                log_type = "error" if "ERROR" in line_str else ("success" if "DONE" in line_str else "info")
                add_log(line_str, log_type)
        proc.wait()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": "Video assembly started."})

@app.route("/api/upload-youtube", methods=["POST"])
def run_upload_youtube():
    data = request.json or {}
    title = data.get("title", "Why You Can't Stop Scrolling | The Hidden Why")
    description = data.get("description", "")
    privacy = data.get("privacy", "private")
    video_path = os.path.join(VIDEO_DIR, "final_video.mp4")

    if not os.path.exists(video_path):
        return jsonify({"error": "Video file ./video/final_video.mp4 not found. Please assemble video first."}), 400

    def worker():
        add_log(f"Starting YouTube Upload (Privacy: {privacy.upper()})...", "info")
        cmd = [sys.executable, "upload_youtube.py", "--video", video_path, "--title", title, "--privacy", privacy]
        if description:
            cmd.extend(["--description", description])
        proc = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            line_str = line.strip()
            if line_str:
                log_type = "error" if "ERROR" in line_str else ("success" if "SUCCESS" in line_str or "Completed" in line_str else "info")
                add_log(line_str, log_type)
        proc.wait()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"message": "YouTube upload process started."})

@app.route("/api/upload-image/<section_name>", methods=["POST"])
def upload_image_section(section_name):
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    ext = os.path.splitext(file.filename)[1]
    if not ext:
        ext = ".png"
    target_filename = f"{section_name}{ext}"
    target_path = os.path.join(IMAGES_DIR, target_filename)
    file.save(target_path)
    add_log(f"Uploaded custom image for {section_name}: {target_filename}", "success")
    return jsonify({"message": f"Uploaded {target_filename}"})

@app.route("/media/audio/<filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)

@app.route("/media/video/<filename>")
def serve_video(filename):
    return send_from_directory(VIDEO_DIR, filename)

if __name__ == "__main__":
    print("=" * 65)
    print(" 🎬 The Hidden Why — Pipeline Control Center UI")
    print(" Access URL: http://localhost:5000")
    print("=" * 65)
    app.run(host="0.0.0.0", port=5000, debug=False)

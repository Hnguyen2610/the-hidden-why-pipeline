# 🎬 The Hidden Why — YouTube Automation Pipeline

A minimal, local Python automation pipeline for **"The Hidden Why"** YouTube channel (psychology & tech explainer videos).

## 📁 Repository Structure

```text
d:\the-hidden-why-pipeline\
├── app.py                     # Studio Web UI (http://localhost:5000)
├── generate_audio.py          # Voice generation per section
├── generate_prompts_gemini.py # Automated visual prompt generator (Gemini)
├── generate_images_gemini.py  # Imagen API visual image generator
├── generate_footage_veo.py    # Veo 3 / Veo 2 B-roll video generator
├── build_video.py             # Automated FFmpeg video assembler
├── upload_youtube.py          # Separate YouTube Data API v3 upload (Private by default)
├── script/                    # Section script .txt files (part0_cold_open.txt, etc.)
├── audio/                     # Generated audio .mp3 files
├── images/                    # Section image files (part0_cold_open.png, etc.)
├── footage/                   # B-roll video clips (.mp4) from Veo 3
└── video/                     # Output assembled final_video.mp4

```

---

## 🚀 Phase 1 — ElevenLabs Voice Generation

1. **Install requirements:**
   ```bash
   pip install -r requirements.txt
   ```
2. **Setup secrets (`.env`):**
   ```env
   ELEVENLABS_API_KEY=your_api_key_here
   ELEVENLABS_VOICE_ID=JBFqnCBsd6RMkjVDRZzb  # George (or use python list_voices.py)
   ```
3. **Run voice generation:**
   ```bash
   python generate_audio.py
   ```
   *To force regeneration of existing files:* `python generate_audio.py --force`

---

## 🎥 Phase 2 — YouTube Video Upload (Private-by-Default)

1. **Get OAuth2 Client Secret:**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Enable **YouTube Data API v3**
   - Create an **OAuth 2.0 Client ID** (Type: *Desktop Application*)
   - Download the JSON file and save it as `client_secret.json` in this folder.

2. **Upload Video:**
   ```bash
   python upload_youtube.py --video ./video/final_video.mp4 --title "Why You Can't Stop Scrolling | The Hidden Why"
   ```
   - **Privacy Notice:** Defaults strictly to `PRIVATE`. Never auto-publishes publicly until you review and publish manually via YouTube Studio.
   - **Authentication:** First run opens a browser pop-up to log in once. Credentials are saved to `token.json` for subsequent runs.

---

## 🎨 Phase 3 — Visual Prompts Generator (Automated with Gemini API)

Instead of manually writing prompt descriptions, you can let Gemini analyze your script sections and automatically write cinematic image prompts for Midjourney/Imagen:

1. **Add `GEMINI_API_KEY` to `.env`:**
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   ```
2. **Run automatic prompt generator:**
   ```bash
   python generate_prompts_gemini.py
   ```

This creates `visual_prompts_gemini.txt` containing 2-3 cinematic prompts per script section, tailored specifically for "The Hidden Why" aesthetic.


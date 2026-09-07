let currentScriptFilename = "";

document.addEventListener("DOMContentLoaded", () => {
    setupTabs();
    refreshStatus();
    loadScripts();
    loadPrompts();

    // Auto-refresh status and log stream every 2.5s
    setInterval(refreshStatus, 2500);
});

function setupTabs() {
    const navItems = document.querySelectorAll(".nav-item");
    navItems.forEach(item => {
        item.addEventListener("click", () => {
            const tabName = item.getAttribute("data-tab");
            switchTab(tabName);
        });
    });
}

function switchTab(tabName) {
    document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(el => el.classList.remove("active"));

    const targetBtn = document.querySelector(`.nav-item[data-tab="${tabName}"]`);
    const targetTab = document.getElementById(`tab-${tabName}`);

    if (targetBtn) targetBtn.classList.add("active");
    if (targetTab) targetTab.classList.add("active");

    const pageTitle = document.getElementById("page-title");
    const titles = {
        dashboard: "Dashboard Tổng Quan",
        scripts: "Quản Lý Kịch Bản (Script Editor)",
        voice: "Phase 1: Tạo Giọng Đọc (ElevenLabs)",
        prompts: "Phase 2: Visuals & Prompts AI (Gemini & Veo 3)",
        video: "Phase 3: Ghép Video (FFmpeg Assembler)",
        youtube: "Phase 4: Upload YouTube (OAuth2 Private)"
    };
    if (pageTitle && titles[tabName]) {
        pageTitle.textContent = titles[tabName];
    }

    if (tabName === "scripts") loadScripts();
    if (tabName === "prompts") loadPrompts();
}

async function refreshStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();

        // Badges
        setBadge("badge-eleven", data.elevenlabs_key, "Đã Cấu Hình", "Chưa Cấu Hình");
        setBadge("badge-gemini", data.gemini_key, "Đã Cấu Hình", "Chưa Cấu Hình");
        setBadge("badge-youtube", data.youtube_secret, "OAuth Ready", "Chưa Lấy Secret");

        // Metrics
        document.getElementById("val-scripts").textContent = data.script_count;
        document.getElementById("val-audios").textContent = data.audio_count;
        document.getElementById("val-images").textContent = data.image_count;
        document.getElementById("val-videos").textContent = data.video_count;

        // Logs
        if (data.logs) {
            renderLogs(data.logs);
        }
    } catch (err) {
        console.error("Status fetch error:", err);
    }
}

setBadge = (elementId, isOk, okText, failText) => {
    const el = document.getElementById(elementId);
    if (!el) return;
    const span = el.querySelector("span");
    if (isOk) {
        span.textContent = okText;
        span.className = "ok";
    } else {
        span.textContent = failText;
        span.className = "no";
    }
};

function renderLogs(logs) {
    const container = document.getElementById("console-logs");
    if (!container) return;

    const isScrolledToBottom = container.scrollHeight - container.clientHeight <= container.scrollTop + 20;

    container.innerHTML = "";
    logs.forEach(log => {
        const div = document.createElement("div");
        div.className = `log-line ${log.type || "info"}`;
        div.textContent = log.message;
        container.appendChild(div);
    });

    if (isScrolledToBottom) {
        container.scrollTop = container.scrollHeight;
    }
}

function clearLogs() {
    document.getElementById("console-logs").innerHTML = "";
}

/* SCRIPTS MANAGEMENT */
async function loadScripts() {
    try {
        const res = await fetch("/api/scripts");
        const data = await res.json();

        const fileList = document.getElementById("script-file-list");
        fileList.innerHTML = "";

        const audioGrid = document.getElementById("audio-list-grid");
        if (audioGrid) audioGrid.innerHTML = "";

        if (!data.scripts || data.scripts.length === 0) {
            fileList.innerHTML = '<div style="padding:10px; color:#64748B;">Chưa có file kịch bản.</div>';
            return;
        }

        data.scripts.forEach((item, idx) => {
            // Sidebar list
            const div = document.createElement("div");
            div.className = `script-item ${item.filename === currentScriptFilename ? "active" : ""}`;
            div.onclick = () => selectScript(item.filename, item.content);
            div.innerHTML = `
        <span>📄 ${item.filename}</span>
        <span style="color:#64748B;">${item.char_count} chars</span>
      `;
            fileList.appendChild(div);

            // Audio grid items for Voice tab
            if (audioGrid) {
                const audioBase = item.filename.replace(".txt", ".mp3");
                const audioDiv = document.createElement("div");
                audioDiv.className = "audio-item-card";
                audioDiv.innerHTML = `
          <h4>🔊 ${audioBase}</h4>
          <audio controls preload="none" src="/media/audio/${audioBase}"></audio>
        `;
                audioGrid.appendChild(audioDiv);
            }

            // Auto select first file if none selected
            if (idx === 0 && !currentScriptFilename) {
                selectScript(item.filename, item.content);
            }
        });
    } catch (err) {
        console.error("Load scripts error:", err);
    }
}

function selectScript(filename, content) {
    currentScriptFilename = filename;
    document.getElementById("editor-filename").value = filename;
    document.getElementById("editor-content").value = content;
    loadScripts();
}

async function saveCurrentScript() {
    const filename = document.getElementById("editor-filename").value;
    const content = document.getElementById("editor-content").value;

    if (!filename) {
        alert("Vui lòng nhập tên file kịch bản!");
        return;
    }

    try {
        const res = await fetch("/api/scripts/save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filename, content })
        });
        const data = await res.json();
        alert(data.message);
        loadScripts();
    } catch (err) {
        alert("Lưu thất bại: " + err);
    }
}

function addNewScriptPrompt() {
    const name = prompt("Nhập tên file kịch bản mới (e.g. part6_next.txt):");
    if (name) {
        selectScript(name, "");
    }
}

/* ACTIONS */
async function triggerGenerateAudio() {
    const voiceId = document.getElementById("select-voice").value;
    const force = document.getElementById("check-force").checked;

    try {
        const res = await fetch("/api/generate-audio", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ voice_id: voiceId, force })
        });
        const data = await res.json();
        alert("🚀 " + data.message + "\nHãy theo dõi log ở góc dưới màn hình.");
    } catch (err) {
        alert("Lỗi: " + err);
    }
}

async function triggerGeneratePrompts() {
    try {
        const res = await fetch("/api/generate-prompts", { method: "POST" });
        const data = await res.json();
        alert("🤖 " + data.message + "\nHãy theo dõi log ở góc dưới màn hình.");
    } catch (err) {
        alert("Lỗi: " + err);
    }
}

async function triggerGenerateImages() {
    try {
        const res = await fetch("/api/generate-images", { method: "POST" });
        const data = await res.json();
        alert("🖼️ " + data.message + "\nHãy theo dõi log ở góc dưới màn hình.");
    } catch (err) {
        alert("Lỗi: " + err);
    }
}

async function loadPrompts() {
    try {
        const res = await fetch("/api/prompts");
        const data = await res.json();
        const area = document.getElementById("prompts-output-area");
        if (area) area.textContent = data.content;
    } catch (err) {
        console.error("Load prompts error:", err);
    }
}

function copyPromptsToClipboard() {
    const text = document.getElementById("prompts-output-area").textContent;
    navigator.clipboard.writeText(text);
    alert("Đã copy toàn bộ Prompts vào Clipboard!");
}

async function triggerGenerateFootage(force = false) {
    try {
        const res = await fetch("/api/generate-footage", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ force })
        });
        const data = await res.json();
        alert("🎥 " + data.message);
    } catch (err) {
        alert("Lỗi: " + err);
    }
}

async function triggerBuildVideo() {
    try {
        const res = await fetch("/api/build-video", { method: "POST" });
        const data = await res.json();
        alert("🎬 " + data.message + "\nHãy theo dõi log ở góc dưới màn hình.");
    } catch (err) {
        alert("Lỗi: " + err);
    }
}

async function triggerUploadYoutube() {
    const title = document.getElementById("yt-title").value;
    const description = document.getElementById("yt-desc").value;
    const privacy = document.getElementById("yt-privacy").value;

    try {
        const res = await fetch("/api/upload-youtube", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title, description, privacy })
        });
        const data = await res.json();
        if (res.ok) {
            alert("🚀 " + data.message + "\nHãy theo dõi log ở góc dưới màn hình.");
        } else {
            alert("Lỗi: " + data.error);
        }
    } catch (err) {
        alert("Lỗi: " + err);
    }
}

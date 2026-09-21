let currentScriptFilename = "";

async function readApiResponse(response) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
        return await response.json();
    }

    const text = await response.text();
    throw new Error(`Server trả về HTTP ${response.status} thay vì JSON${text ? `: ${text.slice(0, 120)}` : ""}`);
}

document.addEventListener("DOMContentLoaded", () => {
    setupTabs();
    setupConsoleResize();
    loadProjects();
    refreshStatus();
    loadScripts();
    loadPrompts();

    // Auto-refresh status and log stream every 2.5s
    setInterval(refreshStatus, 2500);
});

function setupConsoleResize() {
    const drawer = document.getElementById("console-drawer");
    const handle = document.getElementById("console-resize-handle");
    if (!drawer || !handle) return;

    try {
        const savedHeight = localStorage.getItem("consoleDrawerHeight");
        if (savedHeight) drawer.style.height = savedHeight + "px";
    } catch (err) { /* localStorage unavailable, ignore */ }

    let dragging = false;
    let startY = 0;
    let startHeight = 0;

    handle.addEventListener("mousedown", (e) => {
        dragging = true;
        startY = e.clientY;
        startHeight = drawer.getBoundingClientRect().height;
        drawer.classList.add("resizing");
        document.body.style.userSelect = "none";
        e.preventDefault();
    });

    document.addEventListener("mousemove", (e) => {
        if (!dragging) return;
        const delta = startY - e.clientY;
        const newHeight = Math.min(Math.max(startHeight + delta, 40), window.innerHeight * 0.8);
        drawer.style.height = newHeight + "px";
    });

    document.addEventListener("mouseup", () => {
        if (!dragging) return;
        dragging = false;
        drawer.classList.remove("resizing");
        document.body.style.userSelect = "";
        try {
            localStorage.setItem("consoleDrawerHeight", Math.round(drawer.getBoundingClientRect().height));
        } catch (err) { /* localStorage unavailable, ignore */ }
    });
}

async function loadProjects() {
    try {
        const res = await fetch("/api/projects");
        const data = await res.json();

        const selectEl = document.getElementById("select-project");
        if (!selectEl) return;

        selectEl.innerHTML = "";
        data.projects.forEach(p => {
            const opt = document.createElement("option");
            opt.value = p;
            opt.textContent = p.replace(/_/g, " ");
            if (p === data.active_project) {
                opt.selected = true;
            }
            selectEl.appendChild(opt);
        });
    } catch (err) {
        console.error("Error loading projects:", err);
    }
}

async function switchProject(projectName) {
    try {
        const res = await fetch("/api/projects/switch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: projectName })
        });
        const data = await res.json();
        if (res.ok) {
            clearScriptEditor();
            refreshStatus();
            loadScripts();
            loadPrompts();
        } else {
            alert("Lỗi đổi dự án: " + data.error);
        }
    } catch (err) {
        alert("Lỗi: " + err);
    }
}

function clearScriptEditor() {
    currentScriptFilename = "";
    const filenameEl = document.getElementById("editor-filename");
    const contentEl = document.getElementById("editor-content");
    if (filenameEl) filenameEl.value = "";
    if (contentEl) contentEl.value = "";
}

async function createNewProjectPrompt() {
    const modal = document.getElementById("new-project-modal");
    const nameInput = document.getElementById("new-project-name");
    if (!modal || !nameInput) return;

    nameInput.value = "";
    modal.classList.add("open");
    nameInput.focus();

    const selection = await new Promise(resolve => {
        modal.querySelectorAll("[data-format]").forEach(button => {
            button.onclick = () => {
                const name = nameInput.value.trim();
                if (!name) {
                    nameInput.focus();
                    return;
                }
                modal.classList.remove("open");
                resolve({ name, vertical: button.dataset.format === "short" });
            };
        });
        modal.querySelector("[data-close]").onclick = () => {
            modal.classList.remove("open");
            resolve(null);
        };
    });

    if (!selection) return;

    try {
        const res = await fetch("/api/projects/create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: selection.name, vertical: selection.vertical })
        });
        const data = await readApiResponse(res);
        if (res.ok) {
            alert("🎉 " + data.message);
            clearScriptEditor();
            try {
                await loadProjects();
                await refreshStatus();
                await loadScripts();
                await loadPrompts();
            } catch (refreshError) {
                console.error("Project created, but refresh failed:", refreshError);
            }
        } else {
            alert("Lỗi: " + data.error);
        }
    } catch (err) {
        alert("Lỗi tạo project: " + err.message);
    }
}

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
        voice: "Tạo Video",
        prompts: "Tạo Video",
        video: "Tạo Video",
        shorts: "Tạo Video Short",
        youtube: "Upload YouTube (OAuth2 Private)"
    };
    if (pageTitle && titles[tabName]) {
        pageTitle.textContent = titles[tabName];
    }

    if (tabName === "scripts") loadScripts();
    if (tabName === "prompts") loadPrompts();
    if (tabName === "shorts") {
        loadShortsSections();
        loadShortsList();
    }
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
        const failedStage = data.pipeline && data.pipeline.failed_stage;
        document.querySelectorAll(".pipeline-retry").forEach(retryButton => {
            retryButton.hidden = !failedStage || data.pipeline.running;
            if (failedStage) retryButton.textContent = `↻ Retry ${failedStage}`;
        });
    } catch (err) {
        console.error("Status fetch error:", err);
    }
}

async function retryFailedStage() {
    try {
        const res = await fetch("/api/create-video/retry", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ voice_id: document.getElementById("select-voice")?.value })
        });
        const data = await readApiResponse(res);
        if (!res.ok) {
            alert("Lỗi retry: " + data.error);
            return;
        }
        alert("↻ " + data.message);
    } catch (err) {
        alert("Lỗi retry: " + err.message);
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

async function triggerAutoSplitScript() {
    const fullScript = document.getElementById("full-script-input").value;
    if (!fullScript || !fullScript.trim()) {
        alert("Vui lòng dán kịch bản video mới của bạn vào ô văn bản!");
        return;
    }

    if (!confirm("Hành động này sẽ xóa dữ liệu video cũ để khởi tạo video mới. Bạn có chắc chắn muốn tiếp tục?")) {
        return;
    }

    try {
        const res = await fetch("/api/scripts/split-and-save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ full_script: fullScript })
        });
        const data = await res.json();
        if (res.ok) {
            alert("✨ " + data.message);
            document.getElementById("full-script-input").value = "";
            clearScriptEditor();
            loadScripts();
        } else {
            alert("Lỗi: " + data.error);
        }
    } catch (err) {
        alert("Lỗi: " + err);
    }
}

async function triggerCreateVideo() {
    const fullScript = document.getElementById("full-script-input").value;
    if (!fullScript || !fullScript.trim()) {
        alert("Vui lòng dán kịch bản video mới của bạn vào ô văn bản!");
        return;
    }

    if (!confirm("Hệ thống sẽ tạo audio, visual, footage và ghép video theo loại project hiện tại. Tiếp tục?")) {
        return;
    }

    try {
        const splitRes = await fetch("/api/scripts/split-and-save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ full_script: fullScript })
        });
        const splitData = await splitRes.json();
        if (!splitRes.ok) {
            alert("Lỗi khởi tạo kịch bản: " + splitData.error);
            return;
        }

        const createRes = await fetch("/api/create-video", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                voice_id: document.getElementById("select-voice").value,
                force: document.getElementById("check-force").checked
            })
        });
        const createData = await createRes.json();
        if (createRes.ok) {
            alert("🎬 " + createData.message);
            document.getElementById("full-script-input").value = "";
            clearScriptEditor();
            loadScripts();
        } else {
            alert("Lỗi tạo video: " + createData.error);
        }
    } catch (err) {
        alert("Lỗi: " + err);
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

/* SHORTS (VERTICAL) */
async function loadShortsSections() {
    const container = document.getElementById("shorts-section-list");
    if (!container) return;
    try {
        const res = await fetch("/api/shorts/sections");
        const data = await res.json();
        container.innerHTML = "";
        if (!data.sections || data.sections.length === 0) {
            container.innerHTML = '<span style="color:#64748b;">Chưa có section nào có audio. Hãy tạo video trước.</span>';
            return;
        }
        data.sections.forEach(sec => {
            const row = document.createElement("label");
            row.style.cssText = "display:flex; align-items:center; gap:8px; cursor:pointer; padding:4px 0;";
            const footageTag = sec.has_footage
                ? '<span style="color:#10b981; font-size:11px;">● có footage</span>'
                : '<span style="color:#f59e0b; font-size:11px;">● chỉ có ảnh/poster</span>';
            row.innerHTML = `
                <input type="checkbox" value="${sec.name}" class="short-section-checkbox">
                <span style="flex:1;">${sec.name}</span>
                ${footageTag}
            `;
            container.appendChild(row);
        });
    } catch (err) {
        container.innerHTML = '<span style="color:#f87171;">Lỗi tải danh sách section.</span>';
    }
}

async function triggerCreateShort() {
    const checked = Array.from(document.querySelectorAll(".short-section-checkbox:checked")).map(el => el.value);
    if (checked.length === 0) {
        alert("Vui lòng chọn ít nhất 1 section!");
        return;
    }
    const name = document.getElementById("short-name-input").value.trim();

    try {
        const res = await fetch("/api/shorts/create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sections: checked, name })
        });
        const data = await res.json();
        if (res.ok) {
            alert("✂️ " + data.message + "\nTheo dõi log ở góc dưới màn hình, sẽ mất khoảng 1-2 phút.");
            document.getElementById("short-name-input").value = "";
        } else {
            alert("Lỗi: " + data.error);
        }
    } catch (err) {
        alert("Lỗi: " + err);
    }
}

async function loadShortsList() {
    const container = document.getElementById("shorts-list-container");
    if (!container) return;
    try {
        const res = await fetch("/api/shorts/list");
        const data = await res.json();
        if (!data.shorts || data.shorts.length === 0) {
            container.innerHTML = '<span style="color:#64748b;">Chưa có Short nào.</span>';
            return;
        }
        container.innerHTML = "";
        data.shorts.forEach(filename => {
            const item = document.createElement("div");
            item.style.cssText = "display:flex; align-items:center; gap:14px; background:#0c1220; border:1px solid #1e3a5f; border-radius:8px; padding:10px;";
            item.innerHTML = `
                <video src="/media/shorts/${filename}" controls style="width:120px; height:213px; border-radius:6px; background:#000; object-fit:cover;"></video>
                <span style="font-weight:600; flex:1;">${filename}</span>
                <button class="btn btn-danger" onclick="triggerUploadShort('${filename}')">🚀 Upload Short</button>
            `;
            container.appendChild(item);
        });
    } catch (err) {
        container.innerHTML = '<span style="color:#f87171;">Lỗi tải danh sách Shorts.</span>';
    }
}

async function triggerUploadShort(filename) {
    const suggestedTitle = filename.replace(/\.mp4$/i, "").replace(/_/g, " ") + " #Shorts";
    const title = prompt("Tiêu đề cho Short này:", suggestedTitle);
    if (title === null) return;

    try {
        const res = await fetch("/api/upload-youtube", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title, privacy: "private", video: filename, is_short: true })
        });
        const data = await res.json();
        if (res.ok) {
            alert("🚀 " + data.message + "\nTheo dõi log ở góc dưới màn hình. Mặc định upload ở chế độ PRIVATE an toàn.");
        } else {
            alert("Lỗi: " + data.error);
        }
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

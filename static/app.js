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
    // Registered first and unconditionally: a synchronous throw from any one
    // init call below must never again be able to prevent this from running
    // (that's exactly what an undefined loadChannelOverview() used to do —
    // it threw before this line and silently froze all live status/log
    // updates for the rest of the session).
    setInterval(refreshStatus, 2500);

    try {
        setupTabs();
        setupConsoleResize();
        loadProjects();
        refreshStatus();
        loadChannelOverview();
        loadScripts();
        loadPrompts();
    } catch (err) {
        console.error("Page init error (non-fatal, status polling still runs):", err);
    }
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
            loadChannelOverview();
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
        analytics: "YouTube Analytics",
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
    if (tabName === "analytics") {
        // checkYoutubeAuthStatus() already calls loadAnalyticsTable() itself
        // once it confirms the token is connected — calling both here would
        // just fetch analytics twice.
        checkYoutubeAuthStatus();
    }
    if (tabName === "dashboard") loadChannelOverview();
    if (tabName === "youtube") {
        loadPackaging();
    }
}

async function loadChannelOverview() {
    // Populates the "Tổng Quan Kênh YouTube" dashboard card from the same
    // /api/analytics summary the Analytics tab uses. Wrapped defensively —
    // this runs inside DOMContentLoaded, and previously being an undefined
    // function threw a ReferenceError there that silently killed the
    // setInterval(refreshStatus, ...) line right after it, freezing the
    // whole app's live status/log updates. Must never throw again.
    try {
        const res = await fetch("/api/analytics");
        const data = await res.json();
        if (!res.ok || !data.summary) {
            return; // Not connected / no data yet — leave the dashboard placeholders as-is.
        }
        const s = data.summary;

        const setMetric = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        };
        setMetric("channel-total-videos", Number(s.total_videos || 0).toLocaleString());
        setMetric("channel-total-views", Number(s.total_views || 0).toLocaleString());
        setMetric("channel-total-subscribers", Number(s.total_subscribers || 0).toLocaleString());
        setMetric("channel-total-likes", Number(s.total_likes || 0).toLocaleString());

        const videoLink = (id, title, views, likes) => id
            ? `<a href="https://youtu.be/${escapeHtml(id)}" target="_blank" rel="noopener noreferrer" style="color:#38bdf8;">${escapeHtml(title || id)}</a> — ${Number(views || 0).toLocaleString()} views, ${Number(likes || 0).toLocaleString()} likes`
            : "Chưa có dữ liệu analytics.";

        const topVideoEl = document.getElementById("channel-top-video");
        if (topVideoEl) {
            topVideoEl.innerHTML = videoLink(s.top_video_id, s.top_video_title, s.top_video_views, s.top_video_likes);
        }

        const formatSummaryHtml = (fmt) => {
            if (!fmt || !fmt.total_videos) return "Chưa có dữ liệu.";
            const topLine = fmt.top_video_id
                ? `<br>🏆 ${videoLink(fmt.top_video_id, fmt.top_video_title, fmt.top_video_views, fmt.top_video_likes)}`
                : "";
            return `${fmt.total_videos} video · ${Number(fmt.total_views || 0).toLocaleString()} views · ${Number(fmt.total_likes || 0).toLocaleString()} likes${topLine}`;
        };
        const regularEl = document.getElementById("regular-video-summary");
        if (regularEl) regularEl.innerHTML = formatSummaryHtml(s.regular_videos);
        const shortsEl = document.getElementById("shorts-summary");
        if (shortsEl) shortsEl.innerHTML = formatSummaryHtml(s.shorts);
    } catch (err) {
        console.error("loadChannelOverview failed (dashboard widget only, non-fatal):", err);
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

async function recommendShortSections() {
    const status = document.getElementById("short-recommendation");
    const checkboxes = Array.from(document.querySelectorAll(".short-section-checkbox"));
    if (!checkboxes.length) {
        alert("Chưa có section nào để AI phân tích.");
        return;
    }

    status.textContent = "AI đang phân tích hook và payoff...";
    try {
        const res = await fetch("/api/shorts/recommend", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}"
        });
        const data = await res.json();
        if (!res.ok) {
            status.textContent = data.error || "Không tạo được đề xuất.";
            return;
        }

        const selected = new Set(data.sections || []);
        checkboxes.forEach(checkbox => {
            checkbox.checked = selected.has(checkbox.value);
        });
        const titleInput = document.getElementById("short-name-input");
        if (titleInput && !titleInput.value.trim() && data.suggested_title) {
            titleInput.value = data.suggested_title
                .replace(/[^a-zA-Z0-9 _-]/g, "")
                .trim()
                .replace(/\s+/g, "_")
                .toLowerCase();
        }
        status.textContent = `${data.sections.length} section: ${data.reason || "Đã chọn một arc ngắn gọn."}`;
    } catch (err) {
        status.textContent = "Lỗi kết nối khi gọi AI.";
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

async function loadAnalyticsTable() {
    const wrap = document.getElementById("analytics-table-wrap");
    if (!wrap) return;
    wrap.innerHTML = '<span style="color:#38bdf8;">Đang tải dữ liệu từ YouTube Analytics...</span>';
    try {
        const res = await fetch("/api/analytics");
        const data = await res.json();
        renderChannelOverview(data.summary || {});
        if (!res.ok || !Array.isArray(data.videos) || data.videos.length === 0) {
            const message = data.message || "Chưa có dữ liệu analytics.";
            // issue_type comes from the backend now (no_token / reauth / api_disabled / error);
            // fall back to the old keyword-sniffing only for older/unexpected responses.
            const issueType = data.issue_type || (/scope|re-auth|token|permission|403|401/i.test(message) ? "reauth" : "info");
            const isWarning = issueType === "reauth" || issueType === "no_token" || issueType === "api_disabled";
            const title = issueType === "api_disabled"
                ? "⚠️ Cần bật YouTube Analytics API"
                : (isWarning ? "⚠️ Cần re-authenticate YouTube" : "ℹ️ Trạng thái analytics");
            const linkHtml = data.enable_url
                ? `<br><a href="${escapeHtml(data.enable_url)}" target="_blank" rel="noopener noreferrer" style="color:#38bdf8;">${escapeHtml(data.enable_url)}</a>`
                : "";
            wrap.innerHTML = `
                <div style="padding:12px 14px; border-radius:8px; border:1px solid ${isWarning ? '#f59e0b' : '#334155'}; background:${isWarning ? '#1f2937' : '#0f172a'}; color:${isWarning ? '#fbbf24' : '#cbd5e1'}; font-size:13px; line-height:1.5;">
                    <strong>${title}</strong><br>
                    ${escapeHtml(message)}${linkHtml}
                </div>
            `;
            return;
        }

        const rows = data.videos.map((video, idx) => {
            const url = video.url || (video.video_id ? `https://youtu.be/${video.video_id}` : "");
            const titleHtml = url
                ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" style="color:#38bdf8;">${escapeHtml(video.title || video.video_id)}</a>`
                : escapeHtml(video.title || video.video_id);
            // No official "is this a Short" flag exists via the API — duration
            // <=180s is the closest available proxy (YouTube's own Shorts cutoff).
            const isLikelyShort = video.duration_seconds > 0 && video.duration_seconds <= 180;
            const formatTag = video.duration_label
                ? `<span style="color:${isLikelyShort ? '#a78bfa' : '#64748b'}; font-size:11px;">${video.duration_label}${isLikelyShort ? ' · Short?' : ''}</span>`
                : "";
            return `
            <tr>
                <td>${idx + 1}</td>
                <td>
                    ${titleHtml}<br>
                    <code style="color:#64748b; font-size:11px;">${escapeHtml(video.video_id)}</code>
                </td>
                <td>${formatTag}</td>
                <td>${Number(video.views || 0).toLocaleString()}</td>
                <td>${Number(video.retention_pct || 0).toFixed(1)}%</td>
                <td>${Number(video.watch_minutes || 0).toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                <td>${Number(video.avg_view_seconds || 0).toFixed(0)}s</td>
                <td>${Number(video.likes || 0).toLocaleString()}</td>
                <td>${Number(video.comments || 0).toLocaleString()}</td>
                <td>${Number(video.shares || 0).toLocaleString()}</td>
            </tr>
        `;
        }).join("");

        wrap.innerHTML = `
            <table style="width:100%; border-collapse:collapse; color:#e2e8f0;">
                <thead>
                    <tr style="background:#111827; text-align:left;">
                        <th style="padding:8px;">#</th>
                        <th style="padding:8px;">Video</th>
                        <th style="padding:8px;">Thời lượng</th>
                        <th style="padding:8px;">Views</th>
                        <th style="padding:8px;">Retention</th>
                        <th style="padding:8px;">Watch time (ph)</th>
                        <th style="padding:8px;">Avg view</th>
                        <th style="padding:8px;">Likes</th>
                        <th style="padding:8px;">Comments</th>
                        <th style="padding:8px;">Shares</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows}
                </tbody>
            </table>
            <p style="color:#64748b; font-size:11px; margin-top:8px;">
                Impressions/CTR không hiển thị — YouTube Analytics API (reports.query) không hỗ trợ 2 metric này
                (khác với số liệu bạn thấy trong YouTube Studio, vốn lấy từ hệ thống báo cáo khác).
                "Short?" chỉ là suy đoán theo thời lượng ≤3 phút — API không có cờ chính thức phân biệt Shorts.
            </p>
        `;
    } catch (err) {
        wrap.innerHTML = '<span style="color:#f87171;">Lỗi tải analytics: ' + escapeHtml(String(err)) + '</span>';
    }
}

function renderChannelOverview(summary) {
    const ids = {
        videos: "channel-total-videos",
        views: "channel-total-views",
        subscribers: "channel-total-subscribers",
        likes: "channel-total-likes",
    };

    const safeNumber = (value, fallback = 0) => {
        const num = Number(value ?? fallback);
        return Number.isFinite(num) ? num : fallback;
    };

    const videosEl = document.getElementById(ids.videos);
    const viewsEl = document.getElementById(ids.views);
    const subscribersEl = document.getElementById(ids.subscribers);
    const likesEl = document.getElementById(ids.likes);
    const topVideoEl = document.getElementById("channel-top-video");

    if (videosEl) videosEl.textContent = safeNumber(summary.total_videos, 0).toLocaleString();
    if (viewsEl) viewsEl.textContent = safeNumber(summary.total_views, 0).toLocaleString();
    if (subscribersEl) subscribersEl.textContent = safeNumber(summary.total_subscribers, 0).toLocaleString();
    if (likesEl) likesEl.textContent = safeNumber(summary.total_likes, 0).toLocaleString();

    if (topVideoEl) {
        const videoId = summary.top_video_id || "";
        const title = summary.top_video_title || (videoId ? `Video ${videoId}` : "Chưa có video nào");
        const views = safeNumber(summary.top_video_views, 0);
        const likes = safeNumber(summary.top_video_likes, 0);

        if (videoId) {
            topVideoEl.innerHTML = `
                <strong style="color:#f8fafc;">${escapeHtml(title)}</strong><br>
                <span>Views: <strong>${views.toLocaleString()}</strong></span><br>
                <span>Likes: <strong>${likes.toLocaleString()}</strong></span>
            `;
        } else {
            topVideoEl.textContent = "Chưa có dữ liệu analytics cho video có view cao nhất.";
        }
    }

    renderFormatSummary("regular-video-summary", summary.regular_videos, "Chưa có video thường trong dữ liệu analytics.");
    renderFormatSummary("shorts-summary", summary.shorts, "Chưa có Short trong dữ liệu analytics.");
}

function renderFormatSummary(elementId, formatSummary, emptyMessage) {
    const element = document.getElementById(elementId);
    if (!element) return;

    const data = formatSummary || {};
    const videoCount = Number(data.total_videos || 0);
    if (!videoCount) {
        element.textContent = emptyMessage;
        return;
    }

    const title = data.top_video_title || data.top_video_id || "Không xác định";
    element.innerHTML = `
        <div>Số video: <strong>${videoCount.toLocaleString()}</strong></div>
        <div>Tổng views: <strong>${Number(data.total_views || 0).toLocaleString()}</strong></div>
        <div>Tổng likes: <strong>${Number(data.total_likes || 0).toLocaleString()}</strong></div>
        <div>Video cao nhất: <strong>${escapeHtml(title)}</strong> (${Number(data.top_video_views || 0).toLocaleString()} views)</div>
    `;
}

async function reauthenticateYoutube() {
    if (!confirm("Đăng nhập lại YouTube: xoá token cũ rồi mở Google login để xin đủ quyền (bao gồm yt-analytics.readonly)?")) {
        return;
    }

    showYoutubeOAuthStatus(true);

    try {
        const resetRes = await fetch("/api/youtube/auth/reset", { method: "POST" });
        const resetText = await resetRes.text();
        let resetData = {};
        try {
            resetData = resetText ? JSON.parse(resetText) : {};
        } catch {
            alert("Lỗi reset OAuth: Server trả về HTML thay vì JSON.\nChi tiết: " + resetText.slice(0, 180));
            showYoutubeOAuthStatus(false);
            return;
        }

        if (!resetRes.ok) {
            alert("Lỗi reset OAuth: " + (resetData.error || resetData.message || "Unknown"));
            showYoutubeOAuthStatus(false);
            return;
        }

        const loginRes = await fetch("/api/youtube/auth/login", { method: "POST" });
        const loginText = await loginRes.text();
        let loginData = {};
        try {
            loginData = loginText ? JSON.parse(loginText) : {};
        } catch {
            alert("Lỗi mở OAuth flow: Server trả về HTML thay vì JSON.\nChi tiết: " + loginText.slice(0, 180));
            showYoutubeOAuthStatus(false);
            return;
        }

        if (!loginRes.ok) {
            alert("Lỗi mở OAuth flow: " + (loginData.error || loginData.message || "Unknown"));
            showYoutubeOAuthStatus(false);
            return;
        }

        alert((resetData.message || "Token đã được reset.") + "\n" + (loginData.message || "Đang mở Google login..."));
        loadAnalyticsTable();
    } catch (err) {
        alert("Lỗi reset OAuth: " + (err && err.message ? err.message : String(err)));
        showYoutubeOAuthStatus(false);
    }
}

function showYoutubeOAuthStatus(show) {
    const status = document.getElementById("youtube-oauth-status");
    if (!status) return;
    status.style.display = show ? "block" : "none";
}

function renderYoutubeAuthInfo(data) {
    const panel = document.getElementById("youtube-auth-info");
    const accountEl = document.getElementById("youtube-auth-account");
    const channelEl = document.getElementById("youtube-auth-channel");
    if (!panel || !accountEl || !channelEl) return;

    if (!data || !data.connected) {
        panel.style.display = "block";
        accountEl.textContent = "Tài khoản: chưa kết nối";
        channelEl.textContent = data && data.message ? data.message : "Cần đăng nhập lại.";
        return;
    }

    panel.style.display = "block";
    accountEl.textContent = "Tài khoản: " + (data.account || "Google account connected");
    channelEl.textContent = "Kênh: " + (data.channel || "Không xác định được kênh hiện tại");
}

async function checkYoutubeAuthStatus() {
    try {
        const res = await fetch("/api/youtube/auth/status");
        const text = await res.text();
        let data = {};
        try {
            data = text ? JSON.parse(text) : {};
        } catch {
            const msg = text.slice(0, 240).replace(/\s+/g, " ");
            showYoutubeOAuthStatus(true);
            const status = document.getElementById("youtube-oauth-status");
            if (status) {
                status.innerHTML = `<strong>⚠️ Không kiểm tra được quyền YouTube</strong><br>SyntaxError: Unexpected token '<', "${escapeHtml(msg)}" is not valid JSON`;
            }
            return;
        }

        renderYoutubeAuthInfo(data);
        if (!data.connected) {
            showYoutubeOAuthStatus(true);
            const msg = data.message || "Cần xác thực lại.";
            document.getElementById("youtube-oauth-status").innerHTML = `<strong>⚠️ YouTube chưa sẵn sàng</strong><br>${escapeHtml(msg)}`;
            return;
        }
        showYoutubeOAuthStatus(false);
        await loadAnalyticsTable();
    } catch (err) {
        showYoutubeOAuthStatus(true);
        const status = document.getElementById("youtube-oauth-status");
        if (status) status.innerHTML = `<strong>⚠️ Không kiểm tra được quyền YouTube</strong><br>${escapeHtml(String(err))}`;
    }
}

async function loadPackaging() {
    const container = document.getElementById("packaging-options");
    if (!container) return;
    try {
        const res = await fetch("/api/packaging");
        const data = await res.json();
        renderPackaging(data.packages || []);
    } catch (err) {
        container.innerHTML = '<span style="color:#f87171;">Lỗi tải packaging options.</span>';
    }
}

function renderPackaging(packages) {
    const container = document.getElementById("packaging-options");
    if (!container) return;
    if (!packages.length) {
        container.innerHTML = '<span style="color:#64748b;">Chưa có concept. Nhấn “Tạo 3 Concept” để bắt đầu.</span>';
        return;
    }
    container.innerHTML = packages.map((item, index) => `
        <article style="background:#0c1220; border:1px solid #1e3a5f; border-radius:8px; padding:12px;">
            <strong style="color:#38bdf8;">Concept ${index + 1}</strong>
            <h4 style="margin:8px 0;">${escapeHtml(item.title)}</h4>
            <p style="color:#cbd5e1; font-size:12px; margin-bottom:8px;">${escapeHtml(item.angle)}</p>
            <div style="color:#fbbf24; font-weight:700; margin-bottom:8px;">Thumbnail: “${escapeHtml(item.thumbnail_text)}”</div>
            <p style="color:#94a3b8; font-size:12px; margin-bottom:10px;">${escapeHtml(item.description_opening)}</p>
            <button class="btn btn-sm btn-primary" type="button" onclick="usePackagingTitle(${index})">Dùng title này</button>
            <button class="btn btn-sm btn-secondary" type="button" onclick="generatePackagingThumbnail(${index})">🖼️ Tạo Thumbnail</button>
        </article>
    `).join("");
}

function escapeHtml(value) {
    return String(value || "").replace(/[&<>'"]/g, character => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
    })[character]);
}

function usePackagingTitle(index) {
    const title = document.querySelectorAll("#packaging-options article h4")[index]?.textContent;
    const titleInput = document.getElementById("yt-title");
    if (title && titleInput) {
        titleInput.value = title;
        titleInput.scrollIntoView({ behavior: "smooth", block: "center" });
    }
}

async function generatePackaging() {
    const container = document.getElementById("packaging-options");
    container.innerHTML = '<span style="color:#38bdf8;">Gemini đang tạo title và thumbnail concepts...</span>';
    try {
        const res = await fetch("/api/packaging/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}"
        });
        const data = await res.json();
        if (!res.ok) {
            container.innerHTML = `<span style="color:#f87171;">${escapeHtml(data.error)}</span>`;
            return;
        }
        renderPackaging(data.packages || []);
    } catch (err) {
        container.innerHTML = '<span style="color:#f87171;">Lỗi kết nối khi tạo packaging.</span>';
    }
}

async function generatePackagingThumbnail(index) {
    try {
        const res = await fetch("/api/packaging/thumbnail", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ index })
        });
        const data = await res.json();
        alert(res.ok ? "🖼️ " + data.message : "Lỗi: " + data.error);
    } catch (err) {
        alert("Lỗi tạo thumbnail: " + err);
    }
}

// ─── 主题 ───────────────────────────────────────────
const themes = ["blue", "gray", "green", "orange", "pink"];
let themeIndex = 0;

function applyTheme(name) { document.body.className = `theme-${name}`; }
function cycleTheme() {
  themeIndex = (themeIndex + 1) % themes.length;
  applyTheme(themes[themeIndex]);
}

// ─── DOM 引用 ────────────────────────────────────────
const messageList   = document.getElementById("messageList");
const promptInput   = document.getElementById("promptInput");
const sendBtn       = document.getElementById("sendNormal");
const historyPreview = document.getElementById("historyPreview");
const serverState   = document.getElementById("serverState");
const themeToggle   = document.getElementById("themeToggle");
const decisionLog   = document.getElementById("decisionLog");

// ─── 状态追踪 ─────────────────────────────────────────
let lastEventId  = 0;
let lastSysMsgId = 0;
const logLines   = [];

// ─── 消息渲染 ─────────────────────────────────────────
// source: "USER" | "SYS" | "AI"
// kind:   "chat" | "warn" | "sys" | "decision" | "ocr"
function appendMessage(kind, text, source) {
  const isUser = source === "USER";
  const isSys  = source === "SYS";      // 自动化系统消息

  const wrapper = document.createElement("div");
  // 系统消息居中单独一行，用户靠右，AI靠左
  wrapper.className = `message ${isUser ? "right" : isSys ? "center" : "left"}`;

  const meta = document.createElement("div");
  meta.className = "meta";
  const t = new Date().toLocaleTimeString("zh-CN", { hour12: false });

  if (isUser) {
    meta.textContent = `[${t}] 你`;
  } else if (isSys) {
    meta.textContent = `[${t}] 系统`;
    meta.style.color = "#e74c3c";
  } else {
    meta.textContent = `[${t}] AI 助手`;
  }

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;

  // 系统警告：红色气泡
  if (isSys || kind === "warn") {
    bubble.style.background = "linear-gradient(135deg, #3d0f0f, #1e0808)";
    bubble.style.border      = "1px solid rgba(231,76,60,0.45)";
    bubble.style.color       = "#ff9999";
    bubble.style.fontWeight  = "600";
  }

  wrapper.appendChild(meta);
  wrapper.appendChild(bubble);
  messageList.appendChild(wrapper);
  messageList.scrollTop = messageList.scrollHeight;
}

function addLog(text) {
  logLines.push(text);
  decisionLog.textContent = logLines.join("\n");
  decisionLog.scrollTop = decisionLog.scrollHeight;
}

// ─── 警告横幅（顶部弹出） ─────────────────────────────
function showWarnBanner(text) {
  const old = document.getElementById("warnBanner");
  if (old) old.remove();

  if (!document.getElementById("bannerStyle")) {
    const s = document.createElement("style");
    s.id = "bannerStyle";
    s.textContent = `
      @keyframes bannerSlide {
        from { transform: translateY(-100%); opacity: 0; }
        to   { transform: translateY(0);     opacity: 1; }
      }
      #warnBanner {
        position: fixed; top: 0; left: 0; right: 0; z-index: 9999;
        background: linear-gradient(90deg, #8e1a1a, #c0392b);
        color: #fff; font-size: 20px; font-weight: bold;
        text-align: center; padding: 18px 16px;
        letter-spacing: 0.05em;
        box-shadow: 0 4px 28px rgba(0,0,0,0.55);
        animation: bannerSlide 0.28s ease-out;
        cursor: pointer;
      }
    `;
    document.head.appendChild(s);
  }

  const banner = document.createElement("div");
  banner.id = "warnBanner";
  banner.textContent = text;
  banner.title = "点击关闭";
  banner.addEventListener("click", () => banner.remove());
  document.body.prepend(banner);
  setTimeout(() => { if (banner.parentNode) banner.remove(); }, 12000);
}

// ─── 轮询 API ─────────────────────────────────────────
async function fetchHistory() {
  try {
    const res  = await fetch("/api/history");
    const data = await res.json();
    historyPreview.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    historyPreview.textContent = `读取失败: ${e}`;
  }
}

async function fetchState() {
  try {
    const res   = await fetch("/api/state");
    const state = await res.json();

    // 状态栏
    serverState.textContent = state.automation_running
      ? `监督运行中 · ${state.history_count} 条记录`
      : `待机 · ${state.history_count} 条记录`;

    // 自动化事件日志（decision log 区域）
    if (state.event_id && state.event_id !== lastEventId) {
      lastEventId = state.event_id;
      if (state.status_line) addLog(state.status_line);
    }

    // 系统推送消息（对话区，红色气泡 + 顶部横幅）
    if (state.sys_msg_id && state.sys_msg_id !== lastSysMsgId) {
      lastSysMsgId = state.sys_msg_id;
      const msg = state.sys_msg_text || "📵 检测到摸鱼，快去学习！";
      showWarnBanner(msg);
      appendMessage("warn", msg, "SYS");
      addLog(`[${new Date().toLocaleTimeString("zh-CN",{hour12:false})}] ⚠️ 系统警告：${msg}`);
      await fetchHistory();
    }
  } catch (e) {
    serverState.textContent = `状态获取失败: ${e}`;
  }
}

// ─── 用户发送消息 ─────────────────────────────────────
async function sendMessage() {
  const text = promptInput.value.trim();
  if (!text) return;

  appendMessage("chat", text, "USER");
  addLog(`[${new Date().toLocaleTimeString("zh-CN",{hour12:false})}] 用户发送：${text}`);
  promptInput.value = "";
  serverState.textContent = "等待 AI 回复…";

  try {
    const res  = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text }),
    });
    const data = await res.json();
    const reply = data.reply || "[空回复]";
    appendMessage("chat", reply, "AI");
    addLog(`[${new Date().toLocaleTimeString("zh-CN",{hour12:false})}] AI 回复：${reply.slice(0,60)}…`);
    serverState.textContent = "就绪";
  } catch (e) {
    appendMessage("chat", `请求失败: ${e}`, "AI");
    serverState.textContent = "请求失败";
  }
}

// ─── 事件绑定 ─────────────────────────────────────────
themeToggle.addEventListener("click", cycleTheme);
sendBtn.addEventListener("click", sendMessage);
promptInput.addEventListener("keydown", (e) => { if (e.key === "Enter") sendMessage(); });

// ─── 初始化 ───────────────────────────────────────────
applyTheme(themes[themeIndex]);
appendMessage("chat", "监督系统已就绪。每5分钟自动检查一次，发现摸鱼立刻出手。有问题也可以直接问我。", "AI");
addLog(`[${new Date().toLocaleTimeString("zh-CN",{hour12:false})}] 系统启动完成`);
fetchHistory();
fetchState();
setInterval(fetchState, 30000);

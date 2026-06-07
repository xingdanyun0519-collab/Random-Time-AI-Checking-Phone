// ─── 主题 ───────────────────────────────────────────
const themes = ["blue", "gray", "green", "orange", "pink"];
let themeIndex = 0;

function applyTheme(name) { document.body.className = `theme-${name}`; }
function cycleTheme() {
  themeIndex = (themeIndex + 1) % themes.length;
  applyTheme(themes[themeIndex]);
}

// ─── DOM 引用 ────────────────────────────────────────
const messageList = document.getElementById("messageList");
const promptInput = document.getElementById("promptInput");
const sendBtn = document.getElementById("sendNormal");
const historyPreview = document.getElementById("historyPreview");
const serverState = document.getElementById("serverState");
const themeToggle = document.getElementById("themeToggle");
const decisionLog = document.getElementById("decisionLog");

// ─── 状态追踪 ─────────────────────────────────────────
let lastEventId = 0;
let lastSysMsgId = 0;
let chatCache = [];
const logLines = [];

function timeLabel(value) {
  if (!value) return new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const raw = String(value);
  return raw.includes(" ") ? raw.split(" ")[1] : raw;
}

function sortById(items) {
  return [...items].sort((a, b) => (a.id || 0) - (b.id || 0));
}

// ─── 消息渲染 ─────────────────────────────────────────
function renderChat(messages) {
  messageList.innerHTML = "";
  const sorted = sortById(messages);

  for (const item of sorted) {
    const role = item.role || "assistant";
    const source = role === "user" ? "USER" : role === "system" ? "SYS" : "AI";
    const isUser = source === "USER";
    const isSys = source === "SYS";

    const wrapper = document.createElement("div");
    wrapper.className = `message ${isUser ? "right" : isSys ? "center" : "left"}`;
    wrapper.dataset.messageId = String(item.id || "");

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `[${timeLabel(item.created_at)}] ${isUser ? "你" : isSys ? "系统" : "AI 助手"}`;
    if (isSys) meta.style.color = "#e74c3c";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = item.content || "";

    if (isSys) {
      bubble.style.background = "linear-gradient(135deg, #3d0f0f, #1e0808)";
      bubble.style.border = "1px solid rgba(231,76,60,0.45)";
      bubble.style.color = "#ff9999";
      bubble.style.fontWeight = "600";
    }

    const actions = document.createElement("div");
    actions.className = "message-actions";

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "msg-action";
    editBtn.textContent = "编辑";
    editBtn.title = "编辑这条消息";
    editBtn.addEventListener("click", () => editMessage(item));

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "msg-action danger";
    deleteBtn.textContent = "删除";
    deleteBtn.title = "删除这条消息";
    deleteBtn.addEventListener("click", () => deleteMessage(item));

    actions.appendChild(editBtn);
    actions.appendChild(deleteBtn);

    wrapper.appendChild(meta);
    wrapper.appendChild(bubble);
    wrapper.appendChild(actions);
    messageList.appendChild(wrapper);
  }

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

async function fetchChatHistory() {
  try {
    const res = await fetch("/api/chat-history");
    const data = await res.json();
    chatCache = Array.isArray(data) ? data : [];
    renderChat(chatCache);
  } catch (e) {
    historyPreview.textContent = `读取失败: ${e}`;
  }
}

async function fetchHistory() {
  try {
    const res = await fetch("/api/history");
    const data = await res.json();
    historyPreview.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    historyPreview.textContent = `读取失败: ${e}`;
  }
}

async function fetchState() {
  try {
    const res = await fetch("/api/state");
    const state = await res.json();

    serverState.textContent = state.automation_running
      ? `监督运行中 · ${state.chat_count || 0} 条记录`
      : `待机 · ${state.chat_count || 0} 条记录`;

    if (state.event_id && state.event_id !== lastEventId) {
      lastEventId = state.event_id;
      if (state.status_line) addLog(state.status_line);
    }

    if (state.sys_msg_id && state.sys_msg_id !== lastSysMsgId) {
      lastSysMsgId = state.sys_msg_id;
      const msg = state.sys_msg_text || "📵 检测到摸鱼，快去学习！";
      showWarnBanner(msg);
      addLog(`[${new Date().toLocaleTimeString("zh-CN",{hour12:false})}] ⚠️ 系统警告：${msg}`);
      await fetchChatHistory();
    }
  } catch (e) {
    serverState.textContent = `状态获取失败: ${e}`;
  }
}

async function reloadChat() {
  await fetchChatHistory();
  await fetchState();
}

async function deleteMessage(item) {
  if (!item?.id) return;
  const ok = confirm("确定删除这条消息吗？");
  if (!ok) return;

  try {
    const res = await fetch(`/api/chat-history/${item.id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(await res.text());
    await reloadChat();
  } catch (e) {
    alert(`删除失败: ${e}`);
  }
}

async function editMessage(item) {
  if (!item?.id) return;
  const nextText = prompt("修改消息内容：", item.content || "");
  if (nextText === null) return;

  try {
    const res = await fetch(`/api/chat-history/${item.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: nextText.trim() }),
    });
    if (!res.ok) throw new Error(await res.text());
    await reloadChat();
  } catch (e) {
    alert(`修改失败: ${e}`);
  }
}

// ─── 用户发送消息 ─────────────────────────────────────
async function sendMessage() {
  const text = promptInput.value.trim();
  if (!text) return;

  addLog(`[${new Date().toLocaleTimeString("zh-CN",{hour12:false})}] 用户发送：${text}`);
  promptInput.value = "";
  serverState.textContent = "等待 AI 回复…";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "请求失败");
    await reloadChat();
    addLog(`[${new Date().toLocaleTimeString("zh-CN",{hour12:false})}] AI 回复：${(data.reply || "[空回复]").slice(0,60)}…`);
    serverState.textContent = "就绪";
  } catch (e) {
    serverState.textContent = "请求失败";
    alert(`发送失败: ${e}`);
  }
}

// ─── 事件绑定 ─────────────────────────────────────────
themeToggle.addEventListener("click", cycleTheme);
sendBtn.addEventListener("click", sendMessage);
promptInput.addEventListener("keydown", (e) => { if (e.key === "Enter") sendMessage(); });

// ─── 初始化 ───────────────────────────────────────────
applyTheme(themes[themeIndex]);
fetchChatHistory();
fetchHistory();
fetchState();
setInterval(fetchState, 30000);

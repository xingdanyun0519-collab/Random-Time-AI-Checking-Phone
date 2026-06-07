const themes = ["blue", "gray", "green", "orange", "pink"];
let themeIndex = 0;

const messageList = document.getElementById("messageList");
const promptInput = document.getElementById("promptInput");
const sendNormal = document.getElementById("sendNormal");
const historyPreview = document.getElementById("historyPreview");
const serverState = document.getElementById("serverState");
const themeToggle = document.getElementById("themeToggle");
const decisionLog = document.getElementById("decisionLog");
let lastAutomationEventId = 0;
const decisionLines = [];

function applyTheme(name) {
  document.body.className = `theme-${name}`;
}

function cycleTheme() {
  themeIndex = (themeIndex + 1) % themes.length;
  applyTheme(themes[themeIndex]);
}

function appendMessage(mode, text, source = "AI") {
  const wrapper = document.createElement("div");
  const isUser = source === "USER";
  wrapper.className = `message ${isUser ? "right" : "left"}`;

  const meta = document.createElement("div");
  meta.className = "meta";
  const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  meta.textContent = isUser
    ? `[${time}] 用户输入 · ${mode === "decision" ? "决策" : "消息"}`
    : `[${time}] 使用${mode === "decision" ? "决策" : "消息"}技能`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;

  wrapper.appendChild(meta);
  wrapper.appendChild(bubble);
  messageList.appendChild(wrapper);
  messageList.scrollTop = messageList.scrollHeight;
}

function appendDecisionLog(text) {
  decisionLines.push(text);
  decisionLog.textContent = decisionLines.join("\n");
  decisionLog.scrollTop = decisionLog.scrollHeight;
}

async function fetchHistory() {
  try {
    const response = await fetch("/api/history");
    const data = await response.json();
    historyPreview.textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    historyPreview.textContent = `读取 history.json 失败: ${error}`;
  }
}

async function fetchState() {
  try {
    const response = await fetch("/api/state");
    const state = await response.json();

    if (typeof state.history_count === "number") {
      serverState.textContent = state.automation_running
        ? `自动化运行中 · history ${state.history_count}`
        : `自动化待机 · history ${state.history_count}`;
    }

    if (state.event_id && state.event_id !== lastAutomationEventId) {
      lastAutomationEventId = state.event_id;
      if (state.status_line) {
        appendDecisionLog(state.status_line);
      }
      if (state.event_type === "decision") {
        appendMessage("decision", state.event_text || "[空决策]", "AI");
      } else if (state.event_type === "ocr") {
        appendMessage("normal", state.event_text || "[空 OCR]", "AI");
      }
    }
  } catch (error) {
    serverState.textContent = `状态获取失败: ${error}`;
  }
}

async function sendMessage(mode) {
  const prompt = promptInput.value.trim();
  if (!prompt) return;

  appendMessage("normal", prompt, "USER");
  appendDecisionLog(`[${new Date().toLocaleTimeString("zh-CN", { hour12: false })}] 收到用户消息，正在等待 AI 回复。`);
  promptInput.value = "";
  serverState.textContent = "正在等待 Python/API 回复...";

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, prompt })
    });

    const data = await response.json();
    appendMessage("normal", data.reply || "[空回复]", "AI");
    appendDecisionLog(`[${new Date().toLocaleTimeString("zh-CN", { hour12: false })}] AI 回复完成：${data.reply || "[空回复]"}`);
    serverState.textContent = "回复已更新";
    await fetchHistory();
  } catch (error) {
    appendMessage("normal", `请求失败: ${error}`, "AI");
    appendDecisionLog(`[${new Date().toLocaleTimeString("zh-CN", { hour12: false })}] 请求失败：${error}`);
    serverState.textContent = "请求失败";
  }
}

themeToggle.addEventListener("click", cycleTheme);
sendNormal.addEventListener("click", () => sendMessage("normal"));
promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    sendMessage("normal");
  }
});

applyTheme(themes[themeIndex]);
appendMessage("normal", "网页已启动。这里会显示 AI 回复。", "AI");
appendDecisionLog(`[${new Date().toLocaleTimeString("zh-CN", { hour12: false })}] 网页已自动打开。自动化循环当前暂停，等待下一步指令。`);
fetchHistory();
fetchState();
setInterval(fetchState, 2000);

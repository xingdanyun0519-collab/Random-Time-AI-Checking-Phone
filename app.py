import json
import os
import random
import subprocess
import threading
import time
import webbrowser
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from openai import OpenAI

WORKSPACE_DIR = r"C:\Users\Administrator\Desktop\p"
WEB_DIR = os.path.join(WORKSPACE_DIR, "web")
HISTORY_PATH = os.path.join(WORKSPACE_DIR, "history.json")
SCREENSHOT_PATH = os.path.join(WORKSPACE_DIR, "screen.png")
OCR_EXE_PATH = r"C:\Users\Administrator\Desktop\Umi-OCR_Rapid_v2.1.5\Umi-OCR.exe"
OCR_OUTPUT_PATH = os.path.join(WORKSPACE_DIR, "ocr_result.txt")

ADB_PATH = "adb"
LOOP_INTERVAL_SECONDS = 10

HOST = "127.0.0.1"
PORT = 8000
BASE_URL = f"http://{HOST}:{PORT}"

MODEL_NAME = "deepseek-chat"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-3f7d3786669d4a88be6566e78b519840")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

ADB_SERIAL = None
STATE_LOCK = threading.Lock()
APP_STATE = {
    "event_id": 0,
    "event_type": "status",
    "event_text": "系统启动完成",
    "status_line": "[00:00:00] 系统启动完成",
    "automation_running": False,
    "history_count": 0,
    # 自动化系统推送给用户的消息队列
    "sys_msg_id": 0,
    "sys_msg_text": "",
}

# 需要关掉的非学习应用包名
NON_STUDY_PACKAGES = [
    "com.netease.cloudmusic",
    "cn.kuwo.player",
    "com.google.android.youtube",
    "com.meitu.meiyancamera",
    "com.mt.mtxx.mtxx",
    "com.tencent.mm",
    "com.openai.chatgpt",
    "com.anthropic.claude",
    "com.google.android.apps.bard",
    "com.deepseek.chat",
    "ai.x.grok",
    "com.moonshot.kimichat",
    "com.bytedance.dreamina",
    "com.aliyun.tongyi",
    "com.tencent.hunyuan.app.chat",
    "ai.perplexity.app.android",
]

# 用于用户对话的消息历史（仅用户↔AI对话）
CHAT_HISTORY = []
CHAT_LOCK = threading.Lock()

# ─────────────────────────────────────────────
# 系统提示词
# ─────────────────────────────────────────────

SUPERVISOR_SYSTEM_PROMPT = (
    "你是用户的AI学习监督助手。你的任务是监督用户有没有在认真学习，防止用户玩手机。\n"
    "当你收到截图OCR文字时，判断用户是否在学习（看教材/做题/看学习类内容）。\n"
    "第一行只回答 学习 或 摸鱼。\n"
    "如果是摸鱼，第二行给用户写一句有力度的中文催促语（不超过30字，可以带emoji，要有点严肃感）。"
)

DECISION_SYSTEM_PROMPT = (
    "你是手机屏幕监控调度器。根据历史OCR记录判断现在有没有必要截图检查用户在干什么。\n"
    "第一行只回答 是 或 否，后面可加一句极简理由。"
)

CHAT_SYSTEM_PROMPT = (
    "你是用户的AI学习助手。用户可能会向你提问学习相关问题，也可能闲聊。\n"
    "你要适度监督用户，如果用户在闲聊摸鱼，温和提醒去学习。\n"
    "回复要简洁有用。"
)


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def now_time():
    return datetime.now().strftime("%H:%M:%S")


def terminal_line(message):
    return f"[{now_time()}] {message}"


def run_command(command, capture_output=False):
    try:
        result = subprocess.run(
            command,
            capture_output=capture_output,
            shell=True,
            text=True,
            check=True,
        )
        if capture_output:
            return result.stdout.strip()
    except subprocess.CalledProcessError as error:
        if capture_output:
            return (error.stderr or "").strip()
        print(f"Command failed: {command}\n{error}")
    except FileNotFoundError:
        print(f"Command not found: {command}")
    return None


def adb_command(args):
    prefix = ADB_PATH
    if ADB_SERIAL:
        prefix = f"{ADB_PATH} -s {ADB_SERIAL}"
    return f"{prefix} {args}"


# ─────────────────────────────────────────────
# History JSON
# ─────────────────────────────────────────────

def ensure_history_file():
    if os.path.exists(HISTORY_PATH):
        return
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)


def load_history():
    ensure_history_file()
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return []


def append_history_record(text):
    history = load_history()
    numeric_ids = [item.get("id") for item in history if isinstance(item, dict) and isinstance(item.get("id"), int)]
    next_id = (max(numeric_ids) + 1) if numeric_ids else 1
    record = {
        "id": next_id,
        "capture_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "text": text,
    }
    history.append(record)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    with STATE_LOCK:
        APP_STATE["history_count"] = len(history)
    return record


# ─────────────────────────────────────────────
# AI 调用
# ─────────────────────────────────────────────

def build_client():
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "xxxxxxxxx":
        return None
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


def call_ai_simple(system_prompt, user_content):
    """单轮 AI 调用，返回文本"""
    client = build_client()
    if client is None:
        return "请先设置有效的 DEEPSEEK_API_KEY。"
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        stream=False,
    )
    return (response.choices[0].message.content or "").strip() or "[空回复]"


def call_ai_chat(user_message):
    """多轮对话：system_prompt + history + 新消息"""
    client = build_client()
    if client is None:
        return "请先设置有效的 DEEPSEEK_API_KEY。"

    with CHAT_LOCK:
        history_snapshot = list(CHAT_HISTORY)

    history_text = json.dumps(load_history(), ensure_ascii=False, indent=2)
    system_with_history = (
        f"{CHAT_SYSTEM_PROMPT}\n\n"
        f"以下是手机屏幕OCR历史记录，供你了解用户近期在做什么：\n{history_text}"
    )

    messages = [{"role": "system", "content": system_with_history}]
    messages.extend(history_snapshot)
    messages.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        stream=False,
    )
    reply = (response.choices[0].message.content or "").strip() or "[空回复]"

    # 把本轮追加进对话历史
    with CHAT_LOCK:
        CHAT_HISTORY.append({"role": "user", "content": user_message})
        CHAT_HISTORY.append({"role": "assistant", "content": reply})
        # 保留最近 20 轮（40 条）
        if len(CHAT_HISTORY) > 40:
            CHAT_HISTORY[:] = CHAT_HISTORY[-40:]

    return reply


# ─────────────────────────────────────────────
# 自动化决策 prompts
# ─────────────────────────────────────────────

def build_decision_prompt():
    history_json = json.dumps(load_history(), ensure_ascii=False, indent=2)
    return (
        f"以下是手机屏幕OCR历史记录：\n{history_json}\n\n"
        "现在要不要截图检查用户在干什么？只回答 是 或 否，附一句极简理由。"
    )


def build_ocr_judge_prompt(ocr_text):
    return (
        f"以下是刚刚截图的OCR文字：\n{ocr_text}\n\n"
        "用户是在学习还是在摸鱼？"
    )


def is_yes_reply(reply):
    first = reply.strip().splitlines()[0].strip().lower() if reply.strip() else ""
    return first.startswith("是") or first.startswith("yes") or first.startswith("y")


def parse_ocr_judge(reply):
    """返回 (is_studying: bool, warn_message: str)"""
    lines = [l.strip() for l in reply.strip().splitlines() if l.strip()]
    first = lines[0] if lines else ""
    is_studying = first.startswith("学习")
    warn_msg = lines[1] if len(lines) > 1 and not is_studying else ""
    if not is_studying and not warn_msg:
        warn_msg = "📵 检测到摸鱼，已关闭娱乐应用，快去学习！"
    return is_studying, warn_msg


# ─────────────────────────────────────────────
# ADB 操作
# ─────────────────────────────────────────────

def take_screenshot():
    command = f'{adb_command("exec-out screencap -p")} > "{SCREENSHOT_PATH}"'
    run_command(command)


def perform_ocr():
    if not os.path.exists(OCR_EXE_PATH):
        return ""
    if not os.path.exists(SCREENSHOT_PATH) or os.path.getsize(SCREENSHOT_PATH) == 0:
        return ""
    try:
        if os.path.exists(OCR_OUTPUT_PATH):
            os.remove(OCR_OUTPUT_PATH)
    except OSError:
        pass
    command = f'"{OCR_EXE_PATH}" --path "{SCREENSHOT_PATH}" --output "{OCR_OUTPUT_PATH}"'
    ocr_console = run_command(command, capture_output=True)
    if os.path.exists(OCR_OUTPUT_PATH):
        try:
            with open(OCR_OUTPUT_PATH, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                return text
        except OSError:
            pass
    return (ocr_console or "").strip()


def kill_non_study_apps():
    for pkg in NON_STUDY_PACKAGES:
        run_command(adb_command(f"shell am force-stop {pkg}"))
    print(f"已关闭 {len(NON_STUDY_PACKAGES)} 个非学习应用。")


def lock_screen():
    run_command(adb_command("shell input keyevent 26"))
    print("已锁屏。")


# ─────────────────────────────────────────────
# 状态管理
# ─────────────────────────────────────────────

def update_state(event_type, event_text, status_line=None):
    with STATE_LOCK:
        APP_STATE["event_id"] += 1
        APP_STATE["event_type"] = event_type
        APP_STATE["event_text"] = event_text
        APP_STATE["status_line"] = status_line or terminal_line(event_text)


def push_sys_message(text):
    """自动化系统向前端对话区推送一条消息"""
    with STATE_LOCK:
        APP_STATE["sys_msg_id"] += 1
        APP_STATE["sys_msg_text"] = text


def get_state_snapshot():
    with STATE_LOCK:
        snapshot = dict(APP_STATE)
        snapshot["history_count"] = len(load_history())
        snapshot["automation_running"] = bool(ADB_SERIAL)
        return snapshot


# ─────────────────────────────────────────────
# 设备连接（先于 HTTP server 运行）
# ─────────────────────────────────────────────

def get_connected_devices():
    output = run_command(f"{ADB_PATH} devices", capture_output=True)
    if not output:
        return []
    devices = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices attached"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def try_connect_wifi(port):
    """扫描 192.168.1.1~254，尝试用给定端口连接，返回第一个成功的 serial"""
    print(f"未检测到已连接设备，开始扫描局域网 192.168.1.x:{port} ...")
    run_command(f"{ADB_PATH} disconnect", capture_output=True)
    time.sleep(0.5)

    found = None
    for i in range(1, 255):
        ip = f"192.168.1.{i}"
        result = run_command(f"{ADB_PATH} connect {ip}:{port}", capture_output=True)
        if result and ("connected" in result.lower()) and ("unable" not in result.lower()) and ("failed" not in result.lower()):
            # 确认设备确实出现在 devices 列表
            devices = get_connected_devices()
            target = f"{ip}:{port}"
            if target in devices:
                print(f"✓ 连接成功：{target}")
                found = target
                break
            else:
                run_command(f"{ADB_PATH} disconnect {ip}:{port}", capture_output=True)

    if not found:
        print(f"扫描完毕，未找到任何在 :{port} 监听的设备。")
    return found


def select_device_or_connect():
    """在 HTTP server 启动之前完成设备连接，阻塞直到成功或放弃"""
    global ADB_SERIAL

    # 先看有没有已连接的设备
    devices = get_connected_devices()
    if len(devices) == 1:
        ADB_SERIAL = devices[0]
        print(f"检测到已连接设备：{ADB_SERIAL}")
        return
    if len(devices) > 1:
        print("检测到多个设备：")
        for idx, d in enumerate(devices, 1):
            print(f"  {idx}. {d}")
        chosen = input("请输入设备序列号（直接回车使用第一个）: ").strip()
        ADB_SERIAL = chosen if chosen in devices else devices[0]
        print(f"使用设备：{ADB_SERIAL}")
        return

    # 无已连接设备，要求输入端口并自动扫描
    while True:
        port = input("请输入手机无线调试端口（5位数字）: ").strip()
        if not port.isdigit() or len(port) != 5:
            print("错误：端口必须是5位数字，请重试。")
            continue
        serial = try_connect_wifi(port)
        if serial:
            ADB_SERIAL = serial
            return
        retry = input("扫描失败，是否重试？(y/n): ").strip().lower()
        if retry != "y":
            print("跳过设备连接，自动化功能不可用。")
            return


# ─────────────────────────────────────────────
# 主自动化循环
# ─────────────────────────────────────────────

def automation_loop():
    with STATE_LOCK:
        APP_STATE["automation_running"] = True

    while True:
        time.sleep(LOOP_INTERVAL_SECONDS)
        try:
            # Step 1: AI 决策要不要截图
            decision_reply = call_ai_simple(DECISION_SYSTEM_PROMPT, build_decision_prompt())
            update_state("decision", decision_reply, terminal_line(f"决策：{decision_reply.splitlines()[0]}"))

            if not is_yes_reply(decision_reply):
                update_state("status", "暂不截图", terminal_line("决策：暂不截图，继续等待"))
                continue

            # Step 2: 截图 + OCR
            update_state("status", "截图中…", terminal_line("开始截图OCR"))
            take_screenshot()
            ocr_text = perform_ocr()

            # Step 3: 无文字 = 锁屏中，跳过
            if not ocr_text or not ocr_text.strip():
                update_state("status", "锁屏中，跳过", terminal_line("OCR无文字：锁屏中，跳过"))
                continue

            # Step 4: 有文字，让 AI 判断是不是在学习
            append_history_record(ocr_text)
            update_state("ocr", ocr_text, terminal_line(f"OCR：{ocr_text[:50]}"))

            judge_reply = call_ai_simple(SUPERVISOR_SYSTEM_PROMPT, build_ocr_judge_prompt(ocr_text))
            is_studying, warn_msg = parse_ocr_judge(judge_reply)
            update_state("judge", judge_reply, terminal_line(f"判断：{judge_reply.splitlines()[0]}"))

            if is_studying:
                update_state("status", "用户在学习，继续监督", terminal_line("判断：学习中 ✓，进入下一轮"))
                continue

            # Step 5: 摸鱼 → 推系统消息 + 关App + 锁屏
            push_sys_message(warn_msg)
            update_state("warn", warn_msg, terminal_line(f"摸鱼警告：{warn_msg}"))

            time.sleep(0.5)
            kill_non_study_apps()
            time.sleep(0.5)
            lock_screen()
            update_state("status", "已关闭娱乐应用并锁屏", terminal_line("已关闭娱乐应用，已锁屏"))

        except Exception as error:
            update_state("status", f"异常：{error}", terminal_line(f"自动化异常：{error}"))


# ─────────────────────────────────────────────
# HTTP Handler
# ─────────────────────────────────────────────

class AppHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # 屏蔽 GET /api/state 的轮询日志，避免刷屏
        if "/api/state" in (args[0] if args else ""):
            return
        super().log_message(fmt, *args)

    def _send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/history":
            self._send_json(load_history())
            return
        if self.path == "/api/state":
            self._send_json(get_state_snapshot())
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/api/chat":
            self._send_json({"error": "Not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            self._send_json({"error": "Invalid JSON"}, status=400)
            return

        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            self._send_json({"error": "Prompt is empty"}, status=400)
            return

        try:
            reply = call_ai_chat(prompt)
        except Exception as error:
            self._send_json({"error": str(error)}, status=500)
            return

        self._send_json({"reply": reply})


def start_server():
    handler = partial(AppHandler, directory=WEB_DIR)
    server = ThreadingHTTPServer((HOST, PORT), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ─────────────────────────────────────────────
# 入口：先连设备，再开 server
# ─────────────────────────────────────────────

def main():
    ensure_history_file()

    # ① 先完成设备连接（阻塞，在终端交互）
    select_device_or_connect()

    # ② 再启动 HTTP server 和浏览器
    server = start_server()
    url = f"{BASE_URL}/"
    print(f"\nHTTP server 已启动：{url}")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    # ③ 启动自动化循环
    if ADB_SERIAL:
        update_state("status", "自动化循环已启动", terminal_line("自动化循环已启动"))
        threading.Thread(target=automation_loop, daemon=True).start()
    else:
        update_state("status", "未连接设备，自动化不可用", terminal_line("未连接设备"))

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

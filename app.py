import json
import os
import re
import shutil
import subprocess
import threading
import time
import webbrowser
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from openai import OpenAI

WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(WORKSPACE_DIR, "web")
HISTORY_PATH = os.path.join(WORKSPACE_DIR, "history.json")
CHAT_PATH = os.path.join(WORKSPACE_DIR, "chat.json")
SCREENSHOT_PATH = os.path.join(WORKSPACE_DIR, "screen.png")
OCR_EXE_PATH = r"#填写你的ocr路径"
OCR_OUTPUT_PATH = os.path.join(WORKSPACE_DIR, "ocr_result.txt")

# OCR 命令模板（换 OCR 工具时改这里即可）
# 占位符: {exe} → OCR_EXE_PATH, {input} → 截图路径, {output} → 结果文件路径
# Umi-OCR  :  '"{exe}" --path "{input}" --output "{output}"'
# Tesseract:  '"{exe}" "{input}" "{output}" -l chi_sim'
# 如果工具直接输出到 stdout，将 {output} 留空即可
OCR_COMMAND = '"{exe}" --path "{input}" --output "{output}"'

ADB_PATH = "adb"
INITIAL_LOOP_INTERVAL_SECONDS = 0
DEFAULT_NEXT_CHECK_SECONDS = 300
NO_TEXT_NEXT_CHECK_SECONDS = 300
MIN_LOOP_INTERVAL_SECONDS = 0
MAX_LOOP_INTERVAL_SECONDS = float("inf")

HOST = "127.0.0.1"
PORT = 8000
BASE_URL = f"http://{HOST}:{PORT}"

MODEL_NAME = "deepseek-chat"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "xxxxxxxxx")
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

# 用于用户对话的消息历史（仅用户↔AI对话，落盘到 chat.json）
CHAT_LOCK = threading.Lock()

# ─────────────────────────────────────────────
# 系统提示词
# ─────────────────────────────────────────────

SUPERVISOR_SYSTEM_PROMPT = (
    "你是用户的学习提醒助手。\n"
    "当你收到截图OCR文字时，判断用户是否在学习（看教材/做题/看学习类内容）。\n"
    "第一行只回答 学习 或 摸鱼。\n"
    "第二行只回答一个数字，表示下一次截图检查要等多少秒。\n"
    "如果用户明显在摸鱼，数字要小；如果用户在稳定学习，数字可以大一些。\n"
    "第三行给用户写一句简洁中文回应（不超过30字）。"
)

DECISION_SYSTEM_PROMPT = (
    "你是手机屏幕监控调度器。根据历史OCR记录或者随机参数判断现在有没有必要截图检查用户在干什么。\n"
    "第一行只回答 是 或 否，后面可加一句极简理由。"
)

CHAT_SYSTEM_PROMPT = (
    "你是用户的学习助手。\n"
    "当你不知道说什么的时候，回一个 '。' 然后锁屏。\n"
    "每句话不超过25字。"
)


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def now_time():
    return datetime.now().strftime("%H:%M:%S")


def terminal_line(message):
    return f"[{now_time()}] {message}"


def quote_command_path(path):
    if not path:
        return path
    if path.startswith('"') and path.endswith('"'):
        return path
    return f'"{path}"' if " " in path else path


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


def resolve_adb_path():
    if os.path.isabs(ADB_PATH) and os.path.exists(ADB_PATH):
        return ADB_PATH

    found = shutil.which(ADB_PATH)
    if found:
        return found

    try:
        result = subprocess.run(
            ["cmd", "/c", "where", "adb"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in (result.stdout or "").splitlines():
            candidate = line.strip()
            if candidate and os.path.exists(candidate):
                return candidate
    except (OSError, subprocess.SubprocessError):
        pass

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    common_paths = [
        os.path.join(local_app_data, "Android", "Sdk", "platform-tools", "adb.exe"),
        r"C:\Android\platform-tools\adb.exe",
        r"C:\platform-tools\adb.exe",
    ]
    for candidate in common_paths:
        if candidate and os.path.exists(candidate):
            return candidate

    return ADB_PATH


def adb_base_command():
    return quote_command_path(ADB_PATH)


def adb_command(args):
    prefix = adb_base_command()
    if ADB_SERIAL:
        prefix = f"{adb_base_command()} -s {ADB_SERIAL}"
    return f"{prefix} {args}"


def get_connected_devices():
    """Return adb serials in `device` state, ignoring adb warnings/noise."""
    output = run_command(f"{adb_base_command()} devices", capture_output=True) or ""
    devices = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("*") or line.lower().startswith("list of devices"):
            continue

        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])

    return devices


# ─────────────────────────────────────────────
# History JSON
# ─────────────────────────────────────────────

def ensure_history_file():
    if os.path.exists(HISTORY_PATH):
        return
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)


def ensure_chat_file():
    if os.path.exists(CHAT_PATH):
        return
    with open(CHAT_PATH, "w", encoding="utf-8") as f:
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


def load_chat_history():
    ensure_chat_file()
    try:
        with open(CHAT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                normalized = normalize_chat_history(data)
                if normalized != data:
                    save_chat_history(normalized)
                return normalized
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_chat_history(items):
    normalized = normalize_chat_history(items)
    with open(CHAT_PATH, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)


def normalize_chat_record(item):
    if not isinstance(item, dict):
        return None

    message_id = item.get("id")
    if not isinstance(message_id, int):
        return None

    time_value = item.get("time") or item.get("created_at") or item.get("capture_time")
    if not isinstance(time_value, str) or not time_value.strip():
        time_value = datetime.now().strftime("%H:%M:%S")
    else:
        time_value = time_value.strip()
        if " " in time_value:
            time_value = time_value.split()[-1]

    speaker = item.get("speaker")
    if not speaker:
        role = item.get("role")
        if role == "user":
            speaker = "user"
        elif role == "system":
            speaker = "ai"
        else:
            speaker = "ai"

    text_value = item.get("text")
    if text_value is None:
        text_value = item.get("content", "")

    return {
        "id": message_id,
        "time": time_value,
        "speaker": speaker,
        "text": str(text_value),
    }


def normalize_chat_history(items):
    normalized = []
    for item in items or []:
        record = normalize_chat_record(item)
        if record is not None:
            normalized.append(record)
    normalized.sort(key=lambda x: x.get("id", 0))
    return normalized


def next_chat_id(history):
    numeric_ids = [item.get("id") for item in history if isinstance(item, dict) and isinstance(item.get("id"), int)]
    return (max(numeric_ids) + 1) if numeric_ids else 1


def append_chat_message(speaker, text):
    with CHAT_LOCK:
        history = load_chat_history()
        record = {
            "id": next_chat_id(history),
            "time": datetime.now().strftime("%H:%M:%S"),
            "speaker": speaker,
            "text": str(text),
        }
        history.append(record)
        save_chat_history(history)
        return record


def seed_chat_history():
    with CHAT_LOCK:
        history = load_chat_history()
        if history:
            return
        history.append(
            {
                "id": 1,
                "time": datetime.now().strftime("%H:%M:%S"),
                "speaker": "ai",
                "text": "监督系统已就绪。需要我看着你学习，或者你也可以直接问我。",
            }
        )
        save_chat_history(history)


def update_chat_message(message_id, text):
    with CHAT_LOCK:
        history = load_chat_history()
        changed = False
        for item in history:
            if isinstance(item, dict) and item.get("id") == message_id:
                item["text"] = str(text)
                item["time"] = item.get("time") or datetime.now().strftime("%H:%M:%S")
                changed = True
                break
        if changed:
            save_chat_history(history)
        return changed


def delete_chat_message(message_id):
    with CHAT_LOCK:
        history = load_chat_history()
        new_history = [item for item in history if not (isinstance(item, dict) and item.get("id") == message_id)]
        if len(new_history) == len(history):
            return False
        save_chat_history(new_history)
        return True


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
        history_snapshot = list(load_chat_history())

    # 这里会把本地 chat.json 里的历史内容一并发给外部模型。
    history_text = json.dumps(load_chat_history(), ensure_ascii=False, indent=2)
    system_with_history = (
        f"{CHAT_SYSTEM_PROMPT}\n\n"
        f"以下是对话历史记录，供你了解用户近期在做什么：\n{history_text}"
    )

    messages = [{"role": "system", "content": system_with_history}]
    messages.extend(
        {
            "role": "user" if item.get("speaker") == "user" else "assistant",
            "content": item.get("text", ""),
        }
        for item in history_snapshot
        if isinstance(item, dict) and item.get("text")
    )
    messages.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        stream=False,
    )
    reply = (response.choices[0].message.content or "").strip() or "[空回复]"

    append_chat_message("user", user_message)
    append_chat_message("ai", reply)

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
        "用户是在学习还是在摸鱼？\n"
        "下一次看是什么时候？第二行回车输入数字，单位是秒。范围在0到正无穷s\n"
        "第三行输入给用户的简洁回答。"
    )


def is_yes_reply(reply):
    first = reply.strip().splitlines()[0].strip().lower() if reply.strip() else ""
    return first.startswith("是") or first.startswith("yes") or first.startswith("y")


def parse_interval_seconds(text):
    match = re.search(r"\d+", text or "")
    if not match:
        return DEFAULT_NEXT_CHECK_SECONDS
    seconds = int(match.group(0))
    return max(MIN_LOOP_INTERVAL_SECONDS, min(MAX_LOOP_INTERVAL_SECONDS, seconds))


def parse_ocr_judge(reply):
    """返回 (is_studying: bool, next_interval_seconds: int, message: str)"""
    lines = [l.strip() for l in reply.strip().splitlines() if l.strip()]
    first = lines[0] if lines else ""
    is_studying = first.startswith("学习")
    next_interval = parse_interval_seconds(lines[1] if len(lines) > 1 else "")
    message = lines[2] if len(lines) > 2 else ""
    if not message:
        message = "继续，别停。" if is_studying else "📵 别摸鱼，回去学习！"
    return is_studying, next_interval, message


# ─────────────────────────────────────────────
# ADB 操作
# ─────────────────────────────────────────────

def take_screenshot():
    command = f'{adb_command("exec-out screencap -p")} > "{SCREENSHOT_PATH}"'
    run_command(command)


def perform_ocr():
    """通用 OCR 调用：用 OCR_COMMAND 模板拼命令，优先读输出文件，兜底读 stdout"""
    if not os.path.exists(OCR_EXE_PATH):
        return ""
    if not os.path.exists(SCREENSHOT_PATH) or os.path.getsize(SCREENSHOT_PATH) == 0:
        return ""
    try:
        if os.path.exists(OCR_OUTPUT_PATH):
            os.remove(OCR_OUTPUT_PATH)
    except OSError:
        pass
    command = OCR_COMMAND.format(exe=OCR_EXE_PATH, input=SCREENSHOT_PATH, output=OCR_OUTPUT_PATH)
    ocr_console = run_command(command, capture_output=True)
    # 优先读输出文件
    if os.path.exists(OCR_OUTPUT_PATH):
        try:
            with open(OCR_OUTPUT_PATH, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                return text
        except OSError:
            pass
    # 兜底：读 stdout
    return (ocr_console or "").strip()


def is_no_text_ocr_result(ocr_text):
    """判断 OCR 是否返回了有效文字"""
    text = (ocr_text or "").strip()
    if not text:
        return True
    return False


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
    append_chat_message("ai", text)


def get_state_snapshot():
    with STATE_LOCK:
        snapshot = dict(APP_STATE)
        snapshot["history_count"] = len(load_history())
        snapshot["chat_count"] = len(load_chat_history())
        snapshot["automation_running"] = bool(ADB_SERIAL)
        return snapshot


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

    # 无已连接设备，要求输入设备地址
    while True:
        addr = input("请输入设备地址 (格式: 192.168.1.x:yyyyy 或直接输入 x:yyyyy): ").strip()
        
        # 如果只输入了 x:port，自动补全
        if addr.startswith("192.168.1."):
            target = addr
        elif ":" in addr and addr.split(":")[0].isdigit():
            target = f"192.168.1.{addr}"
        else:
            print("错误：格式不正确。请输入如 145:38883 或完整的 192.168.1.145:38883")
            continue
        
        # 如果 target 已经在设备列表里，先断开再重连
        if target in get_connected_devices():
            run_command(f"{adb_base_command()} disconnect {target}", capture_output=True)
            time.sleep(0.3)
        
        # 尝试连接
        result = run_command(f"{adb_base_command()} connect {target}", capture_output=True)
        if result and "connected" in result.lower():
            devices = get_connected_devices()
            if target in devices:
                print(f"✓ 连接成功：{target}")
                ADB_SERIAL = target
                return
        
        print(f"连接失败：{target}")
        retry = input("是否重试？(y/n): ").strip().lower()
        if retry != "y":
            print("跳过设备连接，自动化功能不可用。")
            return
# ─────────────────────────────────────────────
# 主自动化循环
# ─────────────────────────────────────────────

def automation_loop():
    with STATE_LOCK:
        APP_STATE["automation_running"] = True

    next_interval_seconds = INITIAL_LOOP_INTERVAL_SECONDS
    while True:
        if next_interval_seconds > 0:
            time.sleep(next_interval_seconds)
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

            # Step 3: 无文字 = 手机没开机/无内容，按学习态处理
            if is_no_text_ocr_result(ocr_text):
                next_interval_seconds = NO_TEXT_NEXT_CHECK_SECONDS
                no_text_message = f"手机没开机或无内容，{next_interval_seconds} 秒后再看"
                append_chat_message("ai", no_text_message)
                update_state(
                    "status",
                    no_text_message,
                    terminal_line(f"OCR无文字：{no_text_message}"),
                )
                continue

            # Step 4: 有文字，让 AI 判断是不是在学习
            append_history_record(ocr_text)
            update_state("ocr", ocr_text, terminal_line(f"OCR：{ocr_text[:50]}"))

            judge_reply = call_ai_simple(SUPERVISOR_SYSTEM_PROMPT, build_ocr_judge_prompt(ocr_text))
            is_studying, next_interval_seconds, user_message = parse_ocr_judge(judge_reply)
            update_state("judge", judge_reply, terminal_line(f"判断：{judge_reply.splitlines()[0]}"))

            if is_studying:
                append_chat_message("ai", user_message)
                update_state(
                    "status",
                    f"用户在学习，{next_interval_seconds} 秒后再看",
                    terminal_line(f"判断：学习中，{next_interval_seconds} 秒后再看"),
                )
                continue

            # Step 5: 摸鱼 → 推系统消息 + 关App + 锁屏
            push_sys_message(f"{user_message}\n{next_interval_seconds} 秒后再看")
            update_state(
                "warn",
                user_message,
                terminal_line(f"摸鱼警告：{user_message}；{next_interval_seconds} 秒后再看"),
            )

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
        if self.path == "/api/chat-history":
            self._send_json(load_chat_history())
            return
        if self.path == "/api/state":
            self._send_json(get_state_snapshot())
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/chat":
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
            return

        if self.path == "/api/chat-history":
            self._send_json({"error": "Not found"}, status=404)
            return

        self._send_json({"error": "Not found"}, status=404)
        return

    def do_PUT(self):
        self._handle_chat_history_mutation("PUT")

    def do_DELETE(self):
        self._handle_chat_history_mutation("DELETE")

    def _handle_chat_history_mutation(self, method):
        if not self.path.startswith("/api/chat-history/"):
            self._send_json({"error": "Not found"}, status=404)
            return

        try:
            message_id = int(self.path.rsplit("/", 1)[-1])
        except ValueError:
            self._send_json({"error": "Invalid message id"}, status=400)
            return

        if method == "DELETE":
            ok = delete_chat_message(message_id)
            if not ok:
                self._send_json({"error": "Message not found"}, status=404)
                return
            self._send_json({"ok": True})
            return

        if method == "PUT":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                payload = json.loads(body)
            except (ValueError, json.JSONDecodeError):
                self._send_json({"error": "Invalid JSON"}, status=400)
                return

            content = str(payload.get("content", "")).strip()
            if not content:
                self._send_json({"error": "Content is empty"}, status=400)
                return

            ok = update_chat_message(message_id, content)
            if not ok:
                self._send_json({"error": "Message not found"}, status=404)
                return

            self._send_json({"ok": True})
            return

        self._send_json({"error": "Not found"}, status=404)


def start_server():
    handler = partial(AppHandler, directory=WEB_DIR)
    server = ThreadingHTTPServer((HOST, PORT), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ─────────────────────────────────────────────
# 入口：先连设备，再开 server
# ─────────────────────────────────────────────

def main():
    global ADB_PATH

    ensure_history_file()
    ensure_chat_file()
    seed_chat_history()
    ADB_PATH = resolve_adb_path()
    print(f"ADB路径：{ADB_PATH}")

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

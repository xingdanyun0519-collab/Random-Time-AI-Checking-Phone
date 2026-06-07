import json
import os
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
PHONE_IP = "192.168.1.134"
LOOP_INTERVAL_SECONDS = 10

HOST = "127.0.0.1"
PORT = 8000
BASE_URL = f"http://{HOST}:{PORT}"

MODEL_NAME = "deepseek-v4-pro"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-3f7d3786669d4a88be6566e78b519840")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

NO_TEXT_MESSAGE = "[Message] No text in OCR result."
ADB_SERIAL = None
STATE_LOCK = threading.Lock()
APP_STATE = {
    "event_id": 0,
    "event_type": "status",
    "event_text": "系统启动完成",
    "status_line": "[00:00:00] 系统启动完成",
    "automation_running": False,
    "api_key_ready": False,
    "history_count": 0,
}


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


def ensure_history_file():
    if os.path.exists(HISTORY_PATH):
        return
    with open(HISTORY_PATH, "w", encoding="utf-8") as file:
        json.dump([], file, ensure_ascii=False, indent=2)


def load_history():
    ensure_history_file()
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
            if isinstance(data, list):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return []


def append_history_record(text):
    history = load_history()
    next_id = 1
    numeric_ids = [item.get("id") for item in history if isinstance(item, dict) and isinstance(item.get("id"), int)]
    if numeric_ids:
        next_id = max(numeric_ids) + 1

    record = {
        "id": next_id,
        "capture_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "text": text,
    }
    history.append(record)
    with open(HISTORY_PATH, "w", encoding="utf-8") as file:
        json.dump(history, file, ensure_ascii=False, indent=2)

    with STATE_LOCK:
        APP_STATE["history_count"] = len(history)

    return record


def build_client():
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "xxxxxxxxx":
        return None
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


def call_ai(mode, prompt):
    client = build_client()
    if client is None:
        return "请先设置有效的 DEEPSEEK_API_KEY。"

    if mode == "decision":
        system_prompt = (
            "你是手机屏幕 OCR 自动化决策器。"
            "你只负责决定此时要不要看截图。"
            "第一行必须只回答 是 或 否。"
            "后面可追加一句极简理由。"
        )
    else:
        system_prompt = "You are a helpful assistant. Keep responses concise and useful."

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        stream=False,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
    )
    content = response.choices[0].message.content or ""
    return content.strip() or "[空回复]"


def build_decision_prompt():
    history_json = json.dumps(load_history(), ensure_ascii=False, indent=2)
    return (
        "角色设定：你是手机屏幕 OCR 自动化决策器，只负责判断此时要不要看截图。\n"
        "json：\n"
        f"{history_json}\n"
        "此时要不要看截图：请只回答 是 或 否，并附一句极简理由。"
    )


def is_yes_reply(reply):
    first_line = reply.strip().splitlines()[0].strip().lower() if reply.strip() else ""
    return first_line.startswith("是") or first_line.startswith("yes") or first_line.startswith("y")


def is_no_text_result(ocr_text):
    if not ocr_text:
        return True
    return ocr_text.strip() == NO_TEXT_MESSAGE


def update_state(event_type, event_text, status_line=None):
    with STATE_LOCK:
        APP_STATE["event_id"] += 1
        APP_STATE["event_type"] = event_type
        APP_STATE["event_text"] = event_text
        APP_STATE["status_line"] = status_line or terminal_line(event_text)


def get_state_snapshot():
    with STATE_LOCK:
        snapshot = dict(APP_STATE)
        snapshot["api_key_ready"] = bool(DEEPSEEK_API_KEY and DEEPSEEK_API_KEY != "xxxxxxxxx")
        snapshot["history_count"] = len(load_history())
        snapshot["automation_running"] = bool(ADB_SERIAL)
        return snapshot


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


def select_device_or_connect():
    global ADB_SERIAL

    devices = get_connected_devices()
    if len(devices) == 1:
        ADB_SERIAL = devices[0]
        print(f"Detected connected device: {ADB_SERIAL}")
        return

    if len(devices) > 1:
        print("Detected multiple connected devices:")
        for index, device in enumerate(devices, start=1):
            print(f"  {index}. {device}")
        chosen = input("请输入要使用的设备序列号，或直接回车使用第一个: ").strip()
        ADB_SERIAL = chosen if chosen else devices[0]
        print(f"Using device: {ADB_SERIAL}")
        return

    port = input("请输入手机上当前最新的5位端口号: ").strip()
    if not port.isdigit() or len(port) != 5:
        print("错误：端口号必须是5位数字。")
        return

    print(f"Connecting to {PHONE_IP}:{port}...")
    run_command(f"{ADB_PATH} disconnect")
    time.sleep(1)
    run_command(f"{ADB_PATH} connect {PHONE_IP}:{port}")
    ADB_SERIAL = f"{PHONE_IP}:{port}"
    print(f"Using device: {ADB_SERIAL}")


def take_screenshot():
    command = f'{adb_command("exec-out screencap -p")} > "{SCREENSHOT_PATH}"'
    run_command(command)


def perform_ocr():
    if not os.path.exists(OCR_EXE_PATH):
        return "[Error] OCR executable not found."
    if not os.path.exists(SCREENSHOT_PATH) or os.path.getsize(SCREENSHOT_PATH) == 0:
        return ""

    try:
        if os.path.exists(OCR_OUTPUT_PATH):
            os.remove(OCR_OUTPUT_PATH)
    except OSError:
        pass

    command = f'"{OCR_EXE_PATH}" --path "{SCREENSHOT_PATH}" --output "{OCR_OUTPUT_PATH}"'
    ocr_console = run_command(command, capture_output=True)

    file_text = ""
    if os.path.exists(OCR_OUTPUT_PATH):
        try:
            with open(OCR_OUTPUT_PATH, "r", encoding="utf-8") as file:
                file_text = file.read().strip()
        except OSError:
            file_text = ""

    if file_text:
        return file_text
    return ocr_console.strip() if ocr_console else ""


def unlock_device():
    print("Attempting to unlock the device...")
    run_command(adb_command("shell input keyevent 224"))
    time.sleep(1)
    run_command(adb_command("shell input swipe 506 1773 506 1330 300"))
    time.sleep(0.5)

    commands = [
        "input tap 280 1454",
        "input tap 738 1725",
        "input tap 434 1848",
        "input tap 780 1454",
        "input tap 635 1725",
        "input tap 1007 1591",
        "input tap 228 1848",
        "input tap 223 1725",
        "input tap 921 1958",
    ]
    for cmd in commands:
        run_command(adb_command(f"shell {cmd}"))

    print("Unlock sequence sent.")


def automation_loop():
    with STATE_LOCK:
        APP_STATE["automation_running"] = True

    while True:
        time.sleep(LOOP_INTERVAL_SECONDS)

        try:
            prompt = build_decision_prompt()
            decision_reply = call_ai("decision", prompt)
            update_state(
                "decision",
                decision_reply,
                terminal_line(f"使用决策技能决策：{decision_reply}"),
            )

            if not is_yes_reply(decision_reply):
                update_state("status", "AI 判断暂不查看截图", terminal_line("使用决策技能：暂不查看截图"))
                continue

            update_state("status", "AI 要求查看截图，开始 OCR 流程", terminal_line("使用截图技能：开始 OCR"))
            take_screenshot()
            ocr_text = perform_ocr()

            if is_no_text_result(ocr_text):
                update_state("status", "截图无文字，执行解锁流程", terminal_line("使用解锁技能：截图无文字，开始解锁"))
                unlock_device()
                time.sleep(1)
                take_screenshot()
                ocr_text = perform_ocr()

            if is_no_text_result(ocr_text):
                ocr_text = NO_TEXT_MESSAGE

            append_history_record(ocr_text)
            update_state("ocr", ocr_text, terminal_line(f"使用OCR技能：{ocr_text[:80]}"))

        except Exception as error:
            update_state("status", f"自动化异常：{error}", terminal_line(f"自动化异常：{error}"))


class AppHandler(SimpleHTTPRequestHandler):
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

        mode = str(payload.get("mode", "normal"))
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            self._send_json({"error": "Prompt is empty"}, status=400)
            return

        try:
            reply = call_ai(mode, prompt)
        except Exception as error:
            self._send_json({"error": str(error)}, status=500)
            return

        self._send_json({"mode": mode, "reply": reply})


def start_server():
    handler = partial(AppHandler, directory=WEB_DIR)
    server = ThreadingHTTPServer((HOST, PORT), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    ensure_history_file()
    server = start_server()
    url = f"{BASE_URL}/"

    print(f"HTTP server started at {url}")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    select_device_or_connect()
    if ADB_SERIAL:
        update_state("status", "自动化循环已启动", terminal_line("自动化循环已启动"))
        threading.Thread(target=automation_loop, daemon=True).start()
    else:
        update_state("status", "未连接设备，自动化循环未启动", terminal_line("未连接设备，自动化循环未启动"))

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

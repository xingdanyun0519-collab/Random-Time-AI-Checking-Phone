import json
import os
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from openai import OpenAI

WORKSPACE_DIR = r"C:\Users\Administrator\Desktop\p"
WEB_DIR = os.path.join(WORKSPACE_DIR, "web")
HISTORY_PATH = os.path.join(WORKSPACE_DIR, "history.json")

HOST = "127.0.0.1"
PORT = 8000
BASE_URL = f"http://{HOST}:{PORT}"

MODEL_NAME = "deepseek-v4-pro"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-3f7d3786669d4a88be6566e78b519840")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

AUTOMATION_PAUSED = True


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


def build_client():
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "xxxxxxxxx":
        return None
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


def get_ai_reply(mode, prompt):
    client = build_client()
    if client is None:
        return "请先在环境变量 DEEPSEEK_API_KEY 中填写有效 API Key。"

    system_prompt = "You are a helpful assistant. Keep responses concise and useful."
    if mode == "decision":
        system_prompt = (
            "You are a strict decision engine for an automation workflow. "
            "Return only the final decision text, concise and deterministic."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        stream=False,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
    )
    content = response.choices[0].message.content or ""
    return content.strip() or "[空回复]"


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
            self._send_json(
                {
                    "automation_paused": AUTOMATION_PAUSED,
                    "history_count": len(load_history()),
                    "api_key_ready": bool(DEEPSEEK_API_KEY and DEEPSEEK_API_KEY != "xxxxxxxxx"),
                }
            )
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
            reply = get_ai_reply(mode, prompt)
        except Exception as error:
            self._send_json({"error": str(error)}, status=500)
            return

        self._send_json({"mode": mode, "reply": reply})


def main():
    ensure_history_file()
    handler = partial(AppHandler, directory=WEB_DIR)
    server = ThreadingHTTPServer((HOST, PORT), handler)
    url = f"{BASE_URL}/"

    print(f"HTTP server started at {url}")
    print("Automation is paused for now.")

    threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


# 旧的截图 / OCR / 解锁自动化循环已经停用。
# 如果后续要重新接回去，请把那段 while True 的循环单独恢复成一个函数，
# 不要直接放进 main()，避免网页服务和自动化逻辑互相阻塞。
#
# def automation_loop():
#     while True:
#         ...
#         time.sleep(10)


if __name__ == "__main__":
    main()

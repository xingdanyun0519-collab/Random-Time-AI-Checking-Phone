# Random Time AI Checking Phone

> An AI tool that uses ADB to randomly check your phone screen and remind you to study.

## 📌 Functions

- Randomly capture screenshots via ADB.
- Use AI to judge whether you are studying or playing games.
- Support HTTP online chatting.

## ❓ How It Works

1. Set up ADB and connect your phone to your computer (via USB or Wi-Fi debugging).
2. Fill in the config fields in `app.py` (OCR path & API Key).
3. Run `app.py`.
4. Done! Now you can chat online and manage your screen time.

## 🛠 Requirements

| Dependency | Description |
|------|------|
| Python | 3.x |
| ADB | Android Debug Bridge |
| Offline OCR | to read text from screenshots |
| API Key | for calling the AI model |
| A computer & a target phone | ... |

## 🚀 Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/xingdanyun0519-collab/p.git
cd p

# 2. Set up virtual environment & install dependencies
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 3. Configure settings in app.py
#    OCR_EXE_PATH → path to your offline OCR tool (e.g. Umi-OCR)
#    DEEPSEEK_API_KEY → your API key (lines 35-37)
```

### Run

```bash
.venv\Scripts\python app.py
```

## 📁 Project Structure

```
├── app.py              # Main program
├── web/                # Web frontend
│   ├── index.html
│   ├── app.js
│   └── style.css
├── requirements.txt    # Python dependencies
├── chat.json           # Chat history
├── history.json        # Screenshot history
└── ui.xml              # UI config
```

## 📝 Notes

- OCR only captures text from the screen — it does not record images.
- ...

---

*Just for personal studying*

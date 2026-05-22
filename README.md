# BoilerMind — Power Plant AI Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GitHub](https://img.shields.io/badge/GitHub-HunterisLive--1-black?logo=github)](https://github.com/HunterisLive-1)

Voice-first assistant powered by **Google Gemini Live** (built from the MAYA v4 mic/speaker/live stack) plus a **persistent book RAG** pipeline (ChromaDB + FastEmbed on `BAAI/bge-small-en-v1.5`). Ask boiler, steam plant, and operations questions aloud; BoilerMind retrieves passages from PDFs you place in `books/` and answers in Hinglish or English.

**Repository:** [github.com/HunterisLive-1/Maya-RAG](https://github.com/HunterisLive-1/Maya-RAG)

## License / open source

This project is **open source** under the **[MIT License](LICENSE)**.

You may use, modify, distribute, and build on this codebase for commercial or personal projects **as long as you keep** the MIT license and copyright notice in copies or substantial portions. See [`LICENSE`](LICENSE) for full terms.

---

## Maintainer & credits

| | |
|--|--|
| **Author & opensource stewardship** | **HunterisLive-1** ([@HunterisLive-1](https://github.com/HunterisLive-1)) |
| **License** | MIT (see [`LICENSE`](LICENSE)); copyright © 2026 HunterisLive |

Contributions welcome — see [**Contributing**](#contributing) below.

**Acknowledgements**

- **Google Gemini Live** ([Google AI Studio](https://aistudio.google.com)) — model and API.
- Original **MAYA v4**–style **`live_engine.py`** / **`audio_io.py`** I/O retained for Gemini Live interoperability.
- **Chroma**, **FastEmbed**, **PyMuPDF**, **FastAPI**, and the **Electron** ecosystem.

---

## Prerequisites

- **Python** 3.10+
- **Node.js** 18+ (for the optional Electron HUD)
- **Gemini API key** ([Google AI Studio](https://aistudio.google.com))
- Working **microphone** and **speakers / headphones**

## Installation

From the project root (paths with spaces — quote them in PowerShell if needed).

**Python (recommended: venv)**

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

Use the same venv for `python ingest_books.py` and `python main.py`.

**Electron HUD**

```powershell
cd hud_electron
npm install
cd ..
```

## Configuration

Create **`.env.local`** in the project root (**never commit** this file — it stays gitignored). Keep it BoilerMind‑minimal (Gemini keys + `MAYA_*` Live / mic + optional `BOILERMIND_*` HUD & RAG). If you migrated from Maya, restore from **`.env.local.bak`** as needed.

```env
GEMINI_API_KEY=your_key_here
GOOGLE_API_KEY=your_key_here

# Gemini Live — read by live_engine unless you override with BOILERMIND_* aliases in main.py
# MAYA_LIVE_MODEL=gemini-3.1-flash-live-preview
# MAYA_GEMINI_TTS_VOICE=Laomedeia
# MAYA_ECHO_TAIL_MS=300
# MAYA_MIC_DEVICE_INDEX=
# MAYA_LIVE_DENIED_RETRY_SEC=

# BoilerMind prefixes (aliases in main.py → MAYA_*)
# BOILERMIND_LIVE_MODEL=
# BOILERMIND_VOICE=
# BOILERMIND_TOP_K=5
# BOILERMIND_HUD_PORT=7070
# BOILERMIND_HUD_HOST=127.0.0.1
# BOILERMIND_SETTINGS_PORT=7071

# Optional (FastEmbed on Windows)
# HF_HUB_DISABLE_SYMLINKS_WARNING=1
```

Gemini Live uses **`MAYA_LIVE_MODEL`** and **`MAYA_GEMINI_TTS_VOICE`**; override with **`BOILERMIND_LIVE_MODEL`** / **`BOILERMIND_VOICE`** without editing `server/live_engine.py`.

## Adding books

1. Put PDFs under **`books/`** (recursive `books/**/*.pdf` is scanned).
2. Run ingestion (first run downloads FastEmbed weights):

```powershell
python ingest_books.py
```

One file explicitly:

```powershell
python ingest_books.py --book "books/manual.pdf" --name "Steam Manual Display Name"
```

Vector data persists under **`data/chroma_db/`**.

## Running the application

With the venv activated (`.\venv\Scripts\activate` on Windows):

```powershell
python main.py
```

- **Electron HUD**: bottom‑right overlay; **Ctrl+Shift+B** show/hide. If Electron is missing (`npm install` not run), the app stays **voice‑only**.
- Closing the HUD window (**×**) terminates the paired Python backend when **`PYTHON_PID`** was passed from `main.py`. If HUD is started standalone (`npm start` only), Electron exits without killing a parent Python process.
- **HUD WebSocket:** `ws://127.0.0.1:<port>` (**`BOILERMIND_HUD_PORT`**, default **7070** — may bump if busy).

## Settings (HUD gear)

With **`python main.py`** running, open **gear** in the HUD title bar for Settings (API test/save, **`BOILERMIND_TOP_K`**, ingest/remove books). Settings HTTP API: **`http://127.0.0.1:<BOILERMIND_SETTINGS_PORT>`** (default **7071**). Restart applies key/port changes to the voice stack where required.

## Windows build (PyInstaller + Inno)

```powershell
pip install pillow pyinstaller
python build.py
```

Output: **`dist/BoilerMind/BoilerMind.exe`** plus **`data/`** and **`books/`** beside it; frozen runs use **`data/chroma_db/`** next to the exe. **`icon.png`** at project root is converted to **`assets/icon.ico`** when present.

Compile **`installer.iss`** with [Inno Setup](https://jrsoftware.org/isinfo.php) → **`installer_output/`** (typical per-user install under **`%LocalAppData%\Programs\BoilerMind`**).

## Troubleshooting

| Issue | Fix |
|--------|-----|
| `No module named fitz` | `pip install PyMuPDF` |
| `GEMINI_API_KEY not set` | Settings gear or `.env.local`, restart for voice stack |
| HUD never connects | Python running? Firewall? `npm install` in **`hud_electron/`** |
| Electron won't launch | Check `hud_electron/node_modules/electron/dist/` |
| Wrong / quiet mic | **`MAYA_MIC_DEVICE_INDEX`** in `.env.local` |

## Repo layout

- `main.py` — entrypoint, env aliases, Electron, HUD WS, wires Settings
- `settings_server.py` — FastAPI settings / ingest on localhost
- `paths.py` — dev vs frozen paths for `data/`, `books/`
- `build.py`, `installer.iss` — Windows distribution
- `orchestrator.py` — BoilerMind runtime
- `book_rag.py`, `ingest_books.py` — PDF → Chroma
- `server/live_engine.py`, `server/audio_io.py` — Gemini Live I/O (MAYA-compatible)

## Contributing

Issues and pull requests are welcome on **[Maya‑RAG](https://github.com/HunterisLive-1/Maya-RAG)**. Please avoid committing secrets (`.env.local`, API keys, private PDFs you do not intend to distribute).

---

*MIT © 2026 HunterisLive-1. Thank you for using and extending BoilerMind.*

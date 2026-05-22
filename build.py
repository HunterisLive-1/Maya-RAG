"""
BoilerMind distribution build (PyInstaller one-dir + post steps).

Usage (from project root, venv activated):
  pip install -r requirements.txt pyinstaller
  python build.py

Icon: only `{repo_root}/icon.ico` (resolved to an absolute path) is used; no PNG/generated fallback.

Output: dist/BoilerMind/ with BoilerMind.exe
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# Sole app icon — absolute path (e.g. C:\\Users\\...\\power plant\\icon.ico).
ICON_ICO = os.path.abspath(os.path.join(ROOT, "icon.ico"))


def sep() -> str:
    return ";" if sys.platform == "win32" else ":"


def run(cmd: list[str], cwd: str | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, cwd=cwd or ROOT)


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def ensure_icon() -> str:
    """Use only ICON_ICO (absolute). Sync copy into assets/ for bundled shortcuts."""
    if not os.path.isfile(ICON_ICO):
        print("ERROR: Required icon missing:", ICON_ICO, file=sys.stderr)
        sys.exit(1)

    assets = os.path.join(ROOT, "assets")
    ensure_dir(assets)
    ico_assets = os.path.join(assets, "icon.ico")
    shutil.copy2(ICON_ICO, ico_assets)
    print("Using icon:", ICON_ICO)
    print("Copied to:", ico_assets)
    return ICON_ICO


def npm_install_hud() -> None:
    hud = os.path.join(ROOT, "hud_electron")
    nm = os.path.join(hud, "node_modules")
    if not os.path.isdir(nm):
        run(["npm", "install"], cwd=hud)


def warmup_fastembed() -> None:
    try:
        from fastembed import TextEmbedding

        TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        print("FastEmbed model warmed / cached.")
    except Exception as e:
        print("FastEmbed warmup skipped:", e)


def main() -> None:
    os.chdir(ROOT)
    ensure_dir(os.path.join(ROOT, "books"))
    ensure_dir(os.path.join(ROOT, "data"))
    assets = os.path.join(ROOT, "assets")
    ensure_dir(assets)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        run([sys.executable, "-m", "pip", "install", "pyinstaller"])

    npm_install_hud()
    icon = ensure_icon()  # absolute path passed to PyInstaller --icon=
    warmup_fastembed()

    s = sep()
    datas = [
        f"hud_electron{s}hud_electron",
        f"books{s}books",
        f"assets{s}assets",
    ]

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "main.py",
        "--name=BoilerMind",
        "--noconfirm",
        "--clean",
        # NOTE: --windowed hides the console (no stderr visible on crash).
        # A log file is written to {app}/boilermind.log via main.py for debugging.
        "--windowed",
        "--onedir",
        # --- collect-all: includes every submodule + data files (dynamic imports) ---
        "--collect-all=uvicorn",       # uvicorn.protocols.http.h11_impl, loops.asyncio, etc.
        "--collect-all=fastapi",
        "--collect-all=starlette",
        "--collect-all=pydantic",
        "--collect-all=pydantic_core",
        "--collect-all=chromadb",
        "--collect-all=fastembed",
        "--collect-all=google.genai",
        # --- hidden imports for packages that don't auto-collect cleanly ---
        "--hidden-import=chromadb.utils.embedding_functions",
        "--hidden-import=onnxruntime",
        "--hidden-import=google.generativeai",
        "--hidden-import=pyaudio",
        "--hidden-import=h11",          # uvicorn HTTP/1.1 backend
        "--hidden-import=anyio",
        "--hidden-import=anyio._backends._asyncio",
        "--hidden-import=click",        # uvicorn CLI dep (imported at runtime)
        "--hidden-import=dotenv",
    ]
    cmd.append(f"--icon={icon}")
    for d in datas:
        cmd.append(f"--add-data={d}")
    cmd.extend(
        [
            "--exclude-module=matplotlib",
            "--exclude-module=scipy",
            "--exclude-module=pandas",
            "--exclude-module=tkinter",
            "--exclude-module=torch",
            "--exclude-module=cv2",
        ]
    )

    run(cmd)

    dist_root = os.path.join(ROOT, "dist", "BoilerMind")
    ensure_dir(dist_root)
    exe_data = os.path.join(dist_root, "data")
    exe_books = os.path.join(dist_root, "books")
    ensure_dir(exe_data)
    ensure_dir(exe_books)

    # Copy icon.ico to dist root (installer uses {app}\icon.ico directly).
    ico_dst_root = os.path.join(dist_root, "icon.ico")
    shutil.copy2(ICON_ICO, ico_dst_root)
    print("Copied icon to dist root:", ico_dst_root)

    env_template = os.path.join(dist_root, ".env.local")
    if not os.path.isfile(env_template):
        with open(env_template, "w", encoding="utf-8") as f:
            f.write(
                "GEMINI_API_KEY=YOUR_API_KEY_HERE\n"
                "GOOGLE_API_KEY=YOUR_API_KEY_HERE\n"
                "BOILERMIND_TOP_K=5\n"
                "BOILERMIND_HUD_PORT=7070\n"
                "BOILERMIND_SETTINGS_PORT=7071\n"
                "BOILERMIND_VOICE=Laomedeia\n"
            )
        print("Wrote template dist/BoilerMind/.env.local")

    print("Build complete:", dist_root)
    print("⚠ Set GEMINI_API_KEY in Settings or .env.local before voice features work.")


if __name__ == "__main__":
    main()

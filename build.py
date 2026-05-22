"""
BoilerMind distribution build (PyInstaller one-dir + post steps).

Usage (from project root, venv activated):
  pip install -r requirements.txt pyinstaller pillow
  python build.py

Output: dist/BoilerMind/ with BoilerMind.exe
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def sep() -> str:
    return ";" if sys.platform == "win32" else ":"


def run(cmd: list[str], cwd: str | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, cwd=cwd or ROOT)


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def ensure_icon() -> str:
    assets = os.path.join(ROOT, "assets")
    ensure_dir(assets)
    ico = os.path.join(assets, "icon.ico")
    if os.path.isfile(ico):
        return ico
    png_candidates = [
        os.path.join(ROOT, "icon.png"),
        os.path.join(assets, "icon.png"),
    ]
    src_png = next((p for p in png_candidates if os.path.isfile(p)), None)
    if src_png:
        try:
            from PIL import Image

            img = Image.open(src_png).convert("RGBA")
            img.save(ico, format="ICO", sizes=[(256, 256), (64, 64), (32, 32), (16, 16)])
            print(f"Generated {ico} from {src_png}")
            return ico
        except Exception as e:
            print("Pillow ICO convert failed:", e)
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (256, 256), (6, 13, 22, 255))
        draw = ImageDraw.Draw(img)
        draw.ellipse([40, 40, 216, 216], outline=(0, 229, 255), width=8)
        draw.text((128, 128), "B", fill=(0, 229, 255), anchor="mm")
        img.save(ico, format="ICO", sizes=[(256, 256)])
        print("Generated placeholder icon.ico")
        return ico
    except Exception as e:
        print("No icon.ico and placeholder failed:", e)
        return ""


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
        from PIL import Image  # noqa: F401
    except ImportError:
        run([sys.executable, "-m", "pip", "install", "pillow"])
        from PIL import Image  # noqa: F401

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        run([sys.executable, "-m", "pip", "install", "pyinstaller"])

    npm_install_hud()
    icon = ensure_icon()
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
        "--windowed",
        "--onedir",
        "--hidden-import=chromadb",
        "--hidden-import=chromadb.utils.embedding_functions",
        "--hidden-import=fastembed",
        "--hidden-import=onnxruntime",
        "--hidden-import=google.genai",
        "--hidden-import=google.generativeai",
        "--hidden-import=pyaudio",
        "--hidden-import=uvicorn",
        "--hidden-import=fastapi",
        "--hidden-import=starlette",
        "--hidden-import=pydantic",
        "--collect-all=chromadb",
        "--collect-all=fastembed",
        "--collect-all=google.genai",
    ]
    if icon:
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

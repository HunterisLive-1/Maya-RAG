"""BoilerMind — Power Plant AI (voice + optional Electron HUD)."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from paths import books_dir, env_local_path, get_hud_electron_dir, get_writable_root
from port_util import pick_hud_and_settings_ports


def _setup_file_logging() -> None:
    """Write logs to boilermind.log next to the exe (visible even with --windowed)."""
    import logging.handlers

    log_path = get_writable_root() / "boilermind.log"
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    )
    logging.getLogger().addHandler(fh)


def _apply_env_aliases() -> None:
    """Map BOILERMIND_* aliases into MAYA_* keys read by verbatim live_engine.py."""
    lm = os.environ.get("BOILERMIND_LIVE_MODEL")
    if lm:
        os.environ.setdefault("MAYA_LIVE_MODEL", lm.strip())
    v = os.environ.get("BOILERMIND_VOICE")
    if v:
        os.environ.setdefault("MAYA_GEMINI_TTS_VOICE", v.strip())


def _has_api_key() -> bool:
    key = (
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
    )
    return bool(key)


def _bundled_electron_argv(hud_dir: Path) -> list[str] | None:
    """Local node_modules electron binary (matches MAYA packaging pattern)."""
    d = hud_dir.resolve()
    dist = d / "node_modules" / "electron" / "dist"
    if sys.platform == "win32":
        exe = dist / "electron.exe"
        return [str(exe), str(d)] if exe.is_file() else None
    exe = dist / "electron"
    return [str(exe), str(d)] if exe.is_file() else None


def _spawn_electron() -> subprocess.Popen | None:
    hud_dir = get_hud_electron_dir()
    argv = _bundled_electron_argv(hud_dir)
    if not argv:
        print(
            "HUD not available (run `npm install` inside hud_electron/). "
            "Continuing — voice-only mode."
        )
        return None
    hud_p = os.environ.get("BOILERMIND_HUD_WS_PORT", os.environ.get("BOILERMIND_HUD_PORT", "7070"))
    set_p = os.environ.get("BOILERMIND_SETTINGS_PORT", "7071")
    hud_host = os.environ.get("BOILERMIND_HUD_HOST", "127.0.0.1").strip() or "127.0.0.1"

    icon_abs = ""
    ic = get_writable_root() / "assets" / "icon.ico"
    if ic.is_file():
        icon_abs = str(ic.resolve())

    env = {
        **os.environ,
        "PYTHON_PID": str(os.getpid()),
        "BOILERMIND_HUD_WS_PORT": str(hud_p),
        "BOILERMIND_SETTINGS_PORT": str(set_p),
        "BOILERMIND_HUD_HOST": hud_host,
        "BOILERMIND_BOOKS_DIR": str(books_dir()),
    }
    if icon_abs:
        env["BOILERMIND_ICON_ICO"] = icon_abs
    try:
        return subprocess.Popen(
            argv,
            cwd=str(hud_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=sys.platform != "win32",
        )
    except OSError as e:
        print(f"Could not start Electron HUD: {e}")
        return None


async def async_main() -> None:
    from dotenv import load_dotenv

    writable = get_writable_root()
    load_dotenv(env_local_path(), override=True)
    load_dotenv(writable / ".env", override=False)

    hud_host = os.environ.get("BOILERMIND_HUD_HOST", "127.0.0.1").strip() or "127.0.0.1"
    hud_pref = int(os.environ.get("BOILERMIND_HUD_PORT", "7070") or "7070")
    settings_pref = int(os.environ.get("BOILERMIND_SETTINGS_PORT", "7071") or "7071")
    hud_p, set_p = pick_hud_and_settings_ports(hud_host, hud_pref, settings_pref)

    os.environ["BOILERMIND_HUD_PORT"] = str(hud_p)
    os.environ["BOILERMIND_HUD_WS_PORT"] = str(hud_p)
    os.environ["BOILERMIND_SETTINGS_PORT"] = str(set_p)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        force=True,
    )
    _setup_file_logging()  # always write to boilermind.log (visible when --windowed hides console)

    if hud_p != hud_pref:
        logging.warning(
            "HUD WebSocket preferred port %s busy — using %s",
            hud_pref,
            hud_p,
        )
    if set_p != settings_pref:
        logging.warning(
            "Settings API preferred port %s busy — using %s",
            settings_pref,
            set_p,
        )

    load_dotenv(env_local_path(), override=True)
    load_dotenv(writable / ".env", override=False)
    # File may override BOILERMIND_*_PORT; Electron must match actual bound ports (hud_p / set_p).
    os.environ["BOILERMIND_HUD_PORT"] = str(hud_p)
    os.environ["BOILERMIND_HUD_WS_PORT"] = str(hud_p)
    os.environ["BOILERMIND_SETTINGS_PORT"] = str(set_p)
    _apply_env_aliases()

    has_key = _has_api_key()
    if not has_key:
        logging.warning(
            "No GEMINI_API_KEY / GOOGLE_API_KEY yet — HUD and Settings API will start; "
            "voice starts after key is configured and app restarted."
        )

    books_dir().mkdir(parents=True, exist_ok=True)

    from book_rag import book_rag

    if book_rag.connect() and not book_rag.is_ready():
        print(
            "⚠️  Warning: No books ingested. Run `python ingest_books.py` "
            "or use Settings.\nContinuing anyway.\n"
        )

    print(
        "==============================\n"
        " BoilerMind — Power Plant AI\n"
        " HUD WS ws://%s:%s   Settings http://127.0.0.1:%s\n"
        "=============================="
        % (hud_host, hud_p, set_p),
    )

    from app_bridge import AppBridge
    from hud_ws_bridge import run_ws_server_forever
    from orchestrator import BoilerMindOrchestrator
    from settings_server import serve_settings_forever

    bridge = AppBridge.instance()

    async def _guarded_settings(port: int) -> None:
        try:
            await serve_settings_forever(port)
        except Exception:
            logging.exception("Settings API crashed on port %s — check boilermind.log", port)

    settings_task = asyncio.create_task(_guarded_settings(set_p))
    hud_task = asyncio.create_task(run_ws_server_forever(bridge))
    await asyncio.sleep(0)

    orch = BoilerMindOrchestrator()
    proc = _spawn_electron()

    orch_started = False

    try:
        if has_key:
            await orch.start()
            orch_started = True
        else:
            await asyncio.Future()

    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt — shutting down")
    finally:
        if orch_started:
            await orch.stop()
        hud_task.cancel()
        settings_task.cancel()
        try:
            await hud_task
        except asyncio.CancelledError:
            pass
        try:
            await settings_task
        except asyncio.CancelledError:
            pass
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

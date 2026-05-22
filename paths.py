"""Unified paths for dev and PyInstaller-frozen BoilerMind."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_writable_root() -> Path:
    """Directory beside the exe (frozen) or project root containing main.py."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # assume paths.py lives next to main.py when not frozen
    return Path(__file__).resolve().parent


def get_meipass_root() -> Path | None:
    if not is_frozen():
        return None
    mp = getattr(sys, "_MEIPASS", None)
    return Path(mp) if mp else None


def get_resource_path(relative: str) -> Path:
    """Bundled assets (e.g. hud_electron) under _MEIPASS when frozen."""
    rel = Path(relative)
    me = get_meipass_root()
    if me is not None:
        p = me / rel
        if p.exists():
            return p
    return get_writable_root() / rel


def get_hud_electron_dir() -> Path:
    bundled = get_resource_path("hud_electron")
    if bundled.is_dir():
        return bundled
    return get_writable_root() / "hud_electron"


def data_dir() -> Path:
    d = get_writable_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def chroma_db_directory() -> Path:
    p = data_dir() / "chroma_db"
    p.mkdir(parents=True, exist_ok=True)
    return p


def chroma_db_path_str() -> str:
    return str(chroma_db_directory())


def books_dir() -> Path:
    b = get_writable_root() / "books"
    b.mkdir(parents=True, exist_ok=True)
    return b


def env_local_path() -> Path:
    return get_writable_root() / ".env.local"

"""Read/write .env.local-style key=value pairs with merge semantics."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional


def parse_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.is_file():
        return data
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        key = k.strip()
        data[key] = v.strip().strip('"').strip("'")
    return data


def merge_write_env(path: Path, updates: Dict[str, Optional[str]]) -> None:
    """Replace/add keys whose values are not None; other lines/comments preserved."""
    path.parent.mkdir(parents=True, exist_ok=True)
    to_apply = {k: v for k, v in updates.items() if v is not None}
    pending = dict(to_apply)
    out_lines: list[str] = []

    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
        for raw in text.splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                out_lines.append(raw)
                continue
            key, _, _ = stripped.partition("=")
            key = key.strip()
            if key in to_apply:
                out_lines.append(f"{key}={to_apply[key]}")
                pending.pop(key, None)
            else:
                out_lines.append(raw)

    for key in sorted(pending.keys()):
        out_lines.append(f"{key}={pending[key]}")

    path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")

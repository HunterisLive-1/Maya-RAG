"""Pick free TCP ports when defaults are busy."""

from __future__ import annotations

import socket
from typing import Tuple


def _try_bind(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def pick_free_port(host: str, preferred: int, *, span: int = 80) -> int:
    """Return first port in [preferred, preferred+span) that is free."""
    for p in range(preferred, preferred + span):
        if _try_bind(host, p):
            return p
    raise RuntimeError(f"No free port in range {preferred}..{preferred + span - 1} on {host}")


def pick_hud_and_settings_ports(
    hud_host: str,
    hud_preferred: int,
    settings_preferred: int,
    *,
    span: int = 80,
) -> Tuple[int, int]:
    """Pick HUD WS port then settings API port (may shift if HUD consumed settings port)."""
    hud_port = pick_free_port(hud_host, hud_preferred, span=span)

    settings_start = settings_preferred
    if settings_preferred == hud_port:
        settings_start = settings_preferred + 1

    settings_port = pick_free_port(hud_host, settings_start, span=span)
    while settings_port == hud_port:
        settings_port = pick_free_port(hud_host, settings_port + 1, span=max(1, span - 1))
    return hud_port, settings_port

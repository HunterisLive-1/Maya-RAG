"""
BoilerMind HUD WebSocket bridge — broadcasts AppBridge dict events to Electron.
Runs on asyncio event loop beside the orchestrator.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue

from app_bridge import AppBridge

logger = logging.getLogger("boilermind-hud-ws")

_event_queue: "queue.Queue[str]" = queue.Queue()
_clients = set()

_bridge_registered = False
HUD_WS_HOST = os.environ.get("BOILERMIND_HUD_HOST", "127.0.0.1").strip() or "127.0.0.1"


def _hud_listen_port() -> int:
    return int(
        os.environ.get(
            "BOILERMIND_HUD_WS_PORT",
            os.environ.get("BOILERMIND_HUD_PORT", "7070"),
        )
        or "7070",
    )


def _settings_listen_port() -> int:
    return int(os.environ.get("BOILERMIND_SETTINGS_PORT", "7071") or "7071")


def push_event(event: dict) -> None:
    """Thread-safe: queue JSON for broadcast."""
    try:
        _event_queue.put_nowait(json.dumps(event, ensure_ascii=False))
    except Exception:
        pass


def subscribe_app_bridge(app_bridge: AppBridge) -> None:
    global _bridge_registered
    if _bridge_registered:
        return

    def _forward(ev: dict) -> None:
        push_event(ev)

    app_bridge.subscribe(_forward)
    _bridge_registered = True
    logger.info("HUD WS: subscribed to AppBridge")


def _welcome_payloads(bridge: AppBridge):
    """Initial messages sent to a freshly connected Electron client."""
    from book_rag import book_rag

    books = []
    try:
        books = book_rag.get_loaded_books()
    except Exception:
        books = []
    return [
        {"type": "status", "message": bridge.status_message},
        {"type": "activity", "state": bridge.activity},
        {
            "type": "books_status",
            "books": books,
            "count": len(books),
        },
        {
            "type": "hud_config",
            "live_model": os.environ.get("MAYA_LIVE_MODEL", "").strip(),
            "display_name": "BOILERMIND",
            "hud_ws_port": _hud_listen_port(),
            "settings_api_port": _settings_listen_port(),
        },
    ]


async def _ws_handler(websocket) -> None:
    import websockets

    bridge = AppBridge.instance()
    _clients.add(websocket)
    logger.info("HUD client connected (%d)", len(_clients))
    try:
        for payload in _welcome_payloads(bridge):
            await websocket.send(json.dumps(payload, ensure_ascii=False))

        async for _raw in websocket:
            pass
    except websockets.ConnectionClosed:
        pass
    except Exception:
        logger.debug("websocket handler stopped", exc_info=True)
    finally:
        _clients.discard(websocket)
        logger.info("HUD client disconnected (%d)", len(_clients))


async def _broadcaster() -> None:
    loop = asyncio.get_event_loop()
    while True:
        try:
            msg = await loop.run_in_executor(None, _event_queue.get, True, 0.05)
            dead = set()
            for ws in list(_clients):
                try:
                    await ws.send(msg)
                except Exception:
                    dead.add(ws)
            _clients.difference_update(dead)
        except queue.Empty:
            pass
        except asyncio.CancelledError:
            break
        except Exception:
            pass


async def run_ws_server_forever(app_bridge: AppBridge | None = None) -> None:
    import websockets

    bridge = app_bridge or AppBridge.instance()
    subscribe_app_bridge(bridge)

    port = _hud_listen_port()
    async with websockets.serve(_ws_handler, HUD_WS_HOST, port):
        logger.info("HUD WS listening on ws://%s:%s", HUD_WS_HOST, port)
        await _broadcaster()


async def start_bridge(app_bridge: AppBridge) -> None:
    await run_ws_server_forever(app_bridge)

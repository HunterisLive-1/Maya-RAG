"""Shared state bridge (no Qt): voice orchestrator ↔ HUD WebSocket."""

from __future__ import annotations

ACTIVITY_IDLE = "idle"
ACTIVITY_LISTENING = "listening"
ACTIVITY_RESPONDING = "responding"
ACTIVITY_TOOL_RUNNING = "tool_running"


class AppBridge:
    _instance = None

    def __init__(self) -> None:
        self._status_message = "OFFLINE"
        self._activity = ACTIVITY_IDLE
        self._listeners: list = []
        self._mic_enabled = True

    @classmethod
    def instance(cls) -> "AppBridge":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def activity(self) -> str:
        return self._activity

    @property
    def status_message(self) -> str:
        return self._status_message

    def subscribe(self, fn):
        """fn(event_dict: dict) — called synchronously."""
        self._listeners.append(fn)

    def _emit(self, event: dict) -> None:
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:
                pass

    def set_status(self, message: str) -> None:
        self._status_message = str(message)
        self._emit({"type": "status", "message": self._status_message})

    def set_activity(self, activity: str) -> None:
        if activity != self._activity:
            self._activity = activity
            self._emit({"type": "activity", "state": self._activity})

    def add_transcript(self, role: str, text: str) -> None:
        self._emit({"type": "transcript", "role": role, "text": text})

    def is_mic_enabled(self) -> bool:
        return self._mic_enabled




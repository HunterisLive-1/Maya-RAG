"""BoilerMind — Gemini Live orchestrator (simplified Maya ws_server MayaOrchestrator)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from app_bridge import (
    ACTIVITY_IDLE,
    ACTIVITY_LISTENING,
    ACTIVITY_RESPONDING,
    ACTIVITY_TOOL_RUNNING,
    AppBridge,
)
from book_rag import book_rag
from prompts import get_system_prompt
from server.audio_io import MicCapture, SpeakerPlayback
from server.live_engine import GeminiLiveEngine
from tool_declarations import ALL_TOOLS

logger = logging.getLogger("boilermind-orchestrator")


class BoilerMindOrchestrator:
    def __init__(self) -> None:
        self._bridge = AppBridge.instance()
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self._speaker = SpeakerPlayback(sample_rate=GeminiLiveEngine.SAMPLE_RATE)
        self._live: Optional[GeminiLiveEngine] = None

        self._speaker_mute_until = 0.0
        _tail_ms = int(os.environ.get("MAYA_ECHO_TAIL_MS", "300"))
        self._echo_tail = _tail_ms / 1000.0

        if not book_rag.connect():
            logger.warning("BookRAG connect failed.")
        elif not book_rag.is_ready():
            logger.warning("No books ingested yet. Run ingest_books.py first.")
            logger.warning("No books ingested. Run: python ingest_books.py")

        books = book_rag.get_loaded_books()
        if books:
            logger.info("%d book(s) loaded: %s", len(books), ', '.join(b['book_name'] for b in books))

    def _on_mic_chunk(self, pcm: bytes) -> None:
        if not self._bridge.is_mic_enabled():
            return
        if self._live is None:
            return

        if not self._speaker.is_empty():
            self._speaker_mute_until = time.monotonic() + self._echo_tail
            return
        if time.monotonic() < self._speaker_mute_until:
            return

        self._live.push_mic(pcm)

    def _on_tool_call_started(self, fn_name: str, fn_args: dict) -> None:
        self._bridge.set_activity(ACTIVITY_TOOL_RUNNING)
        logger.info("Tool call started: %s(%s)", fn_name, fn_args)

    async def _execute_tool(self, fn_name: str, fn_args: dict) -> str:
        self._bridge.set_activity(ACTIVITY_TOOL_RUNNING)
        try:
            if fn_name != "query_engineering_books":
                return f"Error: unsupported tool '{fn_name}'"

            if not book_rag.is_ready():
                return (
                    "Books abhi load nahi hain. "
                    "Pehle python ingest_books.py run karo."
                )

            query = fn_args.get("query") or ""
            bf_raw = fn_args.get("book_filter")
            bf = bf_raw.strip() if isinstance(bf_raw, str) and bf_raw.strip() else None

            top_k = int(os.environ.get("BOILERMIND_TOP_K", "5"))

            result = await asyncio.to_thread(
                book_rag.query,
                str(query),
                max(1, min(top_k, 20)),
                bf,
            )
            return result
        except Exception as e:
            logger.error("tool error: %s", e)
            return f"Error: {e}"
        finally:
            self._bridge.set_activity(ACTIVITY_RESPONDING)

    def _build_system_prompt(self) -> str:
        return get_system_prompt(book_rag.get_books_summary())

    async def start(self) -> None:
        self._loop = asyncio.get_event_loop()
        self._running = True

        self._speaker.start()
        mic = MicCapture(on_chunk=self._on_mic_chunk)
        mic.start()

        await self._start_live(mic)

    async def _start_live(self, mic: MicCapture) -> None:
        system_prompt = self._build_system_prompt()

        def _audio_out(pcm: bytes) -> None:
            self._speaker_mute_until = time.monotonic() + self._echo_tail
            self._speaker.play(pcm)

        def _on_transcript(role: str, text: str) -> None:
            if not text.strip():
                return
            self._bridge.add_transcript(role, text)
            if role == "user":
                logger.info("[User]: %s", text)
            else:
                logger.info("[BoilerMind]: %s", text[:280])

        def _on_interrupted() -> None:
            self._speaker.flush()
            self._speaker_mute_until = 0.0
            self._bridge.set_activity(ACTIVITY_LISTENING)
            logger.info("Barge-in — speaker flushed")

        self._live = GeminiLiveEngine(
            tool_executor=self._execute_tool,
            system_prompt=system_prompt,
            tool_declarations=ALL_TOOLS,
            audio_out_cb=_audio_out,
            on_tool_call=self._on_tool_call_started,
            on_transcript=_on_transcript,
            on_interrupted=_on_interrupted,
        )

        self._bridge.set_status("ONLINE")
        self._bridge.set_activity(ACTIVITY_LISTENING)
        logger.info(
            "BoilerMind orchestrator ready (Gemini Live) model_env=%s",
            os.environ.get("MAYA_LIVE_MODEL"),
        )

        try:
            await self._live.run()
        except Exception as e:
            logger.error("Live orchestrator error: %s", e)
            raise
        finally:
            self._running = False
            if self._live:
                self._live.stop()
            mic.stop()
            self._speaker.stop()
            self._bridge.set_status("OFFLINE")
            self._bridge.set_activity(ACTIVITY_IDLE)
            logger.info("BoilerMind orchestrator stopped")

    async def stop(self) -> None:
        self._running = False
        if self._live is not None:
            self._live.stop()
        self._speaker.flush()
        logger.info("BoilerMind orchestrator stop requested")

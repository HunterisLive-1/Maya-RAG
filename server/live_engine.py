"""Maya 4.0 — Gemini Live Engine (optional real-time voice backend).

When MAYA_VOICE_BACKEND=live this replaces the Whisper→LLM→TTS cascade
with a single bidirectional Gemini Live connection that handles STT+LLM+TTS
natively with extremely low latency.

Based on Google's official example:
https://github.com/google-gemini/gemini-live-api-examples/tree/main/gemini-live-genai-python-sdk

Model default: gemini-3.1-flash-live-preview
Input:  raw 16-bit PCM, 16 kHz mono ← MicCapture;
        optional JPEG frames (≤1 FPS) ← MAYA_LIVE_MEDIA_ENABLED
Output: raw 16-bit PCM, 24 kHz mono   → to SpeakerPlayback
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import traceback
from typing import Callable, Optional

logger = logging.getLogger("maya-live")

# When Google closes the Live WebSocket with 1008 / "denied access", reconnecting every few
# seconds wastes CPU and floods logs — user must fix Cloud/API key / billing / model access first.
_LIVE_DENIED_RETRY_SEC = int(os.environ.get("MAYA_LIVE_DENIED_RETRY_SEC", "300"))

_LIVE_MODEL = os.environ.get("MAYA_LIVE_MODEL", "gemini-3.1-flash-live-preview")
_LIVE_VOICE = os.environ.get("MAYA_GEMINI_TTS_VOICE", "Laomedeia")
_API_KEY    = (os.environ.get("GOOGLE_API_KEY")
               or os.environ.get("GEMINI_API_KEY", ""))

# Tools known to take >3 seconds — Maya sends interim acknowledgment so user isn't
# left in silence while waiting for the tool result.
_SLOW_TOOLS = frozenset({
    "coding_agent", "browser_agent", "analyze_screen",
    "create_maya_skill", "run_maya_skill",
    "ghost_creator_make_video", "ghost_creator_status",
    "whatsapp_send_message_to_contact", "whatsapp_read_chat",
    "explorer_search_files", "download_current_song", "download_song_by_name",
    "search_web", "fetch_url_page_text", "email_send", "email_read_unread",
    "generate_thumbnail", "generate_thumbnail_gemini", "generate_image_gemini",
    "youtube_search_and_play", "youtube_channel_stats",
    "youtube_sentiment_analysis", "prepare_maya_log_for_boss",
    "screenshot_and_send_on_whatsapp", "pc_run_terminal_command",
})


def _live_media_enabled() -> bool:
    v = os.environ.get("MAYA_LIVE_MEDIA_ENABLED", "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _is_live_project_access_denied(exc: BaseException) -> bool:
    """True when Google rejects the Live session (WS 1008 policy / project denied)."""
    msg = ""
    try:
        from google.genai.errors import APIError

        if isinstance(exc, APIError):
            msg = (exc.message or str(exc) or "").lower()
            if getattr(exc, "code", None) == 1008:
                return True
    except Exception:
        pass
    if not msg:
        msg = str(exc).lower()
    return "denied access" in msg or (
        "1008" in msg and "policy violation" in msg
    )


class GeminiLiveEngine:
    """Bridges MicCapture → Gemini Live API → SpeakerPlayback.

    Usage:
        engine = GeminiLiveEngine(tool_executor, system_prompt,
                                  audio_out_cb, on_tool_call, on_transcript,
                                  on_interrupted)
        await engine.run()         # blocks until stopped
        engine.push_mic(pcm)       # call from mic thread
        engine.push_media_jpeg(jpeg)  # optional Live vision (env-gated)
        engine.stop()
    """

    SAMPLE_RATE = 24_000  # Gemini Live output sample rate

    def __init__(
        self,
        tool_executor: Callable,
        system_prompt: str,
        tool_declarations: list,
        audio_out_cb: Callable[[bytes], None],
        on_tool_call: Optional[Callable[[str, dict], None]] = None,
        on_transcript: Optional[Callable[[str, str], None]] = None,
        on_interrupted: Optional[Callable[[], None]] = None,
    ):
        self._tool_executor     = tool_executor
        self._system_prompt     = system_prompt
        self._tool_declarations = tool_declarations
        self._audio_out_cb      = audio_out_cb
        self._on_tool_call      = on_tool_call
        self._on_transcript     = on_transcript
        self._on_interrupted    = on_interrupted

        self._mic_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._media_enabled = _live_media_enabled()
        self._running = False
        self._session = None
        self._reconnect_count = 0
        self._denied_access_logged = False

    # ── Public API ──────────────────────────────────────────────────────────

    def push_mic(self, pcm_bytes: bytes) -> None:
        """Feed a 20ms PCM chunk from MicCapture (called from mic thread)."""
        try:
            self._mic_queue.put_nowait(pcm_bytes)
        except asyncio.QueueFull:
            pass  # drop on overflow — mic thread cannot await

    def push_text(self, text: str) -> None:
        """Inject a text message into the Live session (e.g. background task completion).
        Safe to call from any thread or coroutine."""
        try:
            self._mic_queue.put_nowait(("__text__", text))
        except asyncio.QueueFull:
            pass

    def push_media_jpeg(self, jpeg_bytes: bytes) -> None:
        """Send one JPEG frame to Live (≤ ~1 FPS). No-op when MAYA_LIVE_MEDIA_ENABLED unset."""
        if not self._media_enabled or not jpeg_bytes:
            return
        try:
            self._mic_queue.put_nowait(("__media__", jpeg_bytes))
        except asyncio.QueueFull:
            pass

    def stop(self) -> None:
        self._running = False
        try:
            self._mic_queue.put_nowait(None)  # unblock sender
        except asyncio.QueueFull:
            pass

    async def run(self) -> None:
        """Main loop — connect and reconnect until stopped."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_run()
            except Exception as e:
                if not self._running:
                    break
                if _is_live_project_access_denied(e):
                    if not self._denied_access_logged:
                        self._denied_access_logged = True
                        logger.error(
                            "Gemini Live: Google denied access for this API key / project (HTTP 1008). "
                            "Fix in Google AI Studio or Cloud Console: billing, API enablement, "
                            "region eligibility, or Live model access — then restart Maya. "
                            "Docs: https://ai.google.dev/gemini-api/docs/live-api — "
                            "retrying every %ds (MAYA_LIVE_DENIED_RETRY_SEC).",
                            _LIVE_DENIED_RETRY_SEC,
                        )
                    else:
                        logger.warning(
                            "Gemini Live still denied — retry in %ds: %s",
                            _LIVE_DENIED_RETRY_SEC,
                            e,
                        )
                    await asyncio.sleep(_LIVE_DENIED_RETRY_SEC)
                    continue
                logger.error("Gemini Live error: %s — reconnecting in 3s\n%s",
                             e, traceback.format_exc())
                await asyncio.sleep(3)

    # ── Internal ────────────────────────────────────────────────────────────

    async def _connect_and_run(self) -> None:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=_API_KEY)

        tool_cfg = (types.Tool(function_declarations=self._tool_declarations)
                    if self._tool_declarations else None)

        live_cfg = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=_LIVE_VOICE)
                )
            ),
            # Use Content type for system instruction (more reliable than plain str)
            system_instruction=types.Content(
                parts=[types.Part(text=self._system_prompt)]
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            # KEY: only respond to actual speech activity, not continuous silence
            realtime_input_config=types.RealtimeInputConfig(
                turn_coverage="TURN_INCLUDES_ONLY_ACTIVITY",
            ),
            tools=[tool_cfg] if tool_cfg else [],
        )

        label = "reconnected" if self._reconnect_count > 0 else "connecting"
        logger.info("Gemini Live %s  model=%s  voice=%s", label, _LIVE_MODEL, _LIVE_VOICE)

        try:
            async with client.aio.live.connect(model=_LIVE_MODEL, config=live_cfg) as session:
                self._session = session
                self._reconnect_count += 1
                self._denied_access_logged = False
                logger.info("Gemini Live connected")

                event_queue: asyncio.Queue = asyncio.Queue()

                send_task    = asyncio.create_task(self._send_loop(session))
                receive_task = asyncio.create_task(self._receive_loop(session, event_queue))

                try:
                    while True:
                        event = await event_queue.get()
                        if event is None:  # receive_loop finished
                            break
                        etype = event.get("type")

                        if etype == "audio":
                            self._audio_out_cb(event["data"])

                        elif etype == "user_transcript":
                            if self._on_transcript:
                                self._on_transcript("user", event["text"])

                        elif etype == "model_transcript":
                            if self._on_transcript:
                                self._on_transcript("assistant", event["text"])

                        elif etype == "interrupted":
                            if self._on_interrupted:
                                self._on_interrupted()

                        elif etype == "tool_call":
                            await self._handle_tool_calls(session, event["tool_call"])

                        elif etype == "error":
                            logger.error("Gemini Live receive error: %s", event.get("error"))
                            break

                finally:
                    send_task.cancel()
                    receive_task.cancel()
                    try:
                        await asyncio.gather(send_task, receive_task,
                                             return_exceptions=True)
                    except Exception:
                        pass

        except Exception as e:
            if _is_live_project_access_denied(e):
                logger.error("Gemini Live session closed by Google (access denied): %s", e)
            else:
                logger.error("Gemini Live session error: %s\n%s", e, traceback.format_exc())
            raise
        finally:
            self._session = None
            logger.info("Gemini Live session closed")

    async def _send_loop(self, session) -> None:
        """Read from mic queue and stream audio/text to Gemini Live."""
        from google.genai import types
        try:
            while True:
                item = await self._mic_queue.get()
                if item is None or not self._running:
                    break
                # Text injection from background tasks (e.g. coding agent done)
                if isinstance(item, tuple) and len(item) == 2:
                    tag, payload = item[0], item[1]
                    if tag == "__text__":
                        try:
                            await session.send_realtime_input(text=payload)
                            logger.debug("Live injected text: %s", payload[:80])
                        except Exception as e:
                            logger.warning("Live text inject error: %s", e)
                        continue
                    if tag == "__media__" and isinstance(payload, (bytes, bytearray)):
                        try:
                            await session.send_realtime_input(
                                video=types.Blob(
                                    data=bytes(payload), mime_type="image/jpeg"
                                )
                            )
                        except Exception as e:
                            logger.warning("Live media send error: %s", e)
                        continue
                    logger.debug("Live send: unknown queue tag %r", tag)
                    continue
                # Normal PCM audio chunk
                if isinstance(item, (bytes, bytearray)):
                    await session.send_realtime_input(
                        audio=types.Blob(data=item, mime_type="audio/pcm;rate=16000")
                    )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Live send error: %s", e)

    async def _receive_loop(self, session, event_queue: asyncio.Queue) -> None:
        """Receive responses — re-enters the iterator after each turn_complete."""
        _user_buf: list[str] = []
        _model_buf: list[str] = []

        try:
            while True:  # re-enter after each turn_complete (per Google's example)
                async for response in session.receive():
                    if not self._running:
                        break

                    server_content = response.server_content
                    tool_call      = response.tool_call

                    if server_content:
                        # ── Audio output ─────────────────────────────────────
                        if server_content.model_turn:
                            for part in (server_content.model_turn.parts or []):
                                if part.inline_data and part.inline_data.data:
                                    await event_queue.put({
                                        "type": "audio",
                                        "data": part.inline_data.data,
                                    })

                        # ── Transcripts (accumulate per turn) ────────────────
                        if server_content.input_transcription:
                            t = (server_content.input_transcription.text or "").strip()
                            if t:
                                _user_buf.append(t)

                        if server_content.output_transcription:
                            t = (server_content.output_transcription.text or "").strip()
                            if t:
                                _model_buf.append(t)

                        # ── Turn complete → emit full transcript ──────────────
                        if server_content.turn_complete:
                            if _user_buf:
                                await event_queue.put({
                                    "type": "user_transcript",
                                    "text": " ".join(_user_buf),
                                })
                            if _model_buf:
                                await event_queue.put({
                                    "type": "model_transcript",
                                    "text": " ".join(_model_buf),
                                })
                            _user_buf.clear()
                            _model_buf.clear()

                        # ── Barge-in: model was interrupted ───────────────────
                        if server_content.interrupted:
                            _model_buf.clear()
                            await event_queue.put({"type": "interrupted"})

                    if tool_call:
                        await event_queue.put({"type": "tool_call", "tool_call": tool_call})

                logger.debug("Gemini receive iterator completed — re-entering")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Live receive error: %s\n%s", e, traceback.format_exc())
            await event_queue.put({"type": "error", "error": str(e)})
        finally:
            await event_queue.put(None)

    async def _handle_tool_calls(self, session, tool_call) -> None:
        """Execute all function calls in parallel and send responses.

        For known slow tools, an interim acknowledgment text is pushed into the
        session so the model can speak briefly while the tool runs — this prevents
        the long silence the user perceives during heavy operations.
        """
        from google.genai import types

        fn_calls = list(tool_call.function_calls or [])
        if not fn_calls:
            return

        logger.info("Live tool calls (%d): %s", len(fn_calls), [fc.name for fc in fn_calls])

        # If ANY function in this batch is slow, notify via bridge/HUD so user
        # knows Maya is working (can't send text to Gemini during tool-response phase).
        has_slow = any(fc.name in _SLOW_TOOLS for fc in fn_calls)
        if has_slow:
            slow_names = [fc.name for fc in fn_calls if fc.name in _SLOW_TOOLS]
            logger.info("Slow tool(s) starting: %s — user may experience brief silence", slow_names)
            if self._on_transcript:
                try:
                    self._on_transcript(
                        "assistant",
                        "Ek second Boss, kaam kar rahi hoon..."
                    )
                except Exception:
                    pass

        async def _run(fc):
            fn_name = fc.name
            fn_args = dict(fc.args) if fc.args else {}
            if self._on_tool_call:
                try:
                    self._on_tool_call(fn_name, fn_args)
                except Exception:
                    pass
            try:
                if inspect.iscoroutinefunction(self._tool_executor):
                    result = await self._tool_executor(fn_name, fn_args)
                else:
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, lambda: self._tool_executor(fn_name, fn_args)
                    )
                return types.FunctionResponse(
                    id=fc.id, name=fn_name,
                    response={"result": str(result) if result is not None else "done"},
                )
            except Exception as e:
                logger.error("Live tool %s error: %s", fn_name, e)
                return types.FunctionResponse(
                    id=fc.id, name=fn_name,
                    response={"error": str(e)},
                )

        responses = await asyncio.gather(*[_run(fc) for fc in fn_calls])
        try:
            await session.send_tool_response(function_responses=list(responses))
        except Exception as e:
            logger.warning("Live tool response error: %s", e)

"""Local audio I/O — no LiveKit.

MicCapture  : PyAudio → 20 ms PCM chunks (16 kHz mono int16 for Gemini Live)
SpeakerPlayback: PCM chunks → speaker output via PyAudio output stream
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
from typing import Callable, Optional

logger = logging.getLogger("maya-audio-io")

MIC_SAMPLE_RATE = 16_000
MIC_CHANNELS = 1
MIC_CHUNK_MS = 20
MIC_CHUNK_FRAMES = int(MIC_SAMPLE_RATE * MIC_CHUNK_MS / 1000)  # 320 frames per 20 ms @ 16k

EPS_SR = 1.0  # treat device default within ±1 Hz of 16k as native 16 kHz

SPK_SAMPLE_RATE = 22_050   # Piper TTS default; Gemini TTS uses 24000 (set at runtime)
SPK_CHANNELS = 1


def _pcm16_chunk_to_live_16k(pcm_native: bytes, native_sr: int, target_frames: int) -> bytes:
    """Resample one wall-clock MIC_CHUNK_MS slice at native_sr to ``target_frames`` int16 LE @16k Hz."""
    import numpy as np

    x = np.frombuffer(pcm_native, dtype=np.int16).astype(np.float32) / 32768.0
    n = len(x)
    if n == 0:
        return bytes(target_frames * 2)
    if native_sr <= 0:
        native_sr = MIC_SAMPLE_RATE
    if native_sr == MIC_SAMPLE_RATE and n == target_frames:
        return pcm_native[: target_frames * 2]
    new_len = target_frames
    if n == 1:
        y = np.full(new_len, x[0], dtype=np.float32)
    else:
        xp = np.linspace(0.0, float(n - 1), num=new_len, dtype=np.float32)
        xi = np.arange(n, dtype=np.float32)
        y = np.interp(xp, xi, x)
    yi = np.clip(np.rint(y * 32768.0), -32768, 32767).astype(np.int16)
    return yi.tobytes()


def _read_frames_for_rate(rate: int, resampled_mode: bool) -> int:
    if not resampled_mode:
        return MIC_CHUNK_FRAMES
    rf = rate * MIC_CHUNK_MS / 1000.0
    return max(1, int(round(rf)))


class MicCapture:
    """Captures mic audio in a background thread.

    Calls on_chunk(pcm_bytes) for each 20ms 16kHz mono int16 chunk (Gemini Live input).
    If the hardware default rate is not ~16 kHz, opens at that rate and resamples to 16 kHz.

    Env:
        MAYA_MIC_DEVICE_INDEX — PyAudio input device index (optional)
        MAYA_MIC_FORCE_RATE   — Force open sample rate, e.g. 48000 (optional)
    """

    def __init__(self, on_chunk: Callable[[bytes], None]):
        self._on_chunk = on_chunk
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._pa = None
        self._stream = None
        self._device_rate: int = MIC_SAMPLE_RATE
        self._use_resample: bool = False
        self._read_frames: int = MIC_CHUNK_FRAMES
        self._zeros_streak = 0

    @staticmethod
    def _resolve_input_device_index(pa) -> int:
        raw = (os.environ.get("MAYA_MIC_DEVICE_INDEX") or "").strip()
        if raw.isdigit():
            idx = int(raw)
            if 0 <= idx < pa.get_device_count():
                return idx
            logger.warning(
                "MAYA_MIC_DEVICE_INDEX=%s out of range; using default mic", idx
            )
        return pa.get_default_input_device_info()["index"]

    @staticmethod
    def _clamp_rate(rate: float) -> int:
        ri = int(round(rate))
        return max(8000, min(192_000, ri))

    def _build_open_attempts(
        self, pa, device_index: int
    ) -> list[tuple[int, bool, str]]:
        """(rate_hz, needs_resample, label)"""
        raw_force = (os.environ.get("MAYA_MIC_FORCE_RATE") or "").strip()

        tries: list[tuple[int, bool, str]] = []

        if raw_force:
            try:
                fr = float(raw_force.replace(",", "."))
                r_forced = self._clamp_rate(fr)
                tries.append(
                    (
                        r_forced,
                        abs(r_forced - MIC_SAMPLE_RATE) > EPS_SR,
                        f"MAYA_MIC_FORCE_RATE={r_forced}Hz",
                    )
                )
            except ValueError:
                logger.warning("Invalid MAYA_MIC_FORCE_RATE=%r — ignoring", raw_force)

        info = pa.get_device_info_by_index(device_index)
        native_sr_f = float(info.get("defaultSampleRate") or MIC_SAMPLE_RATE)

        tries.append((MIC_SAMPLE_RATE, False, "16 kHz direct (preferred)"))

        if not raw_force:
            if abs(native_sr_f - MIC_SAMPLE_RATE) >= EPS_SR:
                nr = self._clamp_rate(native_sr_f)
                if nr != MIC_SAMPLE_RATE:
                    tries.append(
                        (nr, True, f"device defaultSampleRate≈{nr}Hz"),
                    )

            # Common host rates — only as resampled paths distinct from nr
            nr_int = (
                self._clamp_rate(native_sr_f)
                if abs(native_sr_f - MIC_SAMPLE_RATE) >= EPS_SR
                else None
            )
            for alt in (48_000, 44_100, 32_000, 22_050):
                needs = abs(alt - MIC_SAMPLE_RATE) > EPS_SR
                if nr_int == alt:
                    continue
                tries.append((alt, needs, f"fallback try {alt}Hz"))

        # Dedupe by (rate, resample_flag) preserve first label
        seen: set[tuple[int, bool]] = set()
        out: list[tuple[int, bool, str]] = []
        for r, nr, lbl in tries:
            key = (r, nr)
            if key not in seen:
                seen.add(key)
                out.append((r, nr, lbl))
        return out

    def start(self) -> None:
        import pyaudio

        self._pa = pyaudio.PyAudio()
        pa = self._pa
        dev_idx = self._resolve_input_device_index(pa)

        attempts = self._build_open_attempts(pa, dev_idx)

        opened = False
        last_err: Optional[BaseException] = None
        fmt = pa.get_format_from_width(2)  # 16-bit

        for rate, need_rs, lbl in attempts:
            read_nf = _read_frames_for_rate(rate, need_rs)
            try:
                self._stream = pa.open(
                    format=fmt,
                    channels=MIC_CHANNELS,
                    rate=rate,
                    input=True,
                    input_device_index=dev_idx,
                    frames_per_buffer=read_nf,
                )
                self._device_rate = rate
                self._use_resample = rate != MIC_SAMPLE_RATE or read_nf != MIC_CHUNK_FRAMES
                self._read_frames = read_nf

                logger.info(
                    "MicCapture: opened %s (device %s, PyAudio rate=%s, read_frames=%s) → Live %s Hz (%s)",
                    lbl,
                    dev_idx,
                    rate,
                    self._read_frames,
                    MIC_SAMPLE_RATE,
                    "resampled"
                    if self._use_resample
                    else "direct",
                )
                opened = True
                break
            except Exception as e:
                last_err = e
                logger.warning(
                    "MicCapture open failed [%s]: %s — trying next fallback",
                    lbl,
                    e,
                )

        if not opened or self._stream is None:
            if self._pa:
                try:
                    self._pa.terminate()
                except Exception:
                    pass
                self._pa = None
            raise RuntimeError(
                "MicCapture: could not open any input stream for Gemini Live mic. "
                f"Last error: {last_err!r}. Check MAYA_MIC_DEVICE_INDEX / MAYA_MIC_FORCE_RATE."
            ) from last_err

        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="maya-mic")
        self._thread.start()

    def _loop(self) -> None:
        warned_all_zero = False
        while self._running:
            try:
                raw = self._stream.read(self._read_frames, exception_on_overflow=False)
                if self._use_resample:
                    out = _pcm16_chunk_to_live_16k(
                        raw, self._device_rate, MIC_CHUNK_FRAMES
                    )
                else:
                    out = raw
                    if len(out) != MIC_CHUNK_FRAMES * 2:
                        logger.warning(
                            "MicCapture: unexpected chunk size %d (expected %d)",
                            len(out),
                            MIC_CHUNK_FRAMES * 2,
                        )

                if len(out) == MIC_CHUNK_FRAMES * 2:
                    import numpy as np

                    m = np.frombuffer(out, dtype=np.int16)
                    if not np.any(m):
                        self._zeros_streak += 1
                        if (
                            self._zeros_streak >= 125 and not warned_all_zero
                        ):  # ~2.5s @ 50 loops/s-ish
                            logger.warning(
                                "MicCapture: many consecutive silence chunks — "
                                "wrong MIC device or unplugged microphone?"
                            )
                            warned_all_zero = True
                    else:
                        self._zeros_streak = 0
                        warned_all_zero = False

                self._on_chunk(out)
            except Exception as e:
                if self._running:
                    logger.error("Mic read error: %s", e)

    def stop(self) -> None:
        self._running = False
        try:
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
                self._stream = None
            if self._pa:
                self._pa.terminate()
                self._pa = None
        except Exception:
            pass
        logger.info("MicCapture stopped")


class SpeakerPlayback:
    """Plays PCM audio chunks on speakers using PyAudio.

    Thread-safe queue → background playback thread.
    flush() clears pending buffer (used for barge-in interruption).
    """

    def __init__(self, sample_rate: int = SPK_SAMPLE_RATE):
        self._sample_rate = sample_rate
        self._q: queue.Queue[Optional[bytes]] = queue.Queue()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._actively_writing = False   # True while stream.write() is blocking

    def set_sample_rate(self, rate: int) -> None:
        """Update sample rate (call before start() or after stop())."""
        self._sample_rate = rate

    def start(self) -> None:
        import pyaudio
        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=SPK_CHANNELS,
            rate=self._sample_rate,
            output=True,
            frames_per_buffer=1024,
        )
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="maya-spk")
        self._thread.start()
        logger.info("SpeakerPlayback started (%dHz)", self._sample_rate)

    def _loop(self) -> None:
        while self._running:
            try:
                chunk = self._q.get(timeout=0.1)
                if chunk is None:
                    continue
                self._actively_writing = True
                self._stream.write(chunk)
                self._actively_writing = False
            except queue.Empty:
                self._actively_writing = False
                continue
            except Exception as e:
                self._actively_writing = False
                if self._running:
                    logger.error("Speaker write error: %s", e)

    def play(self, pcm_bytes: bytes) -> None:
        """Queue PCM chunk for playback."""
        if pcm_bytes:
            self._q.put(pcm_bytes)

    def flush(self) -> None:
        """Clear all pending audio (barge-in / interruption)."""
        drained = 0
        while not self._q.empty():
            try:
                self._q.get_nowait()
                drained += 1
            except queue.Empty:
                break
        if drained:
            logger.debug("SpeakerPlayback: flushed %d pending chunks (barge-in)", drained)

    def is_empty(self) -> bool:
        """True only when queue is empty AND no chunk is actively playing."""
        return self._q.empty() and not self._actively_writing

    def stop(self) -> None:
        self._running = False
        self._q.put(None)
        try:
            if self._thread:
                self._thread.join(timeout=2.0)
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
            if self._pa:
                self._pa.terminate()
        except Exception:
            pass
        logger.info("SpeakerPlayback stopped")

from __future__ import annotations

import os
import tempfile
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any


def load_audio_backend() -> Any:
    """Load the array-backed sounddevice path used by microphone capture."""
    try:
        import numpy
        import sounddevice as sd  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("Install the audio extra: uv sync --extra audio") from exc
    _ = numpy.ndarray
    return sd


class MicrophoneRecorder:
    """Explicit start/stop recorder; it never captures while idle."""

    def __init__(
        self,
        sample_rate: int = 16_000,
        activity_listener: Callable[[float], None] | None = None,
        speech_threshold: float = 250.0,
    ) -> None:
        self.sample_rate = sample_rate
        self._activity_listener = activity_listener
        self._speech_threshold = speech_threshold
        self._lock = threading.Lock()
        self._stream: Any | None = None
        self._chunks: list[Any] = []
        self._last_level_at = 0.0
        self._activity_revision = 0
        self._last_activity_at = 0.0

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._stream is not None

    @property
    def activity_revision(self) -> int:
        with self._lock:
            return self._activity_revision

    @property
    def last_activity_at(self) -> float:
        with self._lock:
            return self._last_activity_at

    def start(self) -> None:
        sd = load_audio_backend()

        with self._lock:
            if self._stream is not None:
                raise RuntimeError("Microphone capture is already active")
            self._chunks = []
            self._last_level_at = 0.0
            self._activity_revision = 0
            self._last_activity_at = 0.0

            def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
                del frames, time_info
                if status:
                    # PortAudio status is intentionally not fatal; an empty capture is.
                    pass
                with self._lock:
                    if self._stream is not None:
                        self._chunks.append(indata.copy())
                        floating = indata.astype("float32")
                        rms = float((floating**2).mean() ** 0.5)
                        level = min(1.0, rms / 4000.0)
                        now = time.monotonic()
                        active = rms >= self._speech_threshold
                        should_report = now - self._last_level_at >= 0.08
                        if should_report:
                            self._last_level_at = now
                        if active:
                            self._activity_revision += 1
                            self._last_activity_at = now
                        listener = self._activity_listener if should_report else None
                    else:
                        level = 0.0
                        listener = None
                if listener is not None:
                    listener(level)

            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                callback=callback,
            )
            stream.start()
            self._stream = stream

    def stop(self) -> Path:
        with self._lock:
            stream = self._stream
            if stream is None:
                raise RuntimeError("Microphone capture is not active")
            self._stream = None
        try:
            stream.stop()
        finally:
            stream.close()
        with self._lock:
            chunks = self._chunks
            self._chunks = []
        if not chunks:
            raise RuntimeError("No audio was captured")
        return self._write_wav(chunks, "relay-")

    def snapshot(self) -> Path:
        """Copy captured audio so far for non-authoritative live preview transcription."""
        with self._lock:
            if self._stream is None:
                raise RuntimeError("Microphone capture is not active")
            chunks = [chunk.copy() for chunk in self._chunks]
        if not chunks:
            raise RuntimeError("No audio is available for preview")
        return self._write_wav(chunks, "relay-preview-")

    def _write_wav(self, chunks: list[Any], prefix: str) -> Path:
        descriptor, filename = tempfile.mkstemp(prefix=prefix, suffix=".wav")
        os.close(descriptor)
        path = Path(filename)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            for chunk in chunks:
                wav.writeframes(chunk.tobytes())
        return path

    def cancel(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
            self._chunks = []
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

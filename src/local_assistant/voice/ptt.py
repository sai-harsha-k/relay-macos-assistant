from __future__ import annotations

from pathlib import Path
from types import TracebackType

from local_assistant.audio.recorder import MicrophoneRecorder


class PushToTalkRecorder:
    """Terminal push-to-talk recorder using the default macOS input device."""

    def __init__(self, sample_rate: int = 16_000) -> None:
        self._recorder = MicrophoneRecorder(sample_rate)

    def record(self) -> Path:
        input("Press Enter to start recording...")
        self._recorder.start()
        try:
            input("Recording. Press Enter to stop...")
            return self._recorder.stop()
        except BaseException:
            self._recorder.cancel()
            raise


class TemporaryAudio:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> Path:
        return self.path

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.path.unlink(missing_ok=True)

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from local_assistant.runtime.errors import ProviderResponseError, ProviderUnavailableError

Runner = Callable[..., subprocess.CompletedProcess[str]]


class WhisperCppSpeechToTextProvider:
    def __init__(
        self,
        binary: str,
        model_path: Path,
        runner: Runner = subprocess.run,
        use_gpu: bool = True,
    ) -> None:
        self._binary = binary
        self._model_path = model_path
        self._runner = runner
        self._use_gpu = use_gpu

    def transcribe(self, audio_path: Path) -> str:
        binary = shutil.which(self._binary) if not Path(self._binary).exists() else self._binary
        if binary is None:
            raise ProviderUnavailableError(
                "whisper-cli was not found. Run scripts/bootstrap_macos.sh or set "
                "ASSISTANT_WHISPER_BINARY."
            )
        if not self._model_path.is_file():
            raise ProviderUnavailableError(
                f"Whisper model not found at {self._model_path}. Run scripts/bootstrap_macos.sh."
            )
        if not audio_path.is_file():
            raise ProviderUnavailableError(f"Audio file not found: {audio_path}")
        with tempfile.TemporaryDirectory(prefix="local-assistant-whisper-") as directory:
            output_base = Path(directory) / "transcript"
            command: list[str] = [
                str(binary),
                "--model",
                str(self._model_path),
                "--file",
                str(audio_path),
                "--language",
                "en",
                "--no-timestamps",
                "--output-txt",
                "--output-file",
                str(output_base),
            ]
            if not self._use_gpu:
                command.append("--no-gpu")
            try:
                completed = self._runner(
                    command, check=False, capture_output=True, text=True, timeout=180
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ProviderUnavailableError(f"whisper.cpp failed to start: {exc}") from exc
            if completed.returncode != 0:
                error = completed.stderr.strip()[-500:]
                raise ProviderUnavailableError(f"whisper.cpp failed: {error}")
            output_file = output_base.with_suffix(".txt")
            if not output_file.is_file():
                raise ProviderResponseError("whisper.cpp did not create a transcript")
            transcript = " ".join(output_file.read_text(encoding="utf-8").split())
            if not transcript:
                raise ProviderResponseError("whisper.cpp produced an empty transcript")
            return transcript

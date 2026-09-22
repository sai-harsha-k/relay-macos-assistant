import subprocess
from pathlib import Path

import pytest

from local_assistant.audio.whisper_cpp import WhisperCppSpeechToTextProvider
from local_assistant.runtime.errors import ProviderResponseError, ProviderUnavailableError


def test_whisper_invocation_and_response(tmp_path: Path) -> None:
    binary = tmp_path / "whisper-cli"
    binary.write_text("binary")
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output = Path(command[command.index("--output-file") + 1]).with_suffix(".txt")
        output.write_text(" open calculator \n")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    provider = WhisperCppSpeechToTextProvider(str(binary), model, runner=runner)
    assert provider.transcribe(audio) == "open calculator"


def test_whisper_missing_model_is_recoverable(tmp_path: Path) -> None:
    binary = tmp_path / "whisper-cli"
    binary.write_text("binary")
    with pytest.raises(ProviderUnavailableError, match="model not found"):
        WhisperCppSpeechToTextProvider(str(binary), tmp_path / "missing.bin").transcribe(
            tmp_path / "audio.wav"
        )


def test_whisper_empty_transcript_is_rejected(tmp_path: Path) -> None:
    binary = tmp_path / "whisper-cli"
    binary.write_text("binary")
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(command[command.index("--output-file") + 1]).with_suffix(".txt").write_text("")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(ProviderResponseError, match="empty"):
        WhisperCppSpeechToTextProvider(str(binary), model, runner=runner).transcribe(audio)

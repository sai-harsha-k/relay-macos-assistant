import os
import shutil
import subprocess
from pathlib import Path

import pytest

from local_assistant.audio.whisper_cpp import WhisperCppSpeechToTextProvider


@pytest.mark.integration
def test_whisper_transcribes_generated_audio(tmp_path: Path) -> None:
    binary = os.environ.get("ASSISTANT_WHISPER_BINARY", "whisper-cli")
    model = Path(os.environ.get("ASSISTANT_WHISPER_MODEL_PATH", "models/ggml-base.en.bin"))
    if shutil.which(binary) is None or not model.is_file():
        pytest.skip("whisper-cli or configured model is unavailable")
    if shutil.which("say") is None or shutil.which("ffmpeg") is None:
        pytest.skip("say and ffmpeg are needed to generate the audio fixture")
    aiff = tmp_path / "fixture.aiff"
    wav = tmp_path / "fixture.wav"
    subprocess.run(["say", "open calculator", "-o", str(aiff)], check=True)
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(aiff),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(wav),
        ],
        check=True,
    )
    # CI/sandbox hosts may deny Metal buffer allocation; the production default remains GPU-on.
    transcript = WhisperCppSpeechToTextProvider(binary, model, use_gpu=False).transcribe(wav)
    assert "calculator" in transcript.casefold()

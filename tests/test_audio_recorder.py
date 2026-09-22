from __future__ import annotations

from typing import Any

import numpy as np

from local_assistant.audio import recorder as recorder_module
from local_assistant.audio.recorder import MicrophoneRecorder, load_audio_backend


def test_microphone_audio_runtime_dependencies_are_importable() -> None:
    backend = load_audio_backend()
    assert backend.InputStream is not None
    assert MicrophoneRecorder().is_recording is False


def test_ambient_audio_updates_meter_without_resetting_speech_stability(
    monkeypatch: Any,
) -> None:
    callback: Any = None

    class FakeStream:
        def __init__(self, **kwargs: Any) -> None:
            nonlocal callback
            callback = kwargs["callback"]

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeBackend:
        InputStream = FakeStream

    now = [1.0]
    levels: list[float] = []
    monkeypatch.setattr(recorder_module, "load_audio_backend", lambda: FakeBackend)
    monkeypatch.setattr(recorder_module.time, "monotonic", lambda: now[0])
    microphone = MicrophoneRecorder(activity_listener=levels.append, speech_threshold=250.0)
    microphone.start()

    callback(np.full((800, 1), 100, dtype=np.int16), 800, None, None)
    assert microphone.activity_revision == 0
    assert microphone.last_activity_at == 0.0
    assert levels

    now[0] = 1.1
    callback(np.full((800, 1), 1000, dtype=np.int16), 800, None, None)
    assert microphone.activity_revision == 1
    assert microphone.last_activity_at == 1.1
    assert levels[-1] > levels[0]
    microphone.cancel()

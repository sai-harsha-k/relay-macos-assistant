from __future__ import annotations

import threading
import time
from pathlib import Path

from local_assistant.actions.models import OpenApp
from local_assistant.app.lifecycle import RelayState, SingleInstanceLock, VoiceSession
from local_assistant.runtime.controller import PreparedAction
from local_assistant.runtime.types import (
    ControllerResult,
    DecisionResult,
    ExecutionResult,
    PipelineEvent,
)
from local_assistant.safety.policy import SafetyPolicy


class FakeRecorder:
    def __init__(self, audio_path: Path) -> None:
        self.audio_path = audio_path
        self.started = 0
        self.stopped = 0
        self.cancelled = 0
        self.active = False

    def start(self) -> None:
        self.started += 1
        self.active = True

    def stop(self) -> Path:
        self.stopped += 1
        self.active = False
        return self.audio_path

    def cancel(self) -> None:
        self.cancelled += 1
        self.active = False


class FakeController:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def handle_audio(self, audio_path: Path) -> ControllerResult:
        self.paths.append(audio_path)
        return ControllerResult(
            "open calculator",
            OpenApp(app_name="Calculator"),
            ExecutionResult(True, "Opened Calculator"),
        )


def test_voice_session_start_stop_invokes_shared_controller_and_removes_audio(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"audio")
    recorder = FakeRecorder(audio)
    controller = FakeController()
    finished = threading.Event()

    def status(state: RelayState, detail: str) -> None:
        del detail
        if state is RelayState.DONE and controller.paths:
            finished.set()

    session = VoiceSession(recorder, controller, status)
    session.start_listening()
    assert session.state is RelayState.LISTENING
    session.stop_listening()
    assert finished.wait(2)
    assert controller.paths == [audio]
    assert not audio.exists()
    session.shutdown()
    assert session.state is RelayState.STOPPED


def test_pause_resume_and_cancel_leave_microphone_inactive(tmp_path: Path) -> None:
    recorder = FakeRecorder(tmp_path / "unused.wav")
    session = VoiceSession(recorder, FakeController())
    assert session.state is RelayState.IDLE
    assert not recorder.active

    session.start_listening()
    assert session.state is RelayState.LISTENING
    assert recorder.active

    session.pause()
    assert session.state is RelayState.PAUSED
    assert not recorder.active
    assert recorder.stopped == 1

    session.resume()
    assert session.state is RelayState.LISTENING
    assert recorder.active

    session.cancel()
    assert session.state is RelayState.IDLE
    assert not recorder.active
    session.shutdown()


def test_pause_transcribes_captured_audio_without_executing(tmp_path: Path) -> None:
    audio = tmp_path / "paused.wav"
    audio.write_bytes(b"audio")
    recorder = FakeRecorder(audio)
    events: list[PipelineEvent] = []
    transcribed = threading.Event()

    class PausePreviewController(FakeController):
        def preview_audio(self, audio_path: Path) -> str:
            assert audio_path == audio
            return "open WhatsApp"

    def on_event(event: PipelineEvent) -> None:
        events.append(event)
        if event.stage is RelayState.PAUSED and event.transcript:
            transcribed.set()

    controller = PausePreviewController()
    session = VoiceSession(recorder, controller, pipeline_listener=on_event)
    session.start_listening()
    session.pause()

    assert transcribed.wait(2)
    assert session.state is RelayState.PAUSED
    assert controller.paths == []
    deadline = time.monotonic() + 2
    while audio.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not audio.exists()
    assert any(
        event.stage is RelayState.PAUSED and event.transcript == "open WhatsApp" and event.partial
        for event in events
    )
    session.shutdown()


def test_voice_pipeline_state_sequence(tmp_path: Path) -> None:
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"audio")
    stages = [RelayState.IDLE]

    class PipelineController(FakeController):
        def handle_audio(self, audio_path: Path) -> ControllerResult:
            stages.extend((RelayState.DECIDING, RelayState.EXECUTING))
            return super().handle_audio(audio_path)

    controller = PipelineController()
    finished = threading.Event()

    def status(state: RelayState, detail: str) -> None:
        del detail
        stages.append(state)
        if state is RelayState.DONE:
            finished.set()

    session = VoiceSession(FakeRecorder(audio), controller, status)
    session.start_listening()
    session.stop_listening()
    assert finished.wait(2)
    assert stages == [
        RelayState.IDLE,
        RelayState.LISTENING,
        RelayState.TRANSCRIBING,
        RelayState.DECIDING,
        RelayState.EXECUTING,
        RelayState.DONE,
    ]
    session.shutdown()


def test_stable_preview_executes_and_final_transcript_does_not_repeat_action(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"audio")

    class PreviewRecorder(FakeRecorder):
        activity_revision = 1
        last_activity_at = time.monotonic()

        def snapshot(self) -> Path:
            preview = tmp_path / "preview.wav"
            preview.write_bytes(b"preview")
            return preview

    class PreviewController(FakeController):
        def __init__(self) -> None:
            super().__init__()
            self.commits = 0
            self.finalized = 0
            self.committed = threading.Event()

        def preview_audio(self, audio_path: Path) -> str:
            assert audio_path.name == "preview.wav"
            return "open Calculator"

        def prepare_text(self, text: str) -> PreparedAction:
            action = OpenApp(app_name="Calculator")
            return PreparedAction(
                text,
                DecisionResult(action, 0.97, "jev"),
                SafetyPolicy().assess(action),
                8.0,
                "Calculator",
                None,
            )

        def commit_prepared(
            self, prepared: PreparedAction, *, commit_id: str | None = None
        ) -> ControllerResult:
            assert commit_id is not None
            self.commits += 1
            self.committed.set()
            return ControllerResult(
                prepared.transcript,
                prepared.decision.action,
                ExecutionResult(True, "Opened Calculator"),
            )

        def finalize_committed_audio(self, audio_path: Path, commit_id: str) -> ControllerResult:
            assert audio_path == audio
            assert commit_id
            self.finalized += 1
            action = OpenApp(app_name="Calculator")
            return ControllerResult(
                "open Calculator",
                action,
                ExecutionResult(True, "Action already committed", {"duplicate": True}),
            )

    recorder = PreviewRecorder(audio)
    controller = PreviewController()
    events: list[PipelineEvent] = []
    session = VoiceSession(
        recorder,
        controller,
        pipeline_listener=events.append,
        stability_ms=10,
        preview_interval=0.01,
        utterance_silence_ms=5000,
    )
    session.start_listening()
    assert controller.committed.wait(2)
    deadline = time.monotonic() + 2
    while session.state is RelayState.LISTENING and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.state is RelayState.DONE
    assert controller.commits == 1
    assert controller.finalized == 1
    assert not recorder.active
    assert any(event.partial and event.transcript == "open Calculator" for event in events)
    session.shutdown()


def test_shutdown_cancels_active_microphone_capture(tmp_path: Path) -> None:
    recorder = FakeRecorder(tmp_path / "unused.wav")
    session = VoiceSession(recorder, FakeController())
    session.start_listening()
    session.shutdown()
    session.shutdown()
    assert recorder.cancelled == 1
    assert session.state is RelayState.STOPPED


def test_single_instance_lock_rejects_second_owner(tmp_path: Path) -> None:
    first = SingleInstanceLock(tmp_path / "relay.lock")
    second = SingleInstanceLock(tmp_path / "relay.lock")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_continuous_session_executes_sequential_commands_and_returns_to_listening(
    tmp_path: Path,
) -> None:
    class SequentialRecorder(FakeRecorder):
        def stop(self) -> Path:
            self.stopped += 1
            self.active = False
            path = tmp_path / f"utterance-{self.stopped}.wav"
            path.write_bytes(b"audio")
            return path

    recorder = SequentialRecorder(tmp_path / "unused.wav")
    controller = FakeController()
    listening_count = 0
    listening_again = threading.Event()

    def status(state: RelayState, detail: str) -> None:
        nonlocal listening_count
        del detail
        if state is RelayState.LISTENING:
            listening_count += 1
            if listening_count >= 3:
                listening_again.set()

    session = VoiceSession(recorder, controller, status, continuous=True)
    session.start_listening()
    session.stop_listening()
    deadline = time.monotonic() + 2
    while recorder.started < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.state is RelayState.LISTENING

    session.stop_listening()
    assert listening_again.wait(2)
    assert session.state is RelayState.LISTENING
    assert recorder.started == 3
    assert len(controller.paths) == 2
    session.shutdown()


def test_runtime_continuous_setting_pauses_and_resumes_capture(tmp_path: Path) -> None:
    audio = tmp_path / "paused.wav"
    audio.write_bytes(b"audio")
    recorder = FakeRecorder(audio)
    session = VoiceSession(recorder, FakeController(), continuous=True)
    session.start_listening()

    session.update_runtime_settings(
        continuous=False,
        stability_ms=450,
        silence_endpoint_ms=600,
        release_to_submit=True,
    )
    assert session.state is RelayState.IDLE
    assert not recorder.active

    session.update_runtime_settings(
        continuous=True,
        stability_ms=450,
        silence_endpoint_ms=600,
        release_to_submit=False,
    )
    assert session.state is RelayState.LISTENING
    assert recorder.active
    session.shutdown()


def test_continuous_mode_waits_for_silence_then_executes_and_listens_again(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "continuous-endpoint.wav"
    audio.write_bytes(b"audio")

    class ContinuousRecorder(FakeRecorder):
        activity_revision = 3
        last_activity_at = time.monotonic() - 1

        def snapshot(self) -> Path:
            preview = tmp_path / "continuous-preview.wav"
            preview.write_bytes(b"preview")
            return preview

    recorder = ContinuousRecorder(audio)
    controller = FakeController()
    listening_again = threading.Event()
    listening_count = 0

    def status(state: RelayState, detail: str) -> None:
        nonlocal listening_count
        del detail
        if state is RelayState.LISTENING:
            listening_count += 1
            if listening_count == 2:
                listening_again.set()

    session = VoiceSession(
        recorder,
        controller,
        status,
        continuous=True,
        release_to_submit=False,
        preview_interval=5,
        utterance_silence_ms=300,
    )
    session.start_listening()
    assert listening_again.wait(2)
    assert controller.paths == [audio]
    assert session.state is RelayState.LISTENING
    assert recorder.started == 2
    session.shutdown()


def test_short_silence_endpoint_submits_current_utterance(tmp_path: Path) -> None:
    audio = tmp_path / "endpoint.wav"
    audio.write_bytes(b"audio")

    class EndpointRecorder(FakeRecorder):
        activity_revision = 2
        last_activity_at = time.monotonic() - 1

        def snapshot(self) -> Path:
            preview = tmp_path / "preview.wav"
            preview.write_bytes(b"preview")
            return preview

    class EndpointController(FakeController):
        def preview_audio(self, audio_path: Path) -> str:
            return "open Calculator"

        def prepare_text(self, text: str) -> PreparedAction:
            action = OpenApp(app_name="Calculator")
            return PreparedAction(
                text,
                DecisionResult(action, 1.0, "fast_path"),
                SafetyPolicy().assess(action),
                1.0,
                "Calculator",
                None,
            )

        def commit_prepared(
            self, prepared: PreparedAction, *, commit_id: str | None = None
        ) -> ControllerResult:
            return ControllerResult(
                prepared.transcript,
                prepared.decision.action,
                ExecutionResult(True, "Opened Calculator"),
            )

        def finalize_committed_audio(self, audio_path: Path, commit_id: str) -> ControllerResult:
            return self.handle_audio(audio_path)

    controller = EndpointController()
    finished = threading.Event()

    def status(state: RelayState, detail: str) -> None:
        del detail
        if state is RelayState.DONE:
            finished.set()

    session = VoiceSession(
        EndpointRecorder(audio),
        controller,
        status,
        preview_interval=5,
        utterance_silence_ms=300,
    )
    session.start_listening()
    assert finished.wait(2)
    assert controller.paths == [audio]
    session.shutdown()

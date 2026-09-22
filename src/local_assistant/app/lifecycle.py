from __future__ import annotations

import fcntl
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import structlog

from local_assistant.app.early_commit import EarlyCommit, EarlyCommitCoordinator
from local_assistant.runtime.controller import PreparedAction
from local_assistant.runtime.types import ControllerResult, PipelineEvent, PipelineStage

RelayState = PipelineStage


class Recorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> Path: ...
    def cancel(self) -> None: ...


@runtime_checkable
class PreviewRecorder(Recorder, Protocol):
    @property
    def activity_revision(self) -> int: ...

    @property
    def last_activity_at(self) -> float: ...

    def snapshot(self) -> Path: ...


class AudioController(Protocol):
    def handle_audio(self, audio_path: Path) -> ControllerResult: ...


@runtime_checkable
class AudioPreviewController(AudioController, Protocol):
    def preview_audio(self, audio_path: Path) -> str: ...


@runtime_checkable
class PreviewController(AudioPreviewController, Protocol):
    def prepare_text(self, text: str) -> PreparedAction: ...
    def commit_prepared(
        self, prepared: PreparedAction, *, commit_id: str | None = None
    ) -> ControllerResult: ...
    def finalize_committed_audio(self, audio_path: Path, commit_id: str) -> ControllerResult: ...


class StatusListener(Protocol):
    def __call__(self, state: RelayState, detail: str) -> None: ...


class PipelineListener(Protocol):
    def __call__(self, event: PipelineEvent) -> None: ...


class VoiceSession:
    """Coordinates one explicit recording at a time around the shared controller."""

    def __init__(
        self,
        recorder: Recorder,
        controller: AudioController,
        listener: StatusListener | None = None,
        pipeline_listener: PipelineListener | None = None,
        *,
        early_confidence: float = 0.9,
        stability_ms: int = 350,
        preview_interval: float = 0.75,
        utterance_silence_ms: int = 900,
        continuous: bool = False,
        return_to_idle: bool = False,
        release_to_submit: bool = False,
    ) -> None:
        self._recorder = recorder
        self._controller = controller
        self._listener = listener or (lambda state, detail: None)
        self._pipeline_listener = pipeline_listener or (lambda event: None)
        self._early_confidence = early_confidence
        self._stability_ms = stability_ms
        self._preview_interval = preview_interval
        self._utterance_silence_seconds = utterance_silence_ms / 1000
        self._continuous = continuous
        self._return_to_idle = return_to_idle
        self._release_to_submit = release_to_submit
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="relay-runtime")
        self._lock = threading.Lock()
        self._state = RelayState.IDLE
        self._closed = False
        self._preview_stop = threading.Event()
        self._preview_thread: threading.Thread | None = None
        self._preview_pending = False
        self._early_commit_queued = False
        self._last_preview_key = ""
        self._last_prepared: PreparedAction | None = None
        self._early_coordinator: EarlyCommitCoordinator | None = None
        self._candidate_timer: threading.Timer | None = None
        self._candidate_generation = 0
        self._candidate_transcript = ""
        self._log = structlog.get_logger(__name__)

    @property
    def state(self) -> RelayState:
        with self._lock:
            return self._state

    def _set_state(self, state: RelayState, detail: str) -> None:
        with self._lock:
            self._state = state
        self._listener(state, detail)

    def start_listening(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Relay is shutting down")
            if self._state not in {
                RelayState.IDLE,
                RelayState.PAUSED,
                RelayState.DONE,
                RelayState.ERROR,
            }:
                raise RuntimeError(f"Cannot start listening while {self._state.value.lower()}")
        try:
            self._recorder.start()
        except Exception as exc:
            self._set_state(RelayState.ERROR, str(exc))
            raise
        self._set_state(RelayState.LISTENING, "Listening; release speech to submit")
        self._start_preview()

    def update_runtime_settings(
        self,
        *,
        continuous: bool,
        stability_ms: int,
        silence_endpoint_ms: int,
        release_to_submit: bool | None = None,
    ) -> None:
        with self._lock:
            was_continuous = self._continuous
            self._continuous = continuous
            if release_to_submit is not None:
                self._release_to_submit = release_to_submit
            self._stability_ms = stability_ms
            self._utterance_silence_seconds = silence_endpoint_ms / 1000
            state = self._state
        if continuous and state in {
            RelayState.IDLE,
            RelayState.PAUSED,
            RelayState.DONE,
            RelayState.ERROR,
        }:
            self.start_listening()
        elif was_continuous and not continuous and state is RelayState.LISTENING:
            self._stop_preview()
            self._close_early_coordinator()
            self._recorder.cancel()
            self._set_state(RelayState.IDLE, "Continuous listening disabled")

    def pause(self) -> None:
        if self.state is not RelayState.LISTENING:
            raise RuntimeError("Relay is not listening")
        self._stop_preview()
        self._close_early_coordinator()
        try:
            audio_path = self._recorder.stop()
        except Exception as exc:
            self._set_state(RelayState.ERROR, str(exc))
            raise
        self._set_state(RelayState.PAUSED, "Microphone paused")
        self._executor.submit(self._transcribe_paused, audio_path)

    def resume(self) -> None:
        if self.state is not RelayState.PAUSED:
            raise RuntimeError("Relay is not paused")
        self.start_listening()

    def cancel(self) -> None:
        state = self.state
        if state is RelayState.LISTENING:
            self._stop_preview()
            self._close_early_coordinator()
            self._recorder.cancel()
        elif state in {
            RelayState.TRANSCRIBING,
            RelayState.DECIDING,
            RelayState.EXECUTING,
        }:
            raise RuntimeError("Relay cannot safely cancel an action that has begun processing")
        self._set_state(RelayState.IDLE, "Ready")

    def stop_current(self) -> None:
        """Discard the current utterance while preserving continuous-listening mode."""
        if self.state is RelayState.LISTENING:
            self._stop_preview()
            self._close_early_coordinator()
            self._recorder.cancel()
            self._set_state(RelayState.IDLE, "Interaction stopped")
            self._restart_continuous()
            return
        self.cancel()
        self._restart_continuous()

    def stop_listening(self) -> None:
        if self.state is not RelayState.LISTENING:
            raise RuntimeError("Relay is not listening")
        self._stop_preview()
        self._close_early_coordinator()
        try:
            audio_path = self._recorder.stop()
        except Exception as exc:
            self._set_state(RelayState.ERROR, str(exc))
            raise
        self._set_state(RelayState.TRANSCRIBING, "Transcribing recorded speech")
        self._executor.submit(self._process_audio, audio_path)

    def _start_preview(self) -> None:
        if not isinstance(self._recorder, PreviewRecorder):
            return
        self._preview_stop = threading.Event()
        self._preview_pending = False
        self._early_commit_queued = False
        self._last_preview_key = ""
        self._last_prepared = None
        self._candidate_transcript = ""
        recorder = self._recorder
        if (
            not self._release_to_submit
            and not self._continuous
            and isinstance(self._controller, PreviewController)
        ):
            self._early_coordinator = EarlyCommitCoordinator(
                uuid.uuid4().hex,
                self._queue_early_commit,
                lambda: recorder.activity_revision,
                confidence_threshold=self._early_confidence,
                stability_ms=1,
            )
        preview_thread = threading.Thread(
            target=self._preview_loop,
            name="relay-live-transcript",
            daemon=True,
        )
        preview_thread.start()
        self._preview_thread = preview_thread

    def _preview_loop(self) -> None:
        next_preview = time.monotonic() + self._preview_interval
        while not self._preview_stop.wait(0.05):
            if self.state is not RelayState.LISTENING:
                return
            recorder = self._recorder
            if not isinstance(recorder, PreviewRecorder):
                return
            now = time.monotonic()
            with self._lock:
                preview_pending = self._preview_pending
                early_commit_queued = self._early_commit_queued
            if (
                not self._release_to_submit
                and recorder.last_activity_at > 0
                and now - recorder.last_activity_at >= self._utterance_silence_seconds
                and not preview_pending
                and not early_commit_queued
            ):
                self.stop_listening()
                return
            if now >= next_preview:
                self._queue_preview()
                next_preview = now + self._preview_interval

    def _queue_preview(self) -> None:
        with self._lock:
            if self._state is not RelayState.LISTENING or self._preview_pending:
                return
            self._preview_pending = True
        self._executor.submit(self._preview_once)

    def _preview_once(self) -> None:
        preview_path: Path | None = None
        try:
            recorder = self._recorder
            controller = self._controller
            if not isinstance(recorder, PreviewRecorder) or not isinstance(
                controller, PreviewController
            ):
                return
            revision = recorder.activity_revision
            preview_path = recorder.snapshot()
            transcript = controller.preview_audio(preview_path)
            if self.state is not RelayState.LISTENING:
                return
            self._pipeline_listener(
                PipelineEvent(PipelineStage.LISTENING, transcript=transcript, partial=True)
            )
            if not self._release_to_submit and not self._continuous:
                self._schedule_stable_candidate(transcript, revision)
        except Exception as exc:
            # Preview failure is recoverable; final transcription remains authoritative.
            self._log.warning("live_preview_failed", error=str(exc))
            if self.state is RelayState.LISTENING:
                self._pipeline_listener(
                    PipelineEvent(
                        PipelineStage.LISTENING,
                        partial=True,
                        preview_error=str(exc),
                    )
                )
            return
        finally:
            if preview_path is not None:
                preview_path.unlink(missing_ok=True)
            with self._lock:
                self._preview_pending = False

    def _schedule_stable_candidate(self, transcript: str, revision: int) -> None:
        normalized = " ".join(transcript.casefold().split())
        with self._lock:
            self._candidate_generation += 1
            generation = self._candidate_generation
            self._candidate_transcript = normalized
            previous, self._candidate_timer = self._candidate_timer, None
        if previous is not None:
            previous.cancel()

        def stable() -> None:
            recorder = self._recorder
            if (
                self.state is not RelayState.LISTENING
                or not isinstance(recorder, PreviewRecorder)
                or recorder.activity_revision != revision
            ):
                return
            with self._lock:
                if (
                    generation != self._candidate_generation
                    or normalized != self._candidate_transcript
                ):
                    return
            self._executor.submit(self._prepare_stable_candidate, transcript, revision)

        timer = threading.Timer(self._stability_ms / 1000, stable)
        timer.daemon = True
        with self._lock:
            if generation == self._candidate_generation:
                self._candidate_timer = timer
        timer.start()

    def _prepare_stable_candidate(self, transcript: str, revision: int) -> None:
        controller = self._controller
        recorder = self._recorder
        if (
            self.state is not RelayState.LISTENING
            or not isinstance(controller, PreviewController)
            or not isinstance(recorder, PreviewRecorder)
            or recorder.activity_revision != revision
        ):
            return
        preview_key = " ".join(transcript.casefold().split())
        if preview_key == self._last_preview_key and self._last_prepared is not None:
            prepared = self._last_prepared
        else:
            prepared = controller.prepare_text(transcript)
            self._last_preview_key = preview_key
            self._last_prepared = prepared
        coordinator = self._early_coordinator
        if coordinator is not None:
            coordinator.observe(prepared, revision)

    def _transcribe_paused(self, audio_path: Path) -> None:
        try:
            controller = self._controller
            if not isinstance(controller, AudioPreviewController):
                return
            transcript = controller.preview_audio(audio_path)
            if self.state is RelayState.PAUSED:
                self._pipeline_listener(
                    PipelineEvent(PipelineStage.PAUSED, transcript=transcript, partial=True)
                )
        except Exception as exc:
            self._log.warning("paused_transcription_failed", error=str(exc))
            if self.state is RelayState.PAUSED:
                self._pipeline_listener(
                    PipelineEvent(
                        PipelineStage.PAUSED,
                        partial=True,
                        preview_error=str(exc),
                    )
                )
        finally:
            audio_path.unlink(missing_ok=True)

    def _queue_early_commit(self, commit: EarlyCommit) -> None:
        with self._lock:
            if self._closed or self._state is not RelayState.LISTENING:
                return
            self._early_commit_queued = True
        self._executor.submit(self._commit_early, commit)

    def _commit_early(self, commit: EarlyCommit) -> None:
        controller = self._controller
        if not isinstance(controller, PreviewController) or self.state is not RelayState.LISTENING:
            return
        try:
            result = controller.commit_prepared(commit.prepared, commit_id=commit.commit_id)
        except Exception as exc:
            self._stop_preview()
            self._close_early_coordinator()
            self._recorder.cancel()
            self._set_state(RelayState.ERROR, str(exc))
            return
        final_audio: Path | None = None
        if self.state is RelayState.LISTENING:
            self._stop_preview()
            self._close_early_coordinator()
            try:
                final_audio = self._recorder.stop()
                controller.finalize_committed_audio(final_audio, commit.commit_id)
            except Exception:
                # The low-risk action already completed; finalization must not repeat it.
                pass
            finally:
                if final_audio is not None:
                    final_audio.unlink(missing_ok=True)
        state = RelayState.DONE if result.execution.success else RelayState.ERROR
        self._set_state(state, result.execution.message)
        self._complete_cycle()

    def _stop_preview(self) -> None:
        self._preview_stop.set()
        with self._lock:
            candidate, self._candidate_timer = self._candidate_timer, None
            self._candidate_generation += 1
        if candidate is not None:
            candidate.cancel()
        thread, self._preview_thread = self._preview_thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def _close_early_coordinator(self) -> None:
        coordinator, self._early_coordinator = self._early_coordinator, None
        if coordinator is not None:
            coordinator.close()

    def _process_audio(self, audio_path: Path) -> None:
        try:
            result = self._controller.handle_audio(audio_path)
            detail = result.execution.message
            state = RelayState.DONE if result.execution.success else RelayState.ERROR
        except Exception as exc:
            state, detail = RelayState.ERROR, str(exc)
        finally:
            audio_path.unlink(missing_ok=True)
        self._set_state(state, detail)
        self._complete_cycle()

    def _complete_cycle(self) -> None:
        self._restart_continuous()
        with self._lock:
            should_idle = (
                self._return_to_idle
                and not self._continuous
                and not self._closed
                and self._state in {RelayState.DONE, RelayState.ERROR}
            )
        if should_idle:
            self._set_state(RelayState.IDLE, "Ready")

    def _restart_continuous(self) -> None:
        with self._lock:
            restart = (
                self._continuous
                and not self._closed
                and self._state
                in {
                    RelayState.IDLE,
                    RelayState.DONE,
                    RelayState.ERROR,
                }
            )
        if restart:
            try:
                self.start_listening()
            except Exception as exc:
                self._set_state(RelayState.ERROR, str(exc))

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            was_listening = self._state is RelayState.LISTENING
        if was_listening:
            self._stop_preview()
            self._close_early_coordinator()
            self._recorder.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._set_state(RelayState.STOPPED, "Relay stopped")


@dataclass(slots=True)
class SingleInstanceLock:
    path: Path
    _descriptor: int | None = None

    def acquire(self) -> bool:
        if self._descriptor is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return False
        os.ftruncate(descriptor, 0)
        os.write(descriptor, str(os.getpid()).encode())
        self._descriptor = descriptor
        return True

    def release(self) -> None:
        if self._descriptor is None:
            return
        descriptor, self._descriptor = self._descriptor, None
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    def __enter__(self) -> SingleInstanceLock:
        if not self.acquire():
            raise RuntimeError("Relay is already running")
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

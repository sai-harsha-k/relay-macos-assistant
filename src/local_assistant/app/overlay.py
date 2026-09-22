from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from local_assistant.runtime.types import PipelineEvent, PipelineStage


class Cancellable(Protocol):
    def cancel(self) -> None: ...


class Scheduler(Protocol):
    def __call__(self, delay: float, callback: Callable[[], None]) -> Cancellable: ...


def _schedule_timer(delay: float, callback: Callable[[], None]) -> Cancellable:
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return timer


def display_model_name(model: str) -> str:
    name, separator, size = model.partition(":")
    if name.casefold().startswith("qwen"):
        name = "Qwen" + name[4:]
    return f"{name} {size.upper()}" if separator else name


def clamp_overlay_origin(
    x: float,
    y: float,
    panel_width: float,
    panel_height: float,
    screens: tuple[tuple[float, float, float, float], ...],
    *,
    margin: float = 8,
) -> tuple[float, float]:
    """Keep the complete panel inside the nearest available visible screen."""
    if not screens:
        return x, y
    center_x = x + panel_width / 2
    center_y = y + panel_height / 2
    screen = next(
        (
            candidate
            for candidate in screens
            if candidate[0] <= center_x <= candidate[0] + candidate[2]
            and candidate[1] <= center_y <= candidate[1] + candidate[3]
        ),
        screens[0],
    )
    screen_x, screen_y, screen_width, screen_height = screen
    minimum_x = screen_x + margin
    minimum_y = screen_y + margin
    maximum_x = max(minimum_x, screen_x + screen_width - panel_width - margin)
    maximum_y = max(minimum_y, screen_y + screen_height - panel_height - margin)
    return min(max(x, minimum_x), maximum_x), min(max(y, minimum_y), maximum_y)


@dataclass(frozen=True, slots=True)
class OverlaySnapshot:
    stage: PipelineStage = PipelineStage.IDLE
    expanded: bool = False
    transcript: str = ""
    route_source: str | None = None
    confidence: float | None = None
    action: str | None = None
    action_target: str | None = None
    timings_ms: dict[str, float] | None = None
    result: str | None = None
    error: str | None = None
    writer_model: str | None = None
    transcript_is_partial: bool = False
    audio_level: float = 0.0
    preview_error: str | None = None

    @property
    def status_text(self) -> str:
        return {
            PipelineStage.IDLE: "Ready",
            PipelineStage.LISTENING: "🎙 Listening…",
            PipelineStage.PAUSED: "Paused",
            PipelineStage.TRANSCRIBING: "Transcribing…",
            PipelineStage.DECIDING: "Deciding…",
            PipelineStage.EXECUTING: "Executing…",
            PipelineStage.DONE: "✓ Done",
            PipelineStage.ERROR: "⚠ Error",
            PipelineStage.STOPPED: "Stopped",
        }[self.stage]

    @property
    def route_label(self) -> str | None:
        if self.writer_model:
            if self.route_source == "jev":
                return "Jev → Local LLM"
            if self.route_source == "fast_path":
                return "Fast Path → Local LLM"
            return "Local LLM"
        if self.route_source == "fast_path":
            return "Fast Path"
        if self.route_source in {"jev", "confidence_gate"}:
            return "Jev"
        if self.route_source == "fallback":
            return "Jev unavailable"
        return self.route_source

    @property
    def confidence_label(self) -> str | None:
        if self.route_source == "fast_path":
            return "Exact match"
        if self.confidence is not None and self.route_source in {"jev", "confidence_gate"}:
            return f"Confidence: {self.confidence:.0%}"
        return None

    @property
    def model_label(self) -> str | None:
        return display_model_name(self.writer_model) if self.writer_model else None


class OverlayPresenter:
    """Thread-safe, UI-independent state for the native floating panel."""

    def __init__(
        self,
        listener: Callable[[OverlaySnapshot], None] | None = None,
        *,
        scheduler: Scheduler = _schedule_timer,
        collapse_delay: float = 2.5,
    ) -> None:
        self._listener = listener
        self._scheduler = scheduler
        self._collapse_delay = collapse_delay
        self._snapshot = OverlaySnapshot()
        self._collapse_call: Cancellable | None = None
        self._interacting = False
        self._lock = threading.Lock()
        del scheduler, collapse_delay

    @property
    def snapshot(self) -> OverlaySnapshot:
        with self._lock:
            return self._snapshot

    def set_listener(self, listener: Callable[[OverlaySnapshot], None]) -> None:
        with self._lock:
            self._listener = listener
            snapshot = self._snapshot
        listener(snapshot)

    def _publish(self, snapshot: OverlaySnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot
            listener = self._listener
        if listener is not None:
            listener(snapshot)

    def _cancel_collapse(self) -> None:
        with self._lock:
            collapse_call, self._collapse_call = self._collapse_call, None
        if collapse_call is not None:
            collapse_call.cancel()

    def handle_stage(self, stage: PipelineStage, detail: str = "") -> None:
        if stage is PipelineStage.DONE:
            current = self.snapshot
            if current.stage is PipelineStage.DONE:
                self._publish(replace(current, result=current.result or detail))
            else:
                self.handle_event(PipelineEvent(PipelineStage.DONE, result=detail))
            return
        self._cancel_collapse()
        current = self.snapshot
        if stage is PipelineStage.LISTENING or stage is PipelineStage.IDLE:
            snapshot = replace(
                current,
                stage=stage,
                audio_level=0.0,
                preview_error=None,
            )
        else:
            snapshot = replace(
                current,
                stage=stage,
                error=detail if stage is PipelineStage.ERROR else current.error,
            )
        self._publish(snapshot)

    def speech_activity(self, level: float = 1.0) -> None:
        current = self.snapshot
        if current.stage is PipelineStage.LISTENING:
            self._publish(replace(current, audio_level=max(0.0, min(1.0, level))))

    def handle_event(self, event: PipelineEvent) -> None:
        self._cancel_collapse()
        current = self.snapshot
        preview_error = current.preview_error
        if event.preview_error is not None:
            preview_error = event.preview_error
        elif event.transcript is not None or event.stage not in {
            PipelineStage.LISTENING,
            PipelineStage.PAUSED,
        }:
            preview_error = None
        new_utterance = event.transcript is not None and (
            (event.stage is PipelineStage.LISTENING and event.partial)
            or event.stage is PipelineStage.DECIDING
        )
        snapshot = replace(
            current,
            stage=event.stage,
            transcript=event.transcript if event.transcript is not None else current.transcript,
            route_source=(
                event.route_source
                if event.route_source is not None
                else None
                if new_utterance
                else current.route_source
            ),
            confidence=(
                event.confidence
                if event.confidence is not None
                else None
                if new_utterance
                else current.confidence
            ),
            action=(
                event.action.kind.value
                if event.action is not None
                else None
                if new_utterance
                else current.action
            ),
            action_target=(
                event.action_target
                if event.action_target is not None
                else None
                if new_utterance
                else current.action_target
            ),
            timings_ms=(
                dict(event.timings_ms)
                if event.timings_ms
                else None
                if new_utterance
                else current.timings_ms
            ),
            result=(
                event.result
                if event.result is not None
                else None
                if new_utterance
                else current.result
            ),
            error=(
                event.error if event.error is not None else None if new_utterance else current.error
            ),
            writer_model=(
                event.writer_model
                if event.writer_model is not None
                else None
                if new_utterance
                else current.writer_model
            ),
            transcript_is_partial=event.partial,
            audio_level=current.audio_level if event.stage is PipelineStage.LISTENING else 0.0,
            preview_error=preview_error,
        )
        self._publish(snapshot)

    def _auto_collapse(self) -> None:
        with self._lock:
            current = self._snapshot
            should_collapse = current.stage is PipelineStage.DONE and not self._interacting
            if should_collapse:
                self._snapshot = replace(current, expanded=False)
            listener = self._listener
            snapshot = self._snapshot
            self._collapse_call = None
        if should_collapse and listener is not None:
            listener(snapshot)

    def set_interacting(self, interacting: bool) -> None:
        self._interacting = interacting

    def expand(self) -> None:
        current = self.snapshot
        if not current.expanded:
            self._publish(replace(current, expanded=True))

    def collapse(self) -> None:
        current = self.snapshot
        if current.expanded:
            self._publish(replace(current, expanded=False))

    def close(self) -> None:
        self._cancel_collapse()

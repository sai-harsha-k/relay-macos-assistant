from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from local_assistant.actions.models import GenerateText, OpenApp
from local_assistant.app.overlay import OverlayPresenter, clamp_overlay_origin
from local_assistant.runtime.types import PipelineEvent, PipelineStage


@dataclass
class PendingCall:
    callback: Callable[[], None]
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if not self.cancelled:
            self.callback()


class FakeScheduler:
    def __init__(self) -> None:
        self.delay = 0.0
        self.pending: PendingCall | None = None

    def __call__(self, delay: float, callback: Callable[[], None]) -> PendingCall:
        self.delay = delay
        self.pending = PendingCall(callback)
        return self.pending


def test_saved_overlay_position_is_clamped_to_visible_screen() -> None:
    screens = ((0.0, 25.0, 1440.0, 875.0),)
    assert clamp_overlay_origin(1600, -500, 430, 294, screens) == (1002.0, 33.0)
    assert clamp_overlay_origin(100, 100, 430, 294, screens) == (100, 100)


def test_overlay_stays_compact_during_activity_until_user_expands_it() -> None:
    presenter = OverlayPresenter()
    assert not presenter.snapshot.expanded
    presenter.handle_stage(PipelineStage.LISTENING)
    assert not presenter.snapshot.expanded

    presenter.speech_activity()
    assert not presenter.snapshot.expanded

    presenter.expand()
    assert presenter.snapshot.expanded
    presenter.handle_event(PipelineEvent(PipelineStage.TRANSCRIBING))
    assert presenter.snapshot.expanded

    presenter.collapse()
    assert not presenter.snapshot.expanded


def test_done_and_error_never_resize_overlay() -> None:
    scheduler = FakeScheduler()
    presenter = OverlayPresenter(scheduler=scheduler)
    presenter.handle_event(PipelineEvent(PipelineStage.DONE, result="Opened Calculator"))
    assert not presenter.snapshot.expanded
    assert scheduler.pending is None

    presenter.handle_event(PipelineEvent(PipelineStage.ERROR, error="Failed"))
    assert not presenter.snapshot.expanded


def test_completed_result_remains_visible_until_next_utterance() -> None:
    presenter = OverlayPresenter()
    presenter.handle_event(PipelineEvent(PipelineStage.DONE, result="Opened WhatsApp"))
    presenter.handle_stage(PipelineStage.LISTENING)
    assert presenter.snapshot.result == "Opened WhatsApp"

    presenter.handle_event(
        PipelineEvent(PipelineStage.LISTENING, transcript="open chrome", partial=True)
    )
    assert presenter.snapshot.result is None


def test_full_screenshot_path_is_available_to_expanded_overlay() -> None:
    saved_path = "/Users/example/Pictures/Relay Screenshots/Screenshot 2026-09-22 at 12.34.56.png"
    presenter = OverlayPresenter()
    presenter.handle_event(
        PipelineEvent(PipelineStage.DONE, result=f"Saved screenshot to {saved_path}")
    )
    presenter.expand()

    assert presenter.snapshot.expanded
    assert presenter.snapshot.result == f"Saved screenshot to {saved_path}"


def test_jev_route_confidence_and_action_are_presented() -> None:
    presenter = OverlayPresenter()
    presenter.handle_event(
        PipelineEvent(
            PipelineStage.EXECUTING,
            transcript="could you open calculator for me",
            route_source="jev",
            confidence=0.94,
            action=OpenApp(app_name="Calculator"),
            action_target="Calculator",
        )
    )
    snapshot = presenter.snapshot
    assert snapshot.transcript == "could you open calculator for me"
    assert snapshot.route_label == "Jev"
    assert snapshot.confidence_label == "Confidence: 94%"
    assert snapshot.action == "OPEN_APP"
    assert snapshot.action_target == "Calculator"


def test_fast_path_uses_exact_match_instead_of_confidence() -> None:
    presenter = OverlayPresenter()
    presenter.handle_event(
        PipelineEvent(
            PipelineStage.EXECUTING,
            transcript="open calculator",
            route_source="fast_path",
            confidence=1.0,
            action=OpenApp(app_name="Calculator"),
            action_target="Calculator",
        )
    )
    assert presenter.snapshot.route_label == "Fast Path"
    assert presenter.snapshot.confidence_label == "Exact match"


def test_local_writer_model_is_exposed() -> None:
    presenter = OverlayPresenter()
    presenter.handle_event(
        PipelineEvent(
            PipelineStage.EXECUTING,
            transcript="write a short note",
            route_source="jev",
            confidence=0.91,
            action=GenerateText(instruction="a short note"),
            action_target="a short note",
            writer_model="qwen3.5:2b",
        )
    )
    assert presenter.snapshot.route_label == "Jev → Local LLM"
    assert presenter.snapshot.model_label == "Qwen3.5 2B"
    assert presenter.snapshot.confidence_label == "Confidence: 91%"


def test_new_final_utterance_clears_stale_writer_route() -> None:
    presenter = OverlayPresenter()
    presenter.handle_event(
        PipelineEvent(
            PipelineStage.DONE,
            transcript="write a short note",
            route_source="jev",
            action=GenerateText(instruction="write a short note"),
            writer_model="qwen3.5:2b",
        )
    )
    presenter.handle_event(
        PipelineEvent(
            PipelineStage.DECIDING,
            transcript="open Safari and search for YouTube",
        )
    )
    presenter.handle_event(
        PipelineEvent(
            PipelineStage.EXECUTING,
            transcript="open Safari and search for YouTube",
            route_source="fast_path",
            action=OpenApp(app_name="Safari"),
        )
    )
    assert presenter.snapshot.route_label == "Fast Path"
    assert presenter.snapshot.writer_model is None


def test_fast_path_writer_route_is_labeled_without_fake_confidence() -> None:
    presenter = OverlayPresenter()
    presenter.handle_event(
        PipelineEvent(
            PipelineStage.EXECUTING,
            transcript="open notes and write a short reminder",
            route_source="fast_path",
            action=GenerateText(instruction="Write a short reminder", type_after_generation=True),
            writer_model="qwen3.5:2b",
        )
    )
    assert presenter.snapshot.route_label == "Fast Path → Local LLM"
    assert presenter.snapshot.confidence_label == "Exact match"


def test_live_preview_is_distinct_from_authoritative_final_transcript() -> None:
    presenter = OverlayPresenter()
    presenter.handle_stage(PipelineStage.LISTENING)
    presenter.handle_event(
        PipelineEvent(
            PipelineStage.LISTENING,
            transcript="open calculater",
            partial=True,
        )
    )
    assert presenter.snapshot.transcript == "open calculater"
    assert presenter.snapshot.transcript_is_partial

    presenter.handle_event(
        PipelineEvent(
            PipelineStage.DECIDING,
            transcript="open calculator",
        )
    )
    assert presenter.snapshot.transcript == "open calculator"
    assert not presenter.snapshot.transcript_is_partial


def test_live_preview_error_is_visible_and_clears_after_recovery() -> None:
    presenter = OverlayPresenter()
    presenter.handle_event(
        PipelineEvent(
            PipelineStage.LISTENING,
            partial=True,
            preview_error="Whisper preview failed",
        )
    )
    assert presenter.snapshot.preview_error == "Whisper preview failed"

    presenter.handle_event(
        PipelineEvent(
            PipelineStage.LISTENING,
            transcript="open calculator",
            partial=True,
        )
    )
    assert presenter.snapshot.preview_error is None

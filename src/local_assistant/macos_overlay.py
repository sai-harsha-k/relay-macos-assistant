from __future__ import annotations

from collections.abc import Callable
from typing import Any

import objc  # type: ignore[import-untyped]
from AppKit import (  # type: ignore[import-untyped]
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSButton,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSLineBreakByTruncatingTail,
    NSMakePoint,
    NSMakeRect,
    NSObject,
    NSPanel,
    NSScreen,
    NSTextField,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
)

from local_assistant.app.overlay import OverlayPresenter, OverlaySnapshot, clamp_overlay_origin
from local_assistant.runtime.types import PipelineStage

_COMPACT_WIDTH = 430.0
_COMPACT_HEIGHT = 294.0
_EXPANDED_WIDTH = 620.0
_EXPANDED_HEIGHT = 500.0


def _label(text: str, frame: tuple[float, float, float, float], size: float) -> Any:
    label = NSTextField.labelWithString_(text)
    label.setFrame_(NSMakeRect(*frame))
    label.setFont_(NSFont.systemFontOfSize_(size))
    label.setTextColor_(NSColor.labelColor())
    label.setLineBreakMode_(NSLineBreakByTruncatingTail)
    return label


def _button(title: str, frame: tuple[float, float, float, float], target: Any, action: str) -> Any:
    button = NSButton.alloc().initWithFrame_(NSMakeRect(*frame))
    button.setTitle_(title)
    button.setBezelStyle_(NSBezelStyleRounded)
    button.setTarget_(target)
    button.setAction_(action)
    return button


class RelayOverlayWindow(NSObject):  # type: ignore[misc]
    """Small native AppKit panel; all runtime work remains outside this class."""

    def initWithPresenter_callbacks_preferences_(
        self,
        presenter: OverlayPresenter,
        callbacks: tuple[
            Callable[[], None],
            Callable[[], None],
            Callable[[], None],
            Callable[[], None],
        ],
        preferences: tuple[bool, float | None, float | None],
    ) -> RelayOverlayWindow:
        self = objc.super(RelayOverlayWindow, self).init()
        if self is None:
            raise RuntimeError("Could not initialize Relay overlay")
        self._presenter = presenter
        self._on_listen, self._on_pause, self._on_stop, self._on_settings = callbacks
        always_on_top, saved_x, saved_y = preferences

        visible = NSScreen.mainScreen().visibleFrame()
        origin_x = (
            saved_x
            if saved_x is not None
            else visible.origin.x + visible.size.width - _COMPACT_WIDTH - 22
        )
        origin_y = (
            saved_y
            if saved_y is not None
            else visible.origin.y + visible.size.height - _COMPACT_HEIGHT - 22
        )
        origin_x, origin_y = self._visible_origin(
            origin_x, origin_y, _COMPACT_WIDTH, _COMPACT_HEIGHT
        )
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(origin_x, origin_y, _COMPACT_WIDTH, _COMPACT_HEIGHT),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        self._panel.setLevel_(NSFloatingWindowLevel if always_on_top else 0)
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(NSColor.clearColor())
        self._panel.setHasShadow_(True)
        self._panel.setHidesOnDeactivate_(False)
        self._panel.setMovableByWindowBackground_(True)
        self._panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        self._effect = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, _COMPACT_WIDTH, _COMPACT_HEIGHT)
        )
        self._effect.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self._effect.setMaterial_(NSVisualEffectMaterialHUDWindow)
        self._effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        self._effect.setState_(NSVisualEffectStateActive)
        self._effect.setWantsLayer_(True)
        self._effect.layer().setCornerRadius_(16.0)
        self._effect.layer().setMasksToBounds_(True)
        self._panel.setContentView_(self._effect)

        self._brand = _label("◉  Relay", (16, 259, 92, 22), 14)
        self._brand.setFont_(NSFont.boldSystemFontOfSize_(14))
        self._status = _label("Ready", (108, 259, 230, 22), 13)
        self._meter = _label("", (348, 259, 44, 22), 11)
        self._meter.setTextColor_(NSColor.systemGreenColor())
        self._toggle_button = _button("⌄", (396, 256, 24, 26), self, "toggleClicked:")
        self._transcript = _label("", (18, 198, 394, 48), 15)
        self._transcript.setMaximumNumberOfLines_(2)
        self._transcript.setLineBreakMode_(0)
        self._whisper = _label("Whisper", (18, 170, 394, 20), 12)
        self._route = _label("", (18, 142, 394, 22), 13)
        self._confidence = _label("", (18, 118, 394, 20), 12)
        self._action = _label("", (18, 88, 394, 22), 13)
        self._timings = _label("", (18, 62, 394, 20), 11)
        self._result = _label("", (18, 38, 394, 20), 11)
        self._pause_button = _button("Pause", (18, 8, 92, 28), self, "pauseClicked:")
        self._stop_button = _button("Stop", (116, 8, 92, 28), self, "stopClicked:")
        self._hide_button = _button("Hide", (214, 8, 92, 28), self, "hideClicked:")
        self._settings_button = _button("Settings", (312, 8, 100, 28), self, "settingsClicked:")

        self._detail_views = (
            self._transcript,
            self._whisper,
            self._route,
            self._confidence,
            self._action,
            self._timings,
            self._result,
            self._pause_button,
            self._stop_button,
            self._hide_button,
            self._settings_button,
        )
        for view in (
            self._brand,
            self._status,
            self._meter,
            self._toggle_button,
            *self._detail_views,
        ):
            self._effect.addSubview_(view)
        self._expanded = False
        return self

    @objc.python_method  # type: ignore[untyped-decorator]
    def show(self) -> None:
        frame = self._panel.frame()
        origin = self._panel.frame().origin
        x, y = self._visible_origin(
            float(origin.x),
            float(origin.y),
            float(frame.size.width),
            float(frame.size.height),
        )
        self._panel.setFrameOrigin_(NSMakePoint(x, y))
        self._panel.orderFrontRegardless()

    @objc.python_method  # type: ignore[untyped-decorator]
    def _visible_origin(
        self, x: float, y: float, width: float, height: float
    ) -> tuple[float, float]:
        frames = tuple(
            (
                float(screen.visibleFrame().origin.x),
                float(screen.visibleFrame().origin.y),
                float(screen.visibleFrame().size.width),
                float(screen.visibleFrame().size.height),
            )
            for screen in NSScreen.screens()
        )
        return clamp_overlay_origin(x, y, width, height, frames)

    @objc.python_method  # type: ignore[untyped-decorator]
    def _apply_layout(self, expanded: bool) -> None:
        if self._expanded == expanded:
            return
        old_frame = self._panel.frame()
        width = _EXPANDED_WIDTH if expanded else _COMPACT_WIDTH
        height = _EXPANDED_HEIGHT if expanded else _COMPACT_HEIGHT
        origin_x = float(old_frame.origin.x) + float(old_frame.size.width) - width
        origin_y = float(old_frame.origin.y) + float(old_frame.size.height) - height
        origin_x, origin_y = self._visible_origin(origin_x, origin_y, width, height)
        self._panel.setFrame_display_animate_(
            NSMakeRect(origin_x, origin_y, width, height), True, True
        )
        content_width = width - 36
        if expanded:
            self._brand.setFrame_(NSMakeRect(16, 465, 92, 22))
            self._status.setFrame_(NSMakeRect(108, 465, 380, 22))
            self._meter.setFrame_(NSMakeRect(520, 465, 44, 22))
            self._toggle_button.setFrame_(NSMakeRect(582, 462, 28, 26))
            self._transcript.setFrame_(NSMakeRect(18, 344, content_width, 100))
            self._transcript.setMaximumNumberOfLines_(5)
            self._whisper.setFrame_(NSMakeRect(18, 312, content_width, 20))
            self._route.setFrame_(NSMakeRect(18, 282, content_width, 22))
            self._confidence.setFrame_(NSMakeRect(18, 254, content_width, 20))
            self._action.setFrame_(NSMakeRect(18, 210, content_width, 36))
            self._action.setMaximumNumberOfLines_(2)
            self._action.setLineBreakMode_(0)
            self._timings.setFrame_(NSMakeRect(18, 180, content_width, 20))
            self._result.setFrame_(NSMakeRect(18, 52, content_width, 118))
            self._result.setMaximumNumberOfLines_(6)
            self._result.setLineBreakMode_(0)
        else:
            self._brand.setFrame_(NSMakeRect(16, 259, 92, 22))
            self._status.setFrame_(NSMakeRect(108, 259, 230, 22))
            self._meter.setFrame_(NSMakeRect(348, 259, 44, 22))
            self._toggle_button.setFrame_(NSMakeRect(396, 256, 24, 26))
            self._transcript.setFrame_(NSMakeRect(18, 198, content_width, 48))
            self._transcript.setMaximumNumberOfLines_(2)
            self._whisper.setFrame_(NSMakeRect(18, 170, content_width, 20))
            self._route.setFrame_(NSMakeRect(18, 142, content_width, 22))
            self._confidence.setFrame_(NSMakeRect(18, 118, content_width, 20))
            self._action.setFrame_(NSMakeRect(18, 88, content_width, 22))
            self._action.setMaximumNumberOfLines_(1)
            self._action.setLineBreakMode_(NSLineBreakByTruncatingTail)
            self._timings.setFrame_(NSMakeRect(18, 62, content_width, 20))
            self._result.setFrame_(NSMakeRect(18, 38, content_width, 20))
            self._result.setMaximumNumberOfLines_(1)
            self._result.setLineBreakMode_(NSLineBreakByTruncatingTail)
        self._toggle_button.setTitle_("⌃" if expanded else "⌄")
        self._expanded = expanded

    @objc.python_method  # type: ignore[untyped-decorator]
    def close(self) -> None:
        self._panel.orderOut_(None)
        self._panel.close()

    @objc.python_method  # type: ignore[untyped-decorator]
    def position(self) -> tuple[float, float]:
        origin = self._panel.frame().origin
        return float(origin.x), float(origin.y)

    @objc.python_method  # type: ignore[untyped-decorator]
    def set_always_on_top(self, enabled: bool) -> None:
        self._panel.setLevel_(NSFloatingWindowLevel if enabled else 0)

    @objc.python_method  # type: ignore[untyped-decorator]
    def render(self, snapshot: OverlaySnapshot) -> None:
        self._apply_layout(snapshot.expanded)
        self._status.setStringValue_(snapshot.status_text)
        if snapshot.transcript:
            self._transcript.setStringValue_(f"“{snapshot.transcript}”")
        elif snapshot.stage is PipelineStage.LISTENING:
            self._transcript.setStringValue_("Speak normally — Relay submits after a short silence")
        else:
            self._transcript.setStringValue_("")
        self._transcript.setTextColor_(
            NSColor.secondaryLabelColor()
            if snapshot.transcript_is_partial
            else NSColor.labelColor()
        )
        if snapshot.preview_error:
            whisper_status = "Whisper · preview retrying"
        elif snapshot.stage is PipelineStage.PAUSED and snapshot.transcript:
            whisper_status = "Whisper · paused capture"
        elif snapshot.transcript_is_partial:
            whisper_status = "Whisper · live preview"
        elif snapshot.transcript:
            whisper_status = "Whisper · final ✓"
        else:
            whisper_status = "Whisper · listening"
        self._whisper.setStringValue_(whisper_status)
        bars = min(5, max(0, round(snapshot.audio_level * 5)))
        self._meter.setStringValue_("▮" * bars + "·" * (5 - bars) if bars else "")
        route = snapshot.route_label or ""
        if snapshot.model_label:
            route = f"{route} · {snapshot.model_label}"
        self._route.setStringValue_(route)
        self._confidence.setStringValue_(snapshot.confidence_label or "")
        action = snapshot.action or ""
        if action and snapshot.action_target:
            action = f"{action} → {snapshot.action_target}"
        self._action.setStringValue_(action)
        self._timings.setStringValue_(self._format_timings(snapshot.timings_ms or {}))
        self._result.setStringValue_(
            snapshot.error or snapshot.result or snapshot.preview_error or ""
        )

        self._pause_button.setTitle_(
            "Resume" if snapshot.stage is PipelineStage.PAUSED else "Pause"
        )
        self._pause_button.setEnabled_(
            snapshot.stage in {PipelineStage.LISTENING, PipelineStage.PAUSED}
        )
        self._stop_button.setEnabled_(
            snapshot.stage in {PipelineStage.LISTENING, PipelineStage.PAUSED}
        )
        self._stop_button.setTitle_("Stop")

    @objc.python_method  # type: ignore[untyped-decorator]
    def _format_timings(self, timings: dict[str, float]) -> str:
        labels = {
            "transcription": "transcription",
            "decision": "decision",
            "execution": "execution",
            "end_to_end": "end-to-end",
        }
        return "  ·  ".join(
            f"{value:.0f} ms {labels[key]}" for key, value in timings.items() if key in labels
        )

    @objc.python_method  # type: ignore[untyped-decorator]
    def pauseClicked_(self, _sender: Any) -> None:
        self._presenter.set_interacting(True)
        try:
            if self._presenter.snapshot.stage is PipelineStage.PAUSED:
                self._on_listen()
            else:
                self._on_pause()
        finally:
            self._presenter.set_interacting(False)

    def stopClicked_(self, _sender: Any) -> None:
        self._presenter.set_interacting(True)
        try:
            self._on_stop()
        finally:
            self._presenter.set_interacting(False)

    def hideClicked_(self, _sender: Any) -> None:
        self._panel.orderOut_(None)

    def settingsClicked_(self, _sender: Any) -> None:
        self._on_settings()

    def toggleClicked_(self, _sender: Any) -> None:
        self._presenter.set_interacting(True)
        try:
            if self._presenter.snapshot.expanded:
                self._presenter.collapse()
            else:
                self._presenter.expand()
        finally:
            self._presenter.set_interacting(False)

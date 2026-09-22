from __future__ import annotations

from collections.abc import Callable
from typing import Any

import objc  # type: ignore[import-untyped]
from AppKit import (  # type: ignore[import-untyped]
    NSBackingStoreBuffered,
    NSBeep,
    NSBezelStyleRounded,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSFont,
    NSMakeRect,
    NSObject,
    NSOnState,
    NSPanel,
    NSTextField,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from pydantic import ValidationError

from local_assistant.app.hotkey import HotkeySpec
from local_assistant.app.preferences import RelayPreferences


def _label(text: str, x: float, y: float, width: float = 210) -> Any:
    label = NSTextField.labelWithString_(text)
    label.setFrame_(NSMakeRect(x, y, width, 22))
    return label


def _field(value: str, x: float, y: float, width: float = 120) -> Any:
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, 24))
    field.setStringValue_(value)
    return field


def _toggle(title: str, enabled: bool, x: float, y: float) -> Any:
    button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, 280, 24))
    button.setButtonType_(NSButtonTypeSwitch)
    button.setTitle_(title)
    button.setState_(NSOnState if enabled else 0)
    return button


class RelaySettingsWindow(NSObject):  # type: ignore[misc]
    def initWithPreferences_onSave_(
        self,
        preferences: RelayPreferences,
        on_save: Callable[[RelayPreferences], None],
    ) -> RelaySettingsWindow:
        self = objc.super(RelaySettingsWindow, self).init()
        if self is None:
            raise RuntimeError("Could not initialize Relay settings")
        self._preferences = preferences
        self._on_save = on_save
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 430, 390),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False,
        )
        self._panel.setTitle_("Relay Settings")
        self._panel.setReleasedWhenClosed_(False)
        self._panel.center()
        content = self._panel.contentView()

        self._auto_send = _toggle(
            "Auto-send + Continuous Listening", preferences.auto_send_messages, 24, 302
        )
        self._tts = _toggle("Spoken Responses (TTS)", preferences.tts_enabled, 24, 268)
        self._always_on_top = _toggle(
            "Overlay Always on Top", preferences.overlay_always_on_top, 24, 234
        )
        self._hotkey = _field(preferences.push_to_talk_hotkey, 190, 194, 210)
        self._confidence = _field(str(preferences.jev_confidence_threshold), 270, 153)
        self._silence = _field(str(preferences.silence_endpoint_ms), 270, 117)
        self._stability = _field(str(preferences.transcript_stability_ms), 270, 81)

        model = _label(preferences.local_model_name, 190, 47, 210)
        model.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12, 0))
        save = NSButton.alloc().initWithFrame_(NSMakeRect(310, 12, 96, 28))
        save.setTitle_("Save")
        save.setBezelStyle_(NSBezelStyleRounded)
        save.setTarget_(self)
        save.setAction_("saveClicked:")
        self._error = _label("", 24, 14, 278)
        self._error.setTextColor_(NSColor.systemRedColor())
        self._error.setFont_(NSFont.systemFontOfSize_(11))

        for view in (
            self._auto_send,
            self._tts,
            self._always_on_top,
            _label("Push-to-talk hotkey", 24, 195),
            self._hotkey,
            _label("Jev confidence threshold", 24, 154),
            self._confidence,
            _label("Silence endpoint (ms)", 24, 118),
            self._silence,
            _label("Transcript stability (ms)", 24, 82),
            self._stability,
            _label("Local generation model", 24, 47),
            model,
            self._error,
            save,
        ):
            content.addSubview_(view)
        return self

    @objc.python_method  # type: ignore[untyped-decorator]
    def show(self) -> None:
        self._panel.makeKeyAndOrderFront_(None)

    @objc.python_method  # type: ignore[untyped-decorator]
    def close(self) -> None:
        self._panel.close()

    def saveClicked_(self, _sender: Any) -> None:
        self._panel.makeFirstResponder_(None)
        hotkey = str(self._hotkey.stringValue()).strip()
        try:
            HotkeySpec.parse(hotkey)
        except ValueError:
            self._error.setStringValue_("Use 2+ modifiers, optionally plus a key.")
            NSBeep()
            return
        try:
            updated = self._preferences.model_copy(
                update={
                    "auto_send_messages": bool(self._auto_send.state()),
                    "tts_enabled": bool(self._tts.state()),
                    "overlay_always_on_top": bool(self._always_on_top.state()),
                    "jev_confidence_threshold": float(self._confidence.stringValue()),
                    "silence_endpoint_ms": int(self._silence.stringValue()),
                    "transcript_stability_ms": int(self._stability.stringValue()),
                    "push_to_talk_hotkey": hotkey,
                }
            )
            updated = RelayPreferences.model_validate(updated.model_dump())
        except (ValueError, ValidationError):
            self._error.setStringValue_("Check the highlighted settings values.")
            NSBeep()
            return
        try:
            self._on_save(updated)
        except Exception:
            self._error.setStringValue_("Relay could not save these settings.")
            NSBeep()
            return
        self._preferences = updated
        self._error.setStringValue_("")
        self._panel.performClose_(None)

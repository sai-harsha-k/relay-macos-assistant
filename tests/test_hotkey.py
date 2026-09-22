from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from local_assistant.actions.models import OpenApp
from local_assistant.app.hotkey import (
    HotkeyCallbackDispatcher,
    HotkeyConflictError,
    HotkeyEventMatcher,
    HotkeySpec,
    PushToTalkHotkey,
)
from local_assistant.app.lifecycle import RelayState, VoiceSession
from local_assistant.runtime.types import ControllerResult, ExecutionResult


class FakeBackend:
    def __init__(self, *, conflict: bool = False) -> None:
        self.conflict = conflict
        self.press: Callable[[], None] | None = None
        self.release: Callable[[], None] | None = None
        self.stopped = 0

    def start(
        self, spec: HotkeySpec, on_press: Callable[[], None], on_release: Callable[[], None]
    ) -> None:
        if self.conflict:
            raise HotkeyConflictError("shortcut unavailable")
        assert spec == HotkeySpec.parse("command+option+space")
        self.press = on_press
        self.release = on_release

    def stop(self) -> None:
        self.stopped += 1


def test_hotkey_callbacks_are_dispatched_without_blocking_event_delivery() -> None:
    gate = threading.Event()
    press_started = threading.Event()
    completed: list[str] = []

    def press() -> None:
        press_started.set()
        gate.wait(1)
        completed.append("press")

    dispatcher = HotkeyCallbackDispatcher(press, lambda: completed.append("release"))
    dispatcher.start()
    started = time.perf_counter()
    dispatcher.submit("press")
    dispatcher.submit("release")
    assert time.perf_counter() - started < 0.05
    assert press_started.wait(1)
    assert completed == []
    gate.set()
    dispatcher.stop()
    assert completed == ["press", "release"]


def test_hotkey_press_and_release_record_and_route_final_audio(tmp_path: Path) -> None:
    audio = tmp_path / "ptt.wav"
    routed = threading.Event()

    class Recorder:
        started = 0

        def start(self) -> None:
            self.started += 1

        def stop(self) -> Path:
            audio.write_bytes(b"voice")
            return audio

        def cancel(self) -> None:
            pass

    class Controller:
        calls = 0

        def handle_audio(self, audio_path: Path) -> ControllerResult:
            assert audio_path == audio
            self.calls += 1
            routed.set()
            return ControllerResult(
                "open calculator",
                OpenApp(app_name="Calculator"),
                ExecutionResult(True, "Opened Calculator"),
            )

    recorder = Recorder()
    controller = Controller()
    session = VoiceSession(recorder, controller, return_to_idle=True)
    backend = FakeBackend()
    hotkey = PushToTalkHotkey(
        backend,
        "command+option+space",
        session.start_listening,
        session.stop_listening,
    )
    assert hotkey.start()
    assert backend.press is not None and backend.release is not None
    backend.press()
    backend.press()
    assert recorder.started == 1
    assert session.state is RelayState.LISTENING
    backend.release()
    backend.release()
    assert routed.wait(2)
    assert controller.calls == 1
    deadline = time.monotonic() + 2
    while session.state is not RelayState.IDLE and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.state is RelayState.IDLE
    hotkey.stop()
    session.shutdown()


def test_hotkey_conflict_is_reported_without_crashing() -> None:
    errors: list[str] = []
    hotkey = PushToTalkHotkey(
        FakeBackend(conflict=True),
        "command+option+space",
        lambda: None,
        lambda: None,
        errors.append,
    )
    assert not hotkey.start()
    assert errors == ["shortcut unavailable"]


def test_hotkey_parser_rejects_invalid_shortcuts() -> None:
    assert HotkeySpec.parse("command+option+space").display == "⌥⌘Space"
    assert HotkeySpec.parse("control+command").display == "⌃⌘"
    errors: list[str] = []
    hotkey = PushToTalkHotkey(FakeBackend(), "space", lambda: None, lambda: None, errors.append)
    assert not hotkey.start()
    assert errors


def test_modifier_only_chord_starts_and_stops_on_flag_changes() -> None:
    matcher = HotkeyEventMatcher(key_code=None, required_mask=0b11, relevant_mask=0b1111)
    assert matcher.transition(key_code=59, flags=0b01, flags_changed=True) == (None, False)
    assert matcher.transition(key_code=55, flags=0b11, flags_changed=True) == (
        "press",
        False,
    )
    assert matcher.transition(key_code=55, flags=0b11, flags_changed=True) == (None, False)
    assert matcher.transition(key_code=59, flags=0b10, flags_changed=True) == (
        "release",
        False,
    )


def test_physical_release_submits_even_after_modifier_flags_clear() -> None:
    matcher = HotkeyEventMatcher(key_code=49, required_mask=0b11, relevant_mask=0b1111)
    assert matcher.transition(key_code=49, flags=0b11, key_down=True) == ("press", True)
    assert matcher.transition(key_code=49, flags=0, key_up=True) == ("release", True)
    assert matcher.transition(key_code=49, flags=0, key_up=True) == (None, False)


def test_releasing_a_modifier_ends_the_active_chord_once() -> None:
    matcher = HotkeyEventMatcher(key_code=49, required_mask=0b11, relevant_mask=0b1111)
    matcher.transition(key_code=49, flags=0b11, key_down=True)
    assert matcher.transition(key_code=58, flags=0b01, flags_changed=True) == (
        "release",
        False,
    )
    assert matcher.transition(key_code=49, flags=0, key_up=True) == (None, False)

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from queue import SimpleQueue
from typing import Protocol

from local_assistant.app.permissions import macos_accessibility_trusted


class HotkeyConflictError(RuntimeError):
    pass


_KEY_CODES = {
    "space": 49,
    "return": 36,
    "enter": 36,
    "escape": 53,
    "tab": 48,
    "delete": 51,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
    "0": 29,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "5": 23,
    "6": 22,
    "7": 26,
    "8": 28,
    "9": 25,
    **{
        chr(ord("a") + index): code
        for index, code in enumerate(
            (
                0,
                11,
                8,
                2,
                14,
                3,
                5,
                4,
                34,
                38,
                40,
                37,
                46,
                45,
                31,
                35,
                12,
                15,
                1,
                17,
                32,
                9,
                13,
                7,
                16,
                6,
            )
        )
    },
}
_MODIFIER_NAMES = {"command", "option", "control", "shift"}


@dataclass(frozen=True, slots=True)
class HotkeySpec:
    modifiers: frozenset[str]
    key: str | None

    @classmethod
    def parse(cls, value: str) -> HotkeySpec:
        parts = [part.strip().casefold() for part in value.split("+") if part.strip()]
        aliases = {"cmd": "command", "alt": "option", "ctrl": "control"}
        parts = [aliases.get(part, part) for part in parts]
        if not parts:
            raise ValueError("hotkey is empty")
        if parts[-1] in _KEY_CODES:
            key: str | None = parts[-1]
            modifiers = frozenset(parts[:-1])
        else:
            key = None
            modifiers = frozenset(parts)
        if not modifiers or not modifiers <= _MODIFIER_NAMES:
            raise ValueError("hotkey contains unsupported modifiers")
        if key is None and len(modifiers) < 2:
            raise ValueError("a modifier-only hotkey needs at least two modifiers")
        return cls(modifiers, key)

    @property
    def display(self) -> str:
        symbols = {"command": "⌘", "option": "⌥", "control": "⌃", "shift": "⇧"}
        order = ("control", "option", "shift", "command")
        modifiers = "".join(symbols[name] for name in order if name in self.modifiers)
        if self.key is None:
            return modifiers
        return modifiers + ("Space" if self.key == "space" else self.key.upper())


class HotkeyBackend(Protocol):
    def start(
        self, spec: HotkeySpec, on_press: Callable[[], None], on_release: Callable[[], None]
    ) -> None: ...

    def stop(self) -> None: ...


class HotkeyCallbackDispatcher:
    """Deliver hotkey work serially without blocking the macOS event tap."""

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._events: SimpleQueue[str | None] = SimpleQueue()
        self._thread = threading.Thread(
            target=self._run,
            name="relay-hotkey-actions",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def submit(self, event: str) -> None:
        self._events.put(event)

    def _run(self) -> None:
        while (event := self._events.get()) is not None:
            if event == "press":
                self._on_press()
            elif event == "release":
                self._on_release()

    def stop(self) -> None:
        self._events.put(None)
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=2)


@dataclass(slots=True)
class HotkeyEventMatcher:
    """Track one physical chord independently of modifier flags on key-up."""

    key_code: int | None
    required_mask: int
    relevant_mask: int
    held: bool = False

    def transition(
        self,
        *,
        key_code: int,
        flags: int,
        key_down: bool = False,
        key_up: bool = False,
        flags_changed: bool = False,
    ) -> tuple[str | None, bool]:
        if flags_changed:
            if self.held and flags & self.required_mask != self.required_mask:
                self.held = False
                return "release", False
            if (
                self.key_code is None
                and not self.held
                and flags & self.relevant_mask == self.required_mask
            ):
                self.held = True
                return "press", False
        if key_up and key_code == self.key_code and self.held:
            self.held = False
            return "release", True
        if (
            key_down
            and key_code == self.key_code
            and flags & self.relevant_mask == self.required_mask
        ):
            if self.held:
                return None, True
            self.held = True
            return "press", True
        return None, False

    def cancel(self) -> bool:
        was_held, self.held = self.held, False
        return was_held


class QuartzHotkeyBackend:
    """Suppressing global event tap used only for the configured push-to-talk chord."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._run_loop: object | None = None
        self._tap: object | None = None
        self._callback: object | None = None
        self._startup_cancel = threading.Event()
        self._dispatcher: HotkeyCallbackDispatcher | None = None

    def start(
        self, spec: HotkeySpec, on_press: Callable[[], None], on_release: Callable[[], None]
    ) -> None:
        if self._thread is not None:
            raise RuntimeError("global hotkey is already active")
        self._startup_cancel.clear()
        dispatcher = HotkeyCallbackDispatcher(on_press, on_release)
        self._dispatcher = dispatcher
        dispatcher.start()
        ready = threading.Event()
        failure: list[Exception] = []

        def run_impl() -> None:
            import CoreFoundation  # type: ignore[import-untyped]
            import Quartz  # type: ignore[import-untyped]

            if self._startup_cancel.is_set():
                ready.set()
                return

            modifier_masks = {
                "command": Quartz.kCGEventFlagMaskCommand,
                "option": Quartz.kCGEventFlagMaskAlternate,
                "control": Quartz.kCGEventFlagMaskControl,
                "shift": Quartz.kCGEventFlagMaskShift,
            }
            relevant_mask = 0
            required_mask = 0
            for name, mask in modifier_masks.items():
                relevant_mask |= mask
                if name in spec.modifiers:
                    required_mask |= mask
            key_code = _KEY_CODES[spec.key] if spec.key is not None else None
            matcher = HotkeyEventMatcher(key_code, required_mask, relevant_mask)

            def callback(proxy: object, event_type: int, event: object, refcon: object) -> object:
                del proxy, refcon
                if event_type in {
                    Quartz.kCGEventTapDisabledByTimeout,
                    Quartz.kCGEventTapDisabledByUserInput,
                }:
                    if matcher.cancel():
                        dispatcher.submit("release")
                    if self._tap is not None:
                        Quartz.CGEventTapEnable(self._tap, True)
                    return event
                current_key = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode
                )
                flags = Quartz.CGEventGetFlags(event)
                transition, consume = matcher.transition(
                    key_code=current_key,
                    flags=flags,
                    key_down=event_type == Quartz.kCGEventKeyDown,
                    key_up=event_type == Quartz.kCGEventKeyUp,
                    flags_changed=event_type == Quartz.kCGEventFlagsChanged,
                )
                if transition == "press":
                    dispatcher.submit("press")
                elif transition == "release":
                    dispatcher.submit("release")
                return None if consume else event

            # The C event tap only retains the bridged function pointer. Keep the Python
            # callable alive for as long as the run loop is active as well.
            self._callback = callback

            trusted = macos_accessibility_trusted()
            if not trusted:
                failure.append(
                    HotkeyConflictError(
                        "Global push-to-talk needs Accessibility permission. In System "
                        "Settings → Privacy & Security → Accessibility, enable Relay, then "
                        "save Settings again or reopen Relay."
                    )
                )
                ready.set()
                return

            if self._startup_cancel.is_set():
                ready.set()
                return

            mask = (
                Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            )
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionDefault,
                mask,
                callback,
                None,
            )
            if tap is None:
                failure.append(
                    HotkeyConflictError(
                        "macOS blocked Relay's global hotkey event tap. Toggle Relay in "
                        "Privacy & Security → Accessibility (and Input Monitoring if listed), "
                        "then save Settings again."
                    )
                )
                ready.set()
                return
            self._tap = tap
            source = CoreFoundation.CFMachPortCreateRunLoopSource(None, tap, 0)
            run_loop = CoreFoundation.CFRunLoopGetCurrent()
            self._run_loop = run_loop
            CoreFoundation.CFRunLoopAddSource(
                run_loop, source, CoreFoundation.kCFRunLoopCommonModes
            )
            Quartz.CGEventTapEnable(tap, True)
            ready.set()
            CoreFoundation.CFRunLoopRun()

        def run() -> None:
            try:
                run_impl()
            except Exception as exc:
                failure.append(HotkeyConflictError(f"Global hotkey initialization failed: {exc}"))
                ready.set()

        thread = threading.Thread(target=run, name="relay-global-hotkey", daemon=True)
        self._thread = thread
        thread.start()
        if not ready.wait(10):
            self._startup_cancel.set()
            self.stop()
            raise HotkeyConflictError(
                "Global hotkey initialization did not finish within 10 seconds. "
                "Quit Relay and reopen it after checking Accessibility permission."
            )
        if failure:
            self._thread = None
            self._callback = None
            dispatcher.stop()
            self._dispatcher = None
            raise failure[0]

    def stop(self) -> None:
        self._startup_cancel.set()
        thread, self._thread = self._thread, None
        run_loop, self._run_loop = self._run_loop, None
        if run_loop is not None:
            import CoreFoundation

            CoreFoundation.CFRunLoopStop(run_loop)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        dispatcher, self._dispatcher = self._dispatcher, None
        if dispatcher is not None:
            dispatcher.stop()
        self._tap = None
        self._callback = None


class PushToTalkHotkey:
    def __init__(
        self,
        backend: HotkeyBackend,
        binding: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._backend = backend
        self._binding = binding
        self._on_press = on_press
        self._on_release = on_release
        self._on_error = on_error or (lambda message: None)
        self._held = False
        self._active = False
        self._lock = threading.Lock()

    def start(self) -> bool:
        try:
            self._backend.start(HotkeySpec.parse(self._binding), self._press, self._release)
        except (HotkeyConflictError, ValueError, RuntimeError) as exc:
            self._on_error(str(exc))
            return False
        self._active = True
        return True

    def update_binding(self, binding: str) -> bool:
        self.stop()
        self._binding = binding
        return self.start()

    def _press(self) -> None:
        with self._lock:
            if self._held:
                return
            self._held = True
        try:
            self._on_press()
        except Exception as exc:
            with self._lock:
                self._held = False
            self._on_error(str(exc))

    def _release(self) -> None:
        with self._lock:
            if not self._held:
                return
            self._held = False
        try:
            self._on_release()
        except Exception as exc:
            self._on_error(str(exc))

    def stop(self) -> None:
        with self._lock:
            self._held = False
        if self._active:
            self._backend.stop()
            self._active = False

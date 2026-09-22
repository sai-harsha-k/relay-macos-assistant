from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from local_assistant.runtime.types import ExecutionResult

Runner = Callable[..., subprocess.CompletedProcess[str]]
Clock = Callable[[], datetime]

_SCREENSHOT_SUBDIRECTORY = Path("Pictures") / "Relay Screenshots"


_WHATSAPP_SEND_SCRIPT = r"""on textForAttribute(anElement, attributeName)
tell application "System Events"
    try
        return value of attribute attributeName of anElement as text
    on error
        return ""
    end try
end tell
end textForAttribute

on elementMatches(anElement, expectedText)
tell application "System Events"
    try
        set elementRole to role of anElement as text
        if elementRole is in {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"} then ¬
            return false
    on error
        return false
    end try
end tell
set candidateTexts to {my textForAttribute(anElement, "AXTitle"), ¬
    my textForAttribute(anElement, "AXValue"), ¬
    my textForAttribute(anElement, "AXDescription"), ¬
    my textForAttribute(anElement, "AXHelp")}
ignoring case
    repeat with candidateText in candidateTexts
        if candidateText as text is expectedText then return true
        if candidateText as text starts with expectedText & "," then return true
    end repeat
end ignoring
return false
end elementMatches

on findMatchingElement(theWindow, expectedText)
tell application "System Events"
    try
        set allElements to entire contents of theWindow
        repeat with anElement in allElements
            if my elementMatches(anElement, expectedText) then return anElement
        end repeat
    end try
end tell
return missing value
end findMatchingElement

on clickElement(anElement)
tell application "System Events"
    try
        perform action "AXPress" of anElement
        return true
    end try
    try
        set elementPosition to position of anElement
        set elementSize to size of anElement
        set clickPoint to {(item 1 of elementPosition) + ((item 1 of elementSize) / 2), ¬
            (item 2 of elementPosition) + ((item 2 of elementSize) / 2)}
        click at clickPoint
        return true
    end try
end tell
return false
end clickElement

on run argv
set appName to item 1 of argv
set recipientName to item 2 of argv
set messageText to item 3 of argv
tell application appName to activate
delay 0.8
tell application "System Events"
    if not (exists process appName) then error "WhatsApp did not start." number 1708
    tell process appName
        set frontmost to true
        keystroke "n" using {command down, control down}
        delay 0.8
        keystroke recipientName
    end tell
end tell

set recipientElement to missing value
repeat 20 times
    tell application "System Events" to tell process appName
        set recipientElement to my findMatchingElement(front window, recipientName)
    end tell
    if recipientElement is not missing value then exit repeat
    delay 0.1
end repeat
if recipientElement is missing value then ¬
    error "Relay could not find an exact, non-editable WhatsApp recipient; nothing was sent." ¬
        number 1708
if not my clickElement(recipientElement) then ¬
    error "Relay found the WhatsApp recipient but could not select it; nothing was sent." ¬
        number 1708

delay 1.0
tell application "System Events" to tell process appName
    keystroke messageText
    delay 0.3
    key code 36
end tell

set messageFound to false
repeat 20 times
    tell application "System Events" to tell process appName
        try
            set allElements to entire contents of front window
            repeat with anElement in allElements
                if my elementMatches(anElement, messageText) then
                    set messageFound to true
                    exit repeat
                end if
            end repeat
        end try
    end tell
    if messageFound then exit repeat
    delay 0.1
end repeat
if not messageFound then ¬
    error ("Relay attempted Send but could not verify the outgoing message. " & ¬
        "Check WhatsApp before retrying.") number 1708
return "verified"
end run"""


_CLICK_FRONTMOST_ELEMENT_SCRIPT = r"""on textForAttribute(anElement, attributeName)
tell application "System Events"
    try
        return value of attribute attributeName of anElement as text
    on error
        return ""
    end try
end tell
end textForAttribute

on elementMatches(anElement, expectedText)
tell application "System Events"
    try
        set elementRole to role of anElement as text
        if elementRole is in {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"} then ¬
            return false
    on error
        return false
    end try
end tell
set candidateTexts to {my textForAttribute(anElement, "AXTitle"), ¬
    my textForAttribute(anElement, "AXValue"), ¬
    my textForAttribute(anElement, "AXDescription"), ¬
    my textForAttribute(anElement, "AXHelp")}
ignoring case
    repeat with candidateText in candidateTexts
        if candidateText as text is expectedText then return true
        if candidateText as text starts with expectedText & "," then return true
    end repeat
end ignoring
return false
end elementMatches

on activateElement(anElement)
tell application "System Events"
    try
        perform action "AXPress" of anElement
        return true
    end try
    try
        set elementPosition to position of anElement
        set elementSize to size of anElement
        set clickPoint to {(item 1 of elementPosition) + ((item 1 of elementSize) / 2), ¬
            (item 2 of elementPosition) + ((item 2 of elementSize) / 2)}
        click at clickPoint
        return true
    end try
end tell
return false
end activateElement

on run argv
set expectedText to item 1 of argv
tell application "System Events"
    set frontProcess to first application process whose frontmost is true
    if not (exists front window of frontProcess) then ¬
        error "The frontmost application has no accessible window." number 1708
    set allElements to entire contents of front window of frontProcess
end tell

repeat with anElement in allElements
    if my elementMatches(anElement, expectedText) then
        if my activateElement(anElement) then return "activated"
        error "Relay found the exact element but could not activate it." number 1708
    end if
end repeat
error ("Relay could not find an exact visible element titled “" & expectedText & "”.") number 1708
end run"""


class MacOSPlatformAdapter:
    def __init__(
        self,
        runner: Runner = subprocess.run,
        *,
        home_directory: Path | None = None,
        clock: Clock = datetime.now,
    ) -> None:
        self._runner = runner
        self._home_directory = home_directory or Path.home()
        self._clock = clock

    def _run(self, command: Sequence[str], success_message: str) -> ExecutionResult:
        started = time.perf_counter()
        try:
            completed = self._runner(
                command, check=False, capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ExecutionResult(
                False, str(exc), duration_ms=(time.perf_counter() - started) * 1000
            )
        duration = (time.perf_counter() - started) * 1000
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "command failed").strip()
            return ExecutionResult(False, message, duration_ms=duration)
        return ExecutionResult(
            True, success_message, data=completed.stdout.strip() or None, duration_ms=duration
        )

    def _osascript(self, script: str, args: Sequence[str], message: str) -> ExecutionResult:
        return self._run(["osascript", "-e", script, *args], message)

    def list_applications(self) -> Sequence[str]:
        roots = (Path("/Applications"), Path("/System/Applications"), Path.home() / "Applications")
        names: set[str] = set()
        for root in roots:
            if root.is_dir():
                names.update(path.stem for path in root.glob("*.app"))
        return sorted(names, key=str.casefold)

    def frontmost_app(self) -> str | None:
        try:
            from AppKit import NSWorkspace  # type: ignore[import-untyped]

            application = NSWorkspace.sharedWorkspace().frontmostApplication()
            name = application.localizedName() if application is not None else None
            return str(name) if name else None
        except (ImportError, AttributeError):
            result = self._osascript(
                'tell application "System Events" to get name of first application process '
                "whose frontmost is true",
                [],
                "Read frontmost application",
            )
            return str(result.data).strip() if result.success and result.data else None

    def open_app(self, name: str) -> ExecutionResult:
        return self._run(["open", "-a", name], f"Opened {name}")

    def close_app(self, name: str) -> ExecutionResult:
        script = "on run argv\ntell application (item 1 of argv) to quit\nend run"
        return self._osascript(script, [name], f"Closed {name}")

    def focus_app(self, name: str) -> ExecutionResult:
        script = "on run argv\ntell application (item 1 of argv) to activate\nend run"
        return self._osascript(script, [name], f"Focused {name}")

    def open_url(self, url: str, app_name: str | None = None) -> ExecutionResult:
        command = ["open", url] if app_name is None else ["open", "-a", app_name, url]
        suffix = "default browser" if app_name is None else app_name
        return self._run(command, f"Opened {url} in {suffix}")

    def type_text(self, text: str) -> ExecutionResult:
        script = (
            'on run argv\ntell application "System Events" to keystroke (item 1 of argv)\nend run'
        )
        return self._osascript(script, [text], "Typed text")

    def press_key(self, key: str) -> ExecutionResult:
        key_codes = {
            "enter": "36",
            "return": "36",
            "escape": "53",
            "tab": "48",
            "space": "49",
            "delete": "51",
        }
        normalized = key.casefold()
        if normalized in key_codes:
            return self._osascript(
                f'tell application "System Events" to key code {key_codes[normalized]}',
                [],
                f"Pressed {key}",
            )
        script = (
            'on run argv\ntell application "System Events" to keystroke (item 1 of argv)\nend run'
        )
        return self._osascript(script, [key], f"Pressed {key}")

    def keyboard_shortcut(self, keys: tuple[str, ...]) -> ExecutionResult:
        modifiers = {
            "command": "command down",
            "cmd": "command down",
            "control": "control down",
            "ctrl": "control down",
            "option": "option down",
            "alt": "option down",
            "shift": "shift down",
        }
        special_codes = {
            "enter": 36,
            "return": 36,
            "escape": 53,
            "tab": 48,
            "space": 49,
            "delete": 51,
        }
        primary = keys[-1]
        using = [modifiers[key] for key in keys[:-1] if key in modifiers]
        suffix = f" using {{{', '.join(using)}}}" if using else ""
        if primary in special_codes:
            line = f"key code {special_codes[primary]}{suffix}"
        else:
            line = f'keystroke "{primary}"{suffix}'
        return self._osascript(
            f'tell application "System Events" to {line}', [], f"Pressed {'+'.join(keys)}"
        )

    def click_element(self, title: str) -> ExecutionResult:
        """Activate an exact accessible element in the user's frontmost application."""
        return self._osascript(
            _CLICK_FRONTMOST_ELEMENT_SCRIPT,
            [title],
            f"Opened {title}",
        )

    def scroll(self, direction: str, amount: int) -> ExecutionResult:
        page_count = max(1, min(10, round(amount / 600)))
        key_code = {"down": 121, "up": 116, "left": 123, "right": 124}[direction]
        repeats = page_count if direction in {"up", "down"} else page_count * 6
        script = (
            f'tell application "System Events" to repeat {repeats} times\n'
            f"key code {key_code}\n"
            "end repeat"
        )
        active_app = self.frontmost_app()
        suffix = f" in {active_app}" if active_app else ""
        return self._osascript(script, [], f"Scrolled {direction}{suffix}")

    def set_volume(self, level: int) -> ExecutionResult:
        return self._osascript(f"set volume output volume {level}", [], f"Set volume to {level}%")

    def media_control(self, command: str) -> ExecutionResult:
        if command == "mute":
            return self._osascript("set volume with output muted", [], "Muted audio")
        if command == "unmute":
            return self._osascript("set volume without output muted", [], "Unmuted audio")
        music_command = {
            "play_pause": "playpause",
            "next": "next track",
            "previous": "previous track",
        }[command]
        return self._osascript(
            f'tell application "Music" to {music_command}', [], f"Media control: {command}"
        )

    def open_folder(self, path: Path) -> ExecutionResult:
        expanded = path.expanduser().resolve()
        if not expanded.is_dir():
            return ExecutionResult(False, f"Folder does not exist: {expanded}")
        return self._run(["open", str(expanded)], f"Opened {expanded}")

    def find_file(self, query: str, root: Path | None) -> ExecutionResult:
        command = ["mdfind", "-interpret", query]
        if root:
            command.extend(["-onlyin", str(root.expanduser().resolve())])
        result = self._run(command, f"Searched for {query}")
        if result.success and isinstance(result.data, str):
            matches = result.data.splitlines()[:20]
            return ExecutionResult(
                True, f"Found {len(matches)} matching files", matches, result.duration_ms
            )
        return result

    def read_clipboard(self) -> ExecutionResult:
        return self._run(["pbpaste"], "Read clipboard")

    def write_clipboard(self, text: str) -> ExecutionResult:
        started = time.perf_counter()
        try:
            completed = self._runner(
                ["pbcopy"], input=text, check=False, capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ExecutionResult(
                False, str(exc), duration_ms=(time.perf_counter() - started) * 1000
            )
        duration = (time.perf_counter() - started) * 1000
        return ExecutionResult(
            completed.returncode == 0,
            "Wrote clipboard" if completed.returncode == 0 else completed.stderr.strip(),
            duration_ms=duration,
        )

    def take_screenshot(self, path: Path | None) -> ExecutionResult:
        if path is None:
            directory = self._home_directory / _SCREENSHOT_SUBDIRECTORY
            filename = self._clock().strftime("Screenshot %Y-%m-%d at %H.%M.%S.png")
            target = directory / filename
        else:
            target = path.expanduser()
        target = self._available_path(target.resolve())
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ExecutionResult(False, f"Could not create screenshot directory: {exc}")
        result = self._run(["screencapture", "-x", str(target)], f"Saved screenshot to {target}")
        if not result.success:
            return result
        try:
            size = target.stat().st_size if target.is_file() else 0
        except OSError as exc:
            return ExecutionResult(
                False,
                f"Could not verify screenshot file at {target}: {exc}",
                duration_ms=result.duration_ms,
            )
        if size <= 0:
            return ExecutionResult(
                False,
                f"screencapture reported success but no valid screenshot was created at {target}",
                duration_ms=result.duration_ms,
            )
        return ExecutionResult(
            True,
            f"Saved screenshot to {target}",
            target,
            result.duration_ms,
        )

    @staticmethod
    def _available_path(target: Path) -> Path:
        """Return a non-existing path without silently overwriting an existing capture."""
        if not target.exists():
            return target
        suffix = target.suffix
        stem = target.stem
        for index in range(1, 10_000):
            candidate = target.with_name(f"{stem}-{index}{suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not allocate a unique screenshot filename near {target}")

    def send_message(self, app_name: str, recipient: str, content: str) -> ExecutionResult:
        if app_name.casefold().removesuffix(".app") != "whatsapp":
            return ExecutionResult(
                False,
                f"Verified message sending is not implemented for {app_name}.",
            )
        return self._osascript(
            _WHATSAPP_SEND_SCRIPT,
            [app_name, recipient, content],
            f"Sent and verified message to {recipient}",
        )

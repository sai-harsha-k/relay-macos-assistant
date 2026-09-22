from __future__ import annotations

from pathlib import Path
from typing import Any

from local_assistant.runtime.types import ExecutionResult


class FakePlatform:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.active_app: str | None = None

    def list_applications(self) -> list[str]:
        return ["Calculator", "Chrome", "Notes", "Safari", "TextEdit", "WhatsApp"]

    def frontmost_app(self) -> str | None:
        return self.active_app

    def _record(self, name: str, value: Any = None) -> ExecutionResult:
        self.calls.append((name, value))
        return ExecutionResult(True, name, data=value)

    def open_app(self, name: str) -> ExecutionResult:
        return self._record("open_app", name)

    def close_app(self, name: str) -> ExecutionResult:
        return self._record("close_app", name)

    def focus_app(self, name: str) -> ExecutionResult:
        return self._record("focus_app", name)

    def open_url(self, url: str, app_name: str | None = None) -> ExecutionResult:
        return self._record("open_url", (url, app_name))

    def type_text(self, text: str) -> ExecutionResult:
        return self._record("type_text", text)

    def press_key(self, key: str) -> ExecutionResult:
        return self._record("press_key", key)

    def keyboard_shortcut(self, keys: tuple[str, ...]) -> ExecutionResult:
        return self._record("keyboard_shortcut", keys)

    def click_element(self, title: str) -> ExecutionResult:
        return self._record("click_element", title)

    def scroll(self, direction: str, amount: int) -> ExecutionResult:
        return self._record("scroll", (direction, amount))

    def set_volume(self, level: int) -> ExecutionResult:
        return self._record("set_volume", level)

    def media_control(self, command: str) -> ExecutionResult:
        return self._record("media_control", command)

    def open_folder(self, path: Path) -> ExecutionResult:
        return self._record("open_folder", path)

    def find_file(self, query: str, root: Path | None) -> ExecutionResult:
        return self._record("find_file", (query, root))

    def read_clipboard(self) -> ExecutionResult:
        return self._record("read_clipboard")

    def write_clipboard(self, text: str) -> ExecutionResult:
        return self._record("write_clipboard", text)

    def take_screenshot(self, path: Path | None) -> ExecutionResult:
        return self._record("take_screenshot", path)

    def send_message(self, app_name: str, recipient: str, content: str) -> ExecutionResult:
        return self._record("send_message", (app_name, recipient, content))


class FakeBrowser:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def _record(self, name: str, value: Any) -> ExecutionResult:
        self.calls.append((name, value))
        return ExecutionResult(True, name, value)

    def open_url(self, url: str, browser_app: str | None = None) -> ExecutionResult:
        return self._record("open_url", (url, browser_app) if browser_app else url)

    def search_web(self, query: str, browser_app: str | None = None) -> ExecutionResult:
        return self._record("search_web", (query, browser_app) if browser_app else query)

    def click_element(self, selector: str) -> ExecutionResult:
        return self._record("click_element", selector)

    def scroll(self, direction: str, amount: int) -> ExecutionResult:
        return self._record("scroll", (direction, amount))

    def close(self) -> None:
        pass


class FakeWriter:
    def __init__(self, response: str = "generated") -> None:
        self.response = response
        self.calls: list[str] = []

    def generate(self, instruction: str, context: str | None = None) -> str:
        del context
        self.calls.append(instruction)
        return self.response


class FakeTTS:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)

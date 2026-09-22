from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from local_assistant.actions.models import Action


@dataclass(frozen=True, slots=True)
class DecisionContext:
    app_names: tuple[str, ...] = ()
    clipboard_has_text: bool = False
    active_app: str | None = None
    last_action: str | None = None
    last_target: str | None = None
    previous_command: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionResult:
    action: Action
    confidence: float
    source: str


class PipelineStage(StrEnum):
    IDLE = "Idle"
    LISTENING = "Listening"
    PAUSED = "Paused"
    TRANSCRIBING = "Transcribing"
    DECIDING = "Deciding"
    EXECUTING = "Executing"
    DONE = "Done"
    ERROR = "Error"
    STOPPED = "Stopped"


@dataclass(frozen=True, slots=True)
class PipelineEvent:
    """A read-only observation of work already performed by the controller."""

    stage: PipelineStage
    transcript: str | None = None
    route_source: str | None = None
    confidence: float | None = None
    action: Action | None = None
    action_target: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
    result: str | None = None
    error: str | None = None
    writer_model: str | None = None
    partial: bool = False
    commit_id: str | None = None
    preview_error: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    success: bool
    message: str
    data: object | None = None
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class ControllerResult:
    transcript: str
    action: Action
    execution: ExecutionResult
    timings_ms: dict[str, float] = field(default_factory=dict)


class SpeechToTextProvider(Protocol):
    def transcribe(self, audio_path: Path) -> str: ...


class DecisionProvider(Protocol):
    def decide(self, text: str, context: DecisionContext) -> DecisionResult: ...


class WriterProvider(Protocol):
    def generate(self, instruction: str, context: str | None = None) -> str: ...


class PlatformAdapter(Protocol):
    def list_applications(self) -> Sequence[str]: ...
    def frontmost_app(self) -> str | None: ...
    def open_app(self, name: str) -> ExecutionResult: ...
    def close_app(self, name: str) -> ExecutionResult: ...
    def focus_app(self, name: str) -> ExecutionResult: ...
    def open_url(self, url: str, app_name: str | None = None) -> ExecutionResult: ...
    def type_text(self, text: str) -> ExecutionResult: ...
    def press_key(self, key: str) -> ExecutionResult: ...
    def keyboard_shortcut(self, keys: tuple[str, ...]) -> ExecutionResult: ...
    def click_element(self, title: str) -> ExecutionResult: ...
    def scroll(self, direction: str, amount: int) -> ExecutionResult: ...
    def set_volume(self, level: int) -> ExecutionResult: ...
    def media_control(self, command: str) -> ExecutionResult: ...
    def open_folder(self, path: Path) -> ExecutionResult: ...
    def find_file(self, query: str, root: Path | None) -> ExecutionResult: ...
    def read_clipboard(self) -> ExecutionResult: ...
    def write_clipboard(self, text: str) -> ExecutionResult: ...
    def take_screenshot(self, path: Path | None) -> ExecutionResult: ...
    def send_message(self, app_name: str, recipient: str, content: str) -> ExecutionResult: ...


class BrowserAdapter(Protocol):
    def open_url(self, url: str, browser_app: str | None = None) -> ExecutionResult: ...
    def search_web(self, query: str, browser_app: str | None = None) -> ExecutionResult: ...
    def click_element(self, selector: str) -> ExecutionResult: ...
    def scroll(self, direction: str, amount: int) -> ExecutionResult: ...
    def close(self) -> None: ...


class TextToSpeechProvider(Protocol):
    def speak(self, text: str) -> None: ...

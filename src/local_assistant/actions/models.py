from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    field_validator,
    model_validator,
)

MediaCommand = Literal["play_pause", "next", "previous", "mute", "unmute"]
ScrollDirection = Literal["up", "down", "left", "right"]


class ActionKind(StrEnum):
    OPEN_APP = "OPEN_APP"
    CLOSE_APP = "CLOSE_APP"
    FOCUS_APP = "FOCUS_APP"
    OPEN_URL = "OPEN_URL"
    SEARCH_WEB = "SEARCH_WEB"
    TYPE_TEXT = "TYPE_TEXT"
    GENERATE_TEXT = "GENERATE_TEXT"
    PRESS_KEY = "PRESS_KEY"
    KEYBOARD_SHORTCUT = "KEYBOARD_SHORTCUT"
    CLICK_ELEMENT = "CLICK_ELEMENT"
    SCROLL = "SCROLL"
    SET_VOLUME = "SET_VOLUME"
    MEDIA_CONTROL = "MEDIA_CONTROL"
    OPEN_FOLDER = "OPEN_FOLDER"
    FIND_FILE = "FIND_FILE"
    READ_CLIPBOARD = "READ_CLIPBOARD"
    WRITE_CLIPBOARD = "WRITE_CLIPBOARD"
    TAKE_SCREENSHOT = "TAKE_SCREENSHOT"
    SEND_MESSAGE = "SEND_MESSAGE"
    ASK_USER = "ASK_USER"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OpenApp(StrictModel):
    kind: Literal[ActionKind.OPEN_APP] = ActionKind.OPEN_APP
    app_name: str = Field(min_length=1, max_length=200)


class CloseApp(StrictModel):
    kind: Literal[ActionKind.CLOSE_APP] = ActionKind.CLOSE_APP
    app_name: str = Field(min_length=1, max_length=200)


class FocusApp(StrictModel):
    kind: Literal[ActionKind.FOCUS_APP] = ActionKind.FOCUS_APP
    app_name: str = Field(min_length=1, max_length=200)


class OpenUrl(StrictModel):
    kind: Literal[ActionKind.OPEN_URL] = ActionKind.OPEN_URL
    url: HttpUrl
    browser_app: str | None = Field(default=None, min_length=1, max_length=200)


class SearchWeb(StrictModel):
    kind: Literal[ActionKind.SEARCH_WEB] = ActionKind.SEARCH_WEB
    query: str | None = Field(default=None, min_length=1, max_length=2000)
    query_generation_instruction: str | None = Field(default=None, min_length=1, max_length=4000)
    browser_app: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_one_query_source(self) -> SearchWeb:
        if (self.query is None) == (self.query_generation_instruction is None):
            raise ValueError("provide exactly one of query or query_generation_instruction")
        return self


class TypeText(StrictModel):
    kind: Literal[ActionKind.TYPE_TEXT] = ActionKind.TYPE_TEXT
    text: str = Field(min_length=1, max_length=20_000)


class GenerateText(StrictModel):
    kind: Literal[ActionKind.GENERATE_TEXT] = ActionKind.GENERATE_TEXT
    instruction: str = Field(min_length=1, max_length=10_000)
    type_after_generation: bool = False


class PressKey(StrictModel):
    kind: Literal[ActionKind.PRESS_KEY] = ActionKind.PRESS_KEY
    key: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9 _-]+$")


class KeyboardShortcut(StrictModel):
    kind: Literal[ActionKind.KEYBOARD_SHORTCUT] = ActionKind.KEYBOARD_SHORTCUT
    keys: tuple[str, ...] = Field(min_length=2, max_length=5)

    @field_validator("keys")
    @classmethod
    def normalize_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        allowed = {
            "command",
            "cmd",
            "control",
            "ctrl",
            "option",
            "alt",
            "shift",
            "enter",
            "return",
            "escape",
            "tab",
            "space",
            "delete",
            "backspace",
            "left",
            "right",
            "up",
            "down",
            "a",
            "c",
            "n",
            "v",
            "x",
            "z",
            "w",
            "q",
        }
        normalized = tuple(key.lower().strip() for key in value)
        if any(key not in allowed for key in normalized):
            raise ValueError("shortcut contains an unsupported key")
        return normalized


class ClickElement(StrictModel):
    kind: Literal[ActionKind.CLICK_ELEMENT] = ActionKind.CLICK_ELEMENT
    selector: str = Field(min_length=1, max_length=1000)


class Scroll(StrictModel):
    kind: Literal[ActionKind.SCROLL] = ActionKind.SCROLL
    direction: ScrollDirection
    amount: int = Field(default=600, ge=1, le=10_000)


class SetVolume(StrictModel):
    kind: Literal[ActionKind.SET_VOLUME] = ActionKind.SET_VOLUME
    level: int = Field(ge=0, le=100)


class MediaControl(StrictModel):
    kind: Literal[ActionKind.MEDIA_CONTROL] = ActionKind.MEDIA_CONTROL
    command: MediaCommand


class OpenFolder(StrictModel):
    kind: Literal[ActionKind.OPEN_FOLDER] = ActionKind.OPEN_FOLDER
    path: Path


class FindFile(StrictModel):
    kind: Literal[ActionKind.FIND_FILE] = ActionKind.FIND_FILE
    query: str = Field(min_length=1, max_length=500)
    root: Path | None = None


class ReadClipboard(StrictModel):
    kind: Literal[ActionKind.READ_CLIPBOARD] = ActionKind.READ_CLIPBOARD


class WriteClipboard(StrictModel):
    kind: Literal[ActionKind.WRITE_CLIPBOARD] = ActionKind.WRITE_CLIPBOARD
    text: str = Field(max_length=100_000)


class TakeScreenshot(StrictModel):
    kind: Literal[ActionKind.TAKE_SCREENSHOT] = ActionKind.TAKE_SCREENSHOT
    path: Path | None = None


class SendMessage(StrictModel):
    kind: Literal[ActionKind.SEND_MESSAGE] = ActionKind.SEND_MESSAGE
    app_name: str = Field(min_length=1, max_length=200)
    recipient: str = Field(min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1, max_length=20_000)
    generation_instruction: str | None = Field(default=None, min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def require_one_content_source(self) -> SendMessage:
        if (self.content is None) == (self.generation_instruction is None):
            raise ValueError("provide exactly one of content or generation_instruction")
        return self


class AskUser(StrictModel):
    kind: Literal[ActionKind.ASK_USER] = ActionKind.ASK_USER
    question: str = Field(min_length=1, max_length=2000)
    reason: str | None = Field(default=None, max_length=2000)


Action = Annotated[
    OpenApp
    | CloseApp
    | FocusApp
    | OpenUrl
    | SearchWeb
    | TypeText
    | GenerateText
    | PressKey
    | KeyboardShortcut
    | ClickElement
    | Scroll
    | SetVolume
    | MediaControl
    | OpenFolder
    | FindFile
    | ReadClipboard
    | WriteClipboard
    | TakeScreenshot
    | SendMessage
    | AskUser,
    Field(discriminator="kind"),
]

ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)


def validate_action(data: object) -> Action:
    return ACTION_ADAPTER.validate_python(data)

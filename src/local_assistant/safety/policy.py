from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from local_assistant.actions.models import (
    Action,
    ActionKind,
    GenerateText,
    KeyboardShortcut,
    SendMessage,
    TypeText,
)


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class SafetyAssessment:
    risk: RiskLevel
    requires_confirmation: bool
    reason: str


_LOW_RISK = {
    ActionKind.OPEN_APP,
    ActionKind.FOCUS_APP,
    ActionKind.OPEN_URL,
    ActionKind.SEARCH_WEB,
    ActionKind.GENERATE_TEXT,
    ActionKind.SCROLL,
    ActionKind.SET_VOLUME,
    ActionKind.MEDIA_CONTROL,
    ActionKind.OPEN_FOLDER,
    ActionKind.FIND_FILE,
    ActionKind.READ_CLIPBOARD,
    ActionKind.TAKE_SCREENSHOT,
    ActionKind.ASK_USER,
}

_LOCAL_TEXT_EDITORS = {"notes", "textedit"}


class SafetyPolicy:
    """Central policy evaluated after typed validation and before dispatch."""

    def __init__(self, *, auto_send_messages: bool = False) -> None:
        self._auto_send_messages = auto_send_messages

    def set_auto_send_messages(self, enabled: bool) -> None:
        self._auto_send_messages = enabled

    def assess(self, action: Action, *, active_app: str | None = None) -> SafetyAssessment:
        normalized_app = (active_app or "").casefold().removesuffix(".app")
        if isinstance(action, SendMessage):
            if self._auto_send_messages:
                return SafetyAssessment(
                    RiskLevel.LOW,
                    False,
                    "Auto-send is enabled for an unambiguous recipient and message.",
                )
            return SafetyAssessment(
                RiskLevel.MEDIUM,
                True,
                "Sending a communication requires confirmation while Auto-send is off.",
            )
        if isinstance(action, GenerateText) and action.type_after_generation:
            if normalized_app in _LOCAL_TEXT_EDITORS:
                return SafetyAssessment(
                    RiskLevel.LOW,
                    False,
                    f"Writing into local editor {active_app} is reversible and does not send.",
                )
            return SafetyAssessment(
                RiskLevel.MEDIUM,
                True,
                "Generated text will be typed into the focused application.",
            )
        if isinstance(action, TypeText) and normalized_app in _LOCAL_TEXT_EDITORS:
            return SafetyAssessment(
                RiskLevel.LOW,
                False,
                f"Writing into local editor {active_app} is reversible and does not send.",
            )
        if (
            isinstance(action, TypeText)
            and normalized_app == "calculator"
            and re.fullmatch(r"[0-9+\-*/().%= ]+", action.text)
        ):
            return SafetyAssessment(
                RiskLevel.LOW,
                False,
                "Entering a bounded arithmetic expression in Calculator is reversible.",
            )
        if isinstance(action, KeyboardShortcut) and action.keys in {
            ("command", "n"),
            ("cmd", "n"),
        }:
            return SafetyAssessment(
                RiskLevel.LOW,
                False,
                "Creating a new untitled document is reversible.",
            )
        if action.kind in _LOW_RISK:
            return SafetyAssessment(RiskLevel.LOW, False, "Reversible or read-only operation.")
        return SafetyAssessment(
            RiskLevel.MEDIUM,
            True,
            "This action changes application or clipboard state and needs confirmation.",
        )

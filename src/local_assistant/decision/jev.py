from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from local_assistant.actions.models import (
    AskUser,
    ClickElement,
    CloseApp,
    FindFile,
    FocusApp,
    GenerateText,
    KeyboardShortcut,
    MediaCommand,
    MediaControl,
    OpenApp,
    OpenFolder,
    OpenUrl,
    PressKey,
    ReadClipboard,
    Scroll,
    ScrollDirection,
    SearchWeb,
    SendMessage,
    SetVolume,
    TakeScreenshot,
    TypeText,
    WriteClipboard,
)
from local_assistant.runtime.errors import ProviderResponseError, ProviderUnavailableError
from local_assistant.runtime.types import DecisionContext, DecisionResult

ACTION_CRITERIA: dict[str, str] = {
    "OPEN_APP": "Launch an application",
    "CLOSE_APP": "Quit a named application",
    "FOCUS_APP": "Bring a running application forward",
    "OPEN_URL": "Open a specific web address",
    "SEARCH_WEB": "Search the web for information",
    "TYPE_TEXT": "Type supplied literal text",
    "GENERATE_TEXT": "Compose, rewrite, summarize, or transform language",
    "PRESS_KEY": "Press one keyboard key",
    "KEYBOARD_SHORTCUT": "Use a named keyboard shortcut",
    "CLICK_ELEMENT": "Open or click an exact visible element title in the frontmost application",
    "SCROLL": "Scroll a page or view",
    "SET_VOLUME": "Set system output volume to a number",
    "MEDIA_CONTROL": "Control playback or mute",
    "OPEN_FOLDER": "Open a local folder",
    "FIND_FILE": "Find a local file by name",
    "READ_CLIPBOARD": "Read clipboard text",
    "WRITE_CLIPBOARD": "Copy supplied text to clipboard",
    "TAKE_SCREENSHOT": "Capture the screen",
    "SEND_MESSAGE": "Send supplied text to a named recipient in a messaging application",
    "ASK_USER": "Request clarification; use when no safe action is clear",
}

_SHORTCUTS: dict[str, tuple[str, ...]] = {
    "copy": ("command", "c"),
    "paste": ("command", "v"),
    "cut": ("command", "x"),
    "undo": ("command", "z"),
    "select all": ("command", "a"),
    "close tab": ("command", "w"),
}


def _value(obj: object, name: str, default: object = None) -> object:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _answer(answers: Mapping[str, object], name: str) -> tuple[str | None, float]:
    raw = answers.get(name)
    if raw is None:
        return None, 0.0
    choice = _value(raw, "choice")
    confidence = _value(raw, "confidence", 0.0)
    numeric_confidence = float(confidence) if isinstance(confidence, (int, float, str)) else 0.0
    return (str(choice) if choice is not None else None, numeric_confidence)


def _payload_candidates(text: str) -> list[str]:
    candidates = [text.strip()]
    patterns = (
        r"(?:search(?: the web)?(?: for)?|google)\s+(.+)",
        r"(?:type|write|compose|draft|rewrite|summarize)\s+(.+)",
        r"(?:copy)\s+(.+?)(?:\s+to (?:the )?clipboard)?$",
        r"(?:find|locate)\s+(?:a |the )?(?:file )?(?:named )?(.+)",
        r"(?:open|go to)\s+(.+)",
    )
    for pattern in patterns:
        if match := re.search(pattern, text, re.IGNORECASE):
            candidates.append(match.group(1).strip(" \"'"))
    candidates.extend(match.strip() for match in re.findall(r"[\"']([^\"']+)[\"']", text))
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))[:6]


def _message_field_candidates(text: str) -> list[tuple[str, str]]:
    clause = re.search(
        r"(?:^|\band\s+)(?:send|message|text)\s+(.+)$",
        text.strip().rstrip(".!?"),
        re.IGNORECASE,
    )
    if not clause or re.search(r"(?::|\s+(?:saying|that)\s+)", clause.group(1), re.I):
        return []
    words = clause.group(1).split()
    if len(words) < 2:
        return []
    return [(" ".join(words[:index]), " ".join(words[index:])) for index in range(1, len(words))][
        :5
    ]


def _explicit_app(text: str, apps: Sequence[str]) -> str | None:
    matches = [
        app
        for app in apps
        if re.search(
            rf"(?<!\w){re.escape(app.removesuffix('.app'))}(?!\w)",
            text,
            re.IGNORECASE,
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _normalize_url(value: str) -> str | None:
    compact = value.strip().replace(" dot ", ".").replace(" slash ", "/")
    if " " in compact:
        return None
    candidate = compact if "://" in compact else f"https://{compact}"
    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"} and parsed.hostname and "." in parsed.hostname:
        return candidate
    return None


def _deterministic_payload(text: str, action_id: str | None, fallback: str) -> str:
    """Extract a literal only after Jev selected its bounded action kind."""
    if action_id == "CLICK_ELEMENT":
        titled_video = re.search(
            r"(?:open|click|select)\s+(?:(?:that|the|a)\s+)?video\s+"
            r"(?:with\s+(?:the\s+)?title|titled)\s+[\"']?(.+?)[\"']?[.!?]*$",
            text,
            re.IGNORECASE,
        )
        if titled_video:
            return titled_video.group(1).strip(" \"'.!?")
    patterns = {
        "SEARCH_WEB": r"(?:search(?: the web)?(?: for)?|google)\s+(.+)",
        "TYPE_TEXT": r"(?:type)\s+(.+)",
        "GENERATE_TEXT": r"(?:write|compose|draft|rewrite|summarize)\s+(.+)",
        "WRITE_CLIPBOARD": r"(?:copy)\s+(.+?)(?:\s+to (?:the )?clipboard)?$",
        "FIND_FILE": r"(?:find|locate)\s+(?:a |the )?(?:file )?(?:named )?(.+)",
        "CLICK_ELEMENT": r"(?:click|press|select)\s+(.+)",
    }
    pattern = patterns.get(action_id or "")
    if pattern and (match := re.search(pattern, text, re.IGNORECASE)):
        return match.group(1).strip(" \"'")
    return fallback


def convert_jev_response(
    text: str,
    answers: Mapping[str, object],
    apps: Sequence[str],
    payloads: Sequence[str],
    active_app: str | None = None,
    last_target: str | None = None,
    message_fields: Sequence[tuple[str, str]] = (),
) -> DecisionResult:
    """Convert SDK response data to a validated action without executing anything."""
    action_id, action_confidence = _answer(answers, "action")
    app_id, app_confidence = _answer(answers, "app")
    payload_id, payload_confidence = _answer(answers, "payload")
    value_mode, value_mode_confidence = _answer(answers, "value_mode")
    message_fields_id, message_fields_confidence = _answer(answers, "message_fields")
    value_mode = value_mode or "literal"
    app_map = {f"a{index}": app for index, app in enumerate(apps)}
    transcript_app = _explicit_app(text, apps)
    payload_map = {f"p{index}": payload for index, payload in enumerate(payloads)}
    message_fields_map = {f"m{index}": fields for index, fields in enumerate(message_fields)}
    selected_payload = payload_map.get(payload_id or "", payloads[0] if payloads else text)
    payload = _deterministic_payload(text, action_id, selected_payload)

    def result(action: Any, *used_confidence: float) -> DecisionResult:
        confidence = min((action_confidence, *used_confidence))
        return DecisionResult(action=action, confidence=confidence, source="jev")

    if action_id in {"OPEN_APP", "CLOSE_APP", "FOCUS_APP"}:
        app = transcript_app or app_map.get(app_id or "")
        if not app:
            return result(AskUser(question="Which application should I use?"))
        action_type = {"OPEN_APP": OpenApp, "CLOSE_APP": CloseApp, "FOCUS_APP": FocusApp}[action_id]
        return result(action_type(app_name=app), 1.0 if transcript_app else app_confidence)
    if action_id == "OPEN_URL":
        url = next((_normalize_url(item) for item in payloads if _normalize_url(item)), None)
        return (
            result(OpenUrl.model_validate({"url": url}), payload_confidence)
            if url
            else result(
                AskUser(question="Which URL should I open?", reason="No valid URL was recognized.")
            )
        )
    if action_id == "SEARCH_WEB":
        if value_mode == "generate":
            instruction = text
            if last_target and re.search(r"\b(?:this|existing|previous)\b", text, re.IGNORECASE):
                instruction = f"{text}\nExisting search query: {last_target}"
            return result(
                SearchWeb(query_generation_instruction=instruction), value_mode_confidence
            )
        return result(SearchWeb(query=payload), payload_confidence)
    if action_id == "TYPE_TEXT":
        return result(TypeText(text=payload), payload_confidence)
    if action_id == "GENERATE_TEXT":
        return result(
            GenerateText(
                instruction=payload,
                type_after_generation=bool(re.search(r"\btype (?:it|that)\b", text, re.I)),
            ),
            payload_confidence,
        )
    if action_id == "PRESS_KEY":
        key = next(
            (key for key in ("enter", "escape", "tab", "space", "delete") if key in text.lower()),
            None,
        )
        return (
            result(PressKey(key=key), payload_confidence)
            if key
            else result(AskUser(question="Which key should I press?"))
        )
    if action_id == "KEYBOARD_SHORTCUT":
        keys = next((value for name, value in _SHORTCUTS.items() if name in text.lower()), None)
        return (
            result(KeyboardShortcut(keys=keys))
            if keys
            else result(AskUser(question="Which keyboard shortcut should I use?"))
        )
    if action_id == "CLICK_ELEMENT":
        if re.search(
            r"\b(?:random|any|some|a)\s+(?:visible\s+)?video\b|\bvideo\s+here\b",
            payload,
            re.IGNORECASE,
        ):
            return result(
                AskUser(
                    question="Which video should I open? Please say its visible title.",
                    reason="Relay will not guess which visible element to click.",
                )
            )
        return result(ClickElement(selector=payload), payload_confidence)
    if action_id == "SCROLL":
        direction = cast(
            ScrollDirection,
            next((d for d in ("up", "down", "left", "right") if d in text.lower()), "down"),
        )
        amount = 1200 if "a lot" in text.lower() else 600
        return result(Scroll(direction=direction, amount=amount))
    if action_id == "SET_VOLUME":
        match = re.search(r"\b(100|\d{1,2})\b", text)
        return (
            result(SetVolume(level=int(match.group(1))))
            if match
            else result(AskUser(question="What volume level, from 0 to 100, should I set?"))
        )
    if action_id == "MEDIA_CONTROL":
        command = cast(
            MediaCommand,
            next(
                (name for name in ("next", "previous", "mute", "unmute") if name in text.lower()),
                "play_pause",
            ),
        )
        return result(MediaControl(command=command))
    if action_id == "OPEN_FOLDER":
        home = Path.home()
        folder = next(
            (
                name
                for name in ("Desktop", "Documents", "Downloads")
                if name.lower() in text.lower()
            ),
            None,
        )
        return (
            result(OpenFolder(path=home / folder))
            if folder
            else result(AskUser(question="Which folder should I open?"))
        )
    if action_id == "FIND_FILE":
        return result(FindFile(query=payload), payload_confidence)
    if action_id == "READ_CLIPBOARD":
        return result(ReadClipboard())
    if action_id == "WRITE_CLIPBOARD":
        return result(WriteClipboard(text=payload), payload_confidence)
    if action_id == "TAKE_SCREENSHOT":
        return result(TakeScreenshot())
    if action_id == "SEND_MESSAGE":
        direct = re.search(
            r"(?:send|message|text)\s+(.+?)\s*(?::|\s+(?:saying|that)\s+)(.+)",
            text,
            re.IGNORECASE,
        )
        generated = re.search(
            r"(?:send|message|text)\s+(\S+)\s+((?:a|an)\s+.+)", text, re.IGNORECASE
        )
        match = generated if value_mode == "generate" else direct
        app = transcript_app or app_map.get(app_id or "") or active_app
        bounded_fields = message_fields_map.get(message_fields_id or "")
        if (not match and not bounded_fields) or not app:
            return result(
                AskUser(question="Which person, message, and messaging application should I use?")
            )
        if bounded_fields is not None:
            recipient, language_value = bounded_fields
        elif match is not None:
            recipient, language_value = match.groups()
        else:  # pragma: no cover - guarded above for static narrowing
            raise ProviderResponseError("Jev did not select message fields")
        recipient, language_value = recipient.strip(), language_value.strip()
        resolved_app_confidence = app_confidence if app_id and not transcript_app else 1.0
        if value_mode == "generate":
            return result(
                SendMessage(
                    app_name=app,
                    recipient=recipient,
                    generation_instruction=f"Write {language_value}",
                ),
                value_mode_confidence,
                resolved_app_confidence,
            )
        return result(
            SendMessage(app_name=app, recipient=recipient, content=language_value),
            resolved_app_confidence,
            message_fields_confidence if bounded_fields else payload_confidence,
        )
    if action_id == "ASK_USER" or action_id is None:
        return result(AskUser(question="Could you clarify what you want me to do?"))
    raise ProviderResponseError(f"Jev returned unsupported action: {action_id}")


class TypeSafeJevDecisionProvider:
    def __init__(self) -> None:
        if not os.environ.get("TYPESAFE_API_KEY"):
            raise ProviderUnavailableError(
                "Jev routing needs TYPESAFE_API_KEY. Exact deterministic commands still work."
            )

    def decide(self, text: str, context: DecisionContext) -> DecisionResult:
        try:
            from typesafe_sdk import Choice, TypeSafeClient
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise ProviderUnavailableError("typesafe-sdk is not installed") from exc

        apps = list(context.app_names)[:80]
        payloads = _payload_candidates(text)
        message_fields = _message_field_candidates(text)
        questions: dict[str, object] = {
            "action": Choice(
                instructions="Select the single intended desktop action", criteria=ACTION_CRITERIA
            ),
            "payload": Choice(
                instructions="Select the exact user-supplied payload; do not rewrite it",
                criteria={f"p{i}": value for i, value in enumerate(payloads)},
            ),
            "value_mode": Choice(
                instructions=(
                    "Choose literal when the field value is explicitly usable in the request. "
                    "Choose generate only for writing, rewriting, summarizing, or transforming "
                    "language."
                ),
                criteria={
                    "literal": "Use the user's explicit words directly",
                    "generate": "A language field must be composed or transformed",
                },
            ),
        }
        if apps:
            questions["app"] = Choice(
                instructions="Select the application named or clearly intended",
                criteria={f"a{i}": app for i, app in enumerate(apps)},
            )
        if message_fields:
            questions["message_fields"] = Choice(
                instructions=(
                    "Select the literal recipient/content split explicitly present in the request"
                ),
                criteria={
                    f"m{i}": f"recipient={recipient} | content={content}"
                    for i, (recipient, content) in enumerate(message_fields)
                },
            )
        try:
            context_items = {
                "active_app": context.active_app,
                "last_action": context.last_action,
                "last_target": context.last_target,
                "previous_command": context.previous_command,
            }
            relevant = ", ".join(f"{key}={value}" for key, value in context_items.items() if value)
            state = text if not relevant else f"request={text}\ncontext={relevant}"
            with TypeSafeClient() as client:
                response = client.system_one(state=state, questions=cast(Any, questions))
        except Exception as exc:
            raise ProviderUnavailableError(f"Jev request failed: {exc}") from exc
        answers = _value(response, "answers")
        if not isinstance(answers, Mapping):
            raise ProviderResponseError("Jev response did not contain an answers mapping")
        return convert_jev_response(
            text,
            answers,
            apps,
            payloads,
            context.active_app,
            context.last_target,
            message_fields,
        )

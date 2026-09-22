from __future__ import annotations

import re
from urllib.parse import quote_plus

from pydantic import HttpUrl

from local_assistant.actions.models import (
    ClickElement,
    MediaCommand,
    MediaControl,
    OpenApp,
    OpenUrl,
    Scroll,
    ScrollDirection,
    SearchWeb,
    SetVolume,
    TakeScreenshot,
)
from local_assistant.runtime.types import DecisionContext, DecisionResult

_EXACT_MEDIA: dict[str, MediaCommand] = {
    "play": "play_pause",
    "pause": "play_pause",
    "play music": "play_pause",
    "pause music": "play_pause",
    "next": "next",
    "next track": "next",
    "next song": "next",
    "previous": "previous",
    "previous track": "previous",
    "previous song": "previous",
    "mute": "mute",
    "mute volume": "mute",
    "unmute": "unmute",
    "unmute volume": "unmute",
}

_GENERATION_CUES = re.compile(
    r"\b(?:come up with|create|rewrite|rephrase|summarize|concise .* query|better query)\b",
    re.IGNORECASE,
)


class DeterministicCommandParser:
    """Small, intentionally conservative parser for unambiguous commands."""

    def parse(self, text: str, context: DecisionContext) -> DecisionResult | None:
        normalized = " ".join(text.casefold().strip().rstrip(".!?").split())
        literal_text = " ".join(text.strip().rstrip(".!?").split())
        if command := _EXACT_MEDIA.get(normalized):
            return DecisionResult(
                action=MediaControl(command=command), confidence=1.0, source="fast_path"
            )

        if normalized in {"take a screenshot", "take screenshot", "screenshot"}:
            return DecisionResult(action=TakeScreenshot(), confidence=1.0, source="fast_path")

        scroll_directions: dict[str, ScrollDirection] = {
            "scroll down": "down",
            "scroll up": "up",
            "scroll left": "left",
            "scroll right": "right",
            "page down": "down",
            "page up": "up",
        }
        if direction := scroll_directions.get(normalized):
            return DecisionResult(
                action=Scroll(direction=direction, amount=600),
                confidence=1.0,
                source="fast_path",
            )

        titled_video = re.fullmatch(
            r"(?:open|click|select)\s+(?:(?:that|the|a)\s+)?video\s+"
            r"(?:with\s+(?:the\s+)?title|titled)\s+(?:[\"“](.+?)[\"”]|(.+?))[.!?]*",
            " ".join(text.strip().split()),
            re.IGNORECASE,
        )
        if titled_video:
            title = next(group for group in titled_video.groups() if group is not None).strip()
            return DecisionResult(
                action=ClickElement(selector=title), confidence=1.0, source="fast_path"
            )

        youtube_search = re.fullmatch(
            r"search youtube for (.+)",
            literal_text,
            re.IGNORECASE,
        )
        if youtube_search and not _GENERATION_CUES.search(text):
            query = quote_plus(youtube_search.group(1))
            return DecisionResult(
                action=OpenUrl(
                    url=HttpUrl(f"https://www.youtube.com/results?search_query={query}")
                ),
                confidence=1.0,
                source="fast_path",
            )

        search_match = re.fullmatch(
            r"search(?: (?:google|youtube|the web))? for (.+)",
            literal_text,
            re.IGNORECASE,
        )
        if search_match and not _GENERATION_CUES.search(text):
            return DecisionResult(
                action=SearchWeb(query=search_match.group(1)),
                confidence=1.0,
                source="fast_path",
            )

        volume_match = re.fullmatch(r"(?:set )?volume(?: to)? (\d{1,3})(?: percent)?", normalized)
        if volume_match:
            level = int(volume_match.group(1))
            if 0 <= level <= 100:
                return DecisionResult(
                    action=SetVolume(level=level), confidence=1.0, source="fast_path"
                )

        app_match = re.fullmatch(r"open ([\w .+\-]+)", normalized)
        if app_match:
            requested = app_match.group(1).strip()
            matches = [
                app for app in context.app_names if app.casefold().removesuffix(".app") == requested
            ]
            if len(matches) == 1:
                return DecisionResult(
                    action=OpenApp(app_name=matches[0]), confidence=1.0, source="fast_path"
                )
        return None

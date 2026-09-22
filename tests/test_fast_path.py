import pytest

from local_assistant.actions.models import ActionKind
from local_assistant.decision.fast_path import DeterministicCommandParser
from local_assistant.runtime.types import DecisionContext


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("mute", ActionKind.MEDIA_CONTROL),
        ("next song", ActionKind.MEDIA_CONTROL),
        ("set volume to 25 percent", ActionKind.SET_VOLUME),
        ("take a screenshot", ActionKind.TAKE_SCREENSHOT),
        ("scroll down", ActionKind.SCROLL),
        ("page up", ActionKind.SCROLL),
        ('open that video with title "Imagine Dragons - Believer"', ActionKind.CLICK_ELEMENT),
        ("open calculator", ActionKind.OPEN_APP),
        ("search Google for OpenAI", ActionKind.SEARCH_WEB),
        ("search YouTube for Linux debugging tutorials", ActionKind.OPEN_URL),
    ],
)
def test_unambiguous_commands_take_fast_path(text: str, kind: ActionKind) -> None:
    result = DeterministicCommandParser().parse(
        text, DecisionContext(app_names=("Calculator", "Safari"))
    )
    assert result is not None
    assert result.action.kind is kind
    assert result.confidence == 1


def test_ambiguous_language_does_not_use_regex_guessing() -> None:
    result = DeterministicCommandParser().parse(
        "could you do something with my browser", DecisionContext(app_names=("Safari",))
    )
    assert result is None


def test_generative_search_request_does_not_take_literal_fast_path() -> None:
    result = DeterministicCommandParser().parse(
        "come up with a concise search query for Linux debugging",
        DecisionContext(app_names=("Safari",)),
    )
    assert result is None


def test_literal_search_preserves_user_supplied_query() -> None:
    result = DeterministicCommandParser().parse("Search Google for OpenAI", DecisionContext())
    assert result is not None
    assert result.action.query == "OpenAI"


def test_youtube_search_uses_literal_youtube_results_url() -> None:
    result = DeterministicCommandParser().parse(
        "Search YouTube for Imagine Dragons", DecisionContext()
    )
    assert result is not None
    assert str(result.action.url) == (
        "https://www.youtube.com/results?search_query=Imagine+Dragons"
    )


def test_exact_video_title_is_preserved_without_jev_or_writer() -> None:
    result = DeterministicCommandParser().parse(
        'Open that video with title "Imagine Dragons - Believer".', DecisionContext()
    )
    assert result is not None
    assert result.action.selector == "Imagine Dragons - Believer"
    assert result.source == "fast_path"

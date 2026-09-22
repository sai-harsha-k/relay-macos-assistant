import pytest
from pydantic import ValidationError

from local_assistant.actions.models import ActionKind, KeyboardShortcut, validate_action


def test_typed_action_discriminator_and_validation() -> None:
    action = validate_action({"kind": "SET_VOLUME", "level": 42})
    assert action.kind is ActionKind.SET_VOLUME
    assert action.level == 42


def test_action_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        validate_action({"kind": "OPEN_APP", "app_name": "Safari", "shell": "rm -rf /"})


@pytest.mark.parametrize("level", [-1, 101])
def test_volume_bounds(level: int) -> None:
    with pytest.raises(ValidationError):
        validate_action({"kind": "SET_VOLUME", "level": level})


def test_shortcut_rejects_unbounded_keys() -> None:
    with pytest.raises(ValidationError):
        KeyboardShortcut(keys=("command", "shell"))


def test_new_document_shortcut_is_valid() -> None:
    assert KeyboardShortcut(keys=("command", "n")).keys == ("command", "n")


def test_message_action_requires_explicit_recipient_and_content() -> None:
    action = validate_action(
        {
            "kind": "SEND_MESSAGE",
            "app_name": "WhatsApp",
            "recipient": "ABC",
            "content": "hello",
        }
    )
    assert action.kind is ActionKind.SEND_MESSAGE
    with pytest.raises(ValidationError):
        validate_action({"kind": "SEND_MESSAGE", "app_name": "WhatsApp", "recipient": "ABC"})


def test_language_fields_are_either_literal_or_generated_never_both() -> None:
    generated = validate_action(
        {
            "kind": "SEARCH_WEB",
            "query_generation_instruction": "Create a concise debugging query",
        }
    )
    assert generated.query is None
    with pytest.raises(ValidationError):
        validate_action(
            {
                "kind": "SEARCH_WEB",
                "query": "literal",
                "query_generation_instruction": "rewrite it",
            }
        )

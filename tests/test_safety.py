import pytest

from local_assistant.actions.models import (
    CloseApp,
    GenerateText,
    KeyboardShortcut,
    OpenApp,
    SendMessage,
    TypeText,
    WriteClipboard,
)
from local_assistant.safety.policy import RiskLevel, SafetyPolicy


def test_low_risk_reversible_action_executes_without_confirmation() -> None:
    assessment = SafetyPolicy().assess(OpenApp(app_name="Calculator"))
    assert assessment.risk is RiskLevel.LOW
    assert not assessment.requires_confirmation


@pytest.mark.parametrize(
    "action",
    [CloseApp(app_name="TextEdit"), TypeText(text="hello"), WriteClipboard(text="hello")],
)
def test_state_changing_action_requires_confirmation(action: object) -> None:
    assessment = SafetyPolicy().assess(action)
    assert assessment.requires_confirmation


def test_generation_is_data_only_unless_typing_requested() -> None:
    policy = SafetyPolicy()
    assert not policy.assess(GenerateText(instruction="draft a note")).requires_confirmation
    assert policy.assess(
        GenerateText(instruction="draft a note", type_after_generation=True)
    ).requires_confirmation


def test_local_editor_writing_does_not_require_confirmation() -> None:
    policy = SafetyPolicy()
    assert not policy.assess(TypeText(text="buy milk"), active_app="Notes").requires_confirmation
    assert not policy.assess(
        GenerateText(instruction="write a reminder", type_after_generation=True),
        active_app="TextEdit",
    ).requires_confirmation
    assert policy.assess(TypeText(text="hello"), active_app="WhatsApp").requires_confirmation


def test_bounded_calculator_expression_does_not_require_confirmation() -> None:
    policy = SafetyPolicy()
    assert not policy.assess(TypeText(text="2+2="), active_app="Calculator").requires_confirmation
    assert policy.assess(TypeText(text="send this"), active_app="Calculator").requires_confirmation


def test_new_document_shortcut_is_reversible_but_other_shortcuts_still_confirm() -> None:
    policy = SafetyPolicy()
    assert not policy.assess(KeyboardShortcut(keys=("command", "n"))).requires_confirmation
    assert policy.assess(KeyboardShortcut(keys=("command", "q"))).requires_confirmation


def test_auto_send_setting_only_changes_message_confirmation() -> None:
    message = SendMessage(app_name="WhatsApp", recipient="ABC", content="hello")
    policy = SafetyPolicy()
    assert policy.assess(message).requires_confirmation

    policy.set_auto_send_messages(True)
    assert not policy.assess(message).requires_confirmation
    assert policy.assess(TypeText(text="still consequential")).requires_confirmation
    assert policy.assess(CloseApp(app_name="Finder")).requires_confirmation

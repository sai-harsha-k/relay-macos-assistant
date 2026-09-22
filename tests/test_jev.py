from local_assistant.actions.models import ActionKind, ClickElement, SendMessage
from local_assistant.decision.jev import _message_field_candidates, convert_jev_response


def answer(choice: str, confidence: float) -> dict[str, object]:
    return {"choice": choice, "confidence": confidence}


def test_jev_response_converts_to_typed_search_action() -> None:
    result = convert_jev_response(
        "search for local weather",
        {"action": answer("SEARCH_WEB", 0.91), "payload": answer("p1", 0.84)},
        [],
        ["search for local weather", "local weather"],
    )
    assert result.action.kind is ActionKind.SEARCH_WEB
    assert result.action.query == "local weather"
    assert result.confidence == 0.84


def test_explicit_installed_app_is_extracted_without_relying_on_jev_app_confidence() -> None:
    result = convert_jev_response(
        "focus safari",
        {"action": answer("FOCUS_APP", 0.9), "app": answer("a1", 0.8)},
        ["Calculator", "Safari"],
        ["focus safari"],
    )
    assert result.action.kind is ActionKind.FOCUS_APP
    assert result.action.app_name == "Safari"
    assert result.confidence == 0.9


def test_missing_required_candidate_becomes_ask_user() -> None:
    result = convert_jev_response("open it", {"action": answer("OPEN_APP", 0.9)}, [], ["open it"])
    assert result.action.kind is ActionKind.ASK_USER


def test_generation_output_is_requested_as_data() -> None:
    result = convert_jev_response(
        "compose a friendly greeting",
        {"action": answer("GENERATE_TEXT", 0.9), "payload": answer("p1", 0.85)},
        [],
        ["compose a friendly greeting", "a friendly greeting"],
    )
    assert result.action.kind is ActionKind.GENERATE_TEXT
    assert result.action.instruction == "a friendly greeting"


def test_jev_marks_search_field_for_generation_without_changing_action() -> None:
    text = "come up with a concise YouTube query for beginner Linux debugging"
    result = convert_jev_response(
        text,
        {
            "action": answer("SEARCH_WEB", 0.96),
            "payload": answer("p0", 0.9),
            "value_mode": answer("generate", 0.94),
        },
        [],
        [text],
    )
    assert result.action.kind is ActionKind.SEARCH_WEB
    assert result.action.query is None
    assert result.action.query_generation_instruction == text
    assert result.confidence == 0.94


def test_jev_generated_message_keeps_bounded_recipient_and_application() -> None:
    result = convert_jev_response(
        "Send ABC a polite message asking what he is doing",
        {
            "action": answer("SEND_MESSAGE", 0.97),
            "value_mode": answer("generate", 0.95),
        },
        [],
        ["Send ABC a polite message asking what he is doing"],
        "WhatsApp",
    )
    assert result.action.kind is ActionKind.SEND_MESSAGE
    assert result.action.recipient == "ABC"
    assert result.action.app_name == "WhatsApp"
    assert result.action.content is None
    assert result.action.generation_instruction.startswith("Write a polite message")


def test_ambiguous_generated_message_becomes_ask_user() -> None:
    result = convert_jev_response(
        "send a nice message",
        {
            "action": answer("SEND_MESSAGE", 0.9),
            "value_mode": answer("generate", 0.9),
        },
        [],
        ["send a nice message"],
    )
    assert result.action.kind is ActionKind.ASK_USER


def test_jev_uses_bounded_literal_fields_for_compound_whatsapp_message() -> None:
    text = "Can you open WhatsApp and message Example Contact hello?"
    message_fields = _message_field_candidates(text)
    assert message_fields == [
        ("Example", "Contact hello"),
        ("Example Contact", "hello"),
    ]
    result = convert_jev_response(
        text,
        {
            "action": answer("SEND_MESSAGE", 0.97),
            "value_mode": answer("literal", 0.98),
            "message_fields": answer("m1", 0.96),
        },
        ["WhatsApp"],
        [text],
        message_fields=message_fields,
    )
    assert result.action == SendMessage(
        app_name="WhatsApp", recipient="Example Contact", content="hello"
    )
    assert result.confidence == 0.96


def test_jev_clicks_explicit_video_title_as_literal_without_generation() -> None:
    text = 'open that video with title "Imagine Dragons - Believer"'
    result = convert_jev_response(
        text,
        {
            "action": answer("CLICK_ELEMENT", 0.96),
            "payload": answer("p0", 0.91),
            "value_mode": answer("literal", 0.99),
        },
        [],
        [text, "Imagine Dragons - Believer"],
    )
    assert result.action == ClickElement(selector="Imagine Dragons - Believer")
    assert result.action.kind is ActionKind.CLICK_ELEMENT


def test_jev_does_not_guess_random_visible_video() -> None:
    text = "Select a random video here."
    result = convert_jev_response(
        text,
        {
            "action": answer("CLICK_ELEMENT", 0.91),
            "payload": answer("p0", 0.85),
        },
        [],
        [text],
    )
    assert result.action.kind is ActionKind.ASK_USER
    assert "visible title" in result.action.question

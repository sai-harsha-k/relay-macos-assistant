from pathlib import Path

from local_assistant.actions.models import AskUser, OpenApp, SendMessage, TypeText
from local_assistant.decision.router import DecisionRouter
from local_assistant.runtime.controller import AssistantController
from local_assistant.runtime.executor import ActionExecutor
from local_assistant.runtime.types import (
    DecisionContext,
    DecisionResult,
    PipelineEvent,
    PipelineStage,
)
from local_assistant.safety.policy import SafetyPolicy
from tests.conftest import FakeBrowser, FakePlatform, FakeTTS, FakeWriter


class UnusedSTT:
    def transcribe(self, audio_path: Path) -> str:
        return "open calculator"


class FixedDecision:
    def __init__(self, action: object) -> None:
        self.action = action

    def decide(self, text: str, context: DecisionContext) -> DecisionResult:
        return DecisionResult(self.action, 1.0, "fixed")


def confirmation_must_not_run(action: object, reason: str) -> bool:
    del action, reason
    raise AssertionError("this action must not ask for confirmation")


def controller_for(
    action: object, *, dry_run: bool = False, confirmation: object = None
) -> tuple[AssistantController, FakePlatform]:
    platform = FakePlatform()
    router = DecisionRouter(FixedDecision(action), 0.5)
    controller = AssistantController(
        UnusedSTT(),
        router,
        SafetyPolicy(),
        ActionExecutor(platform, FakeBrowser(), FakeWriter()),
        platform,
        FakeTTS(),
        dry_run=dry_run,
        confirmation=confirmation,
    )
    return controller, platform


def test_dry_run_has_no_side_effect() -> None:
    controller, platform = controller_for(OpenApp(app_name="Notes"), dry_run=True)
    result = controller.handle_text("do it")
    assert result.execution.success
    assert "Dry run" in result.execution.message
    assert platform.calls == []


def test_confirmation_requirement_blocks_execution() -> None:
    controller, platform = controller_for(TypeText(text="consequential"))
    result = controller.handle_text("type it")
    assert not result.execution.success
    assert "confirmation required" in result.execution.message
    assert platform.calls == []


def test_explicit_confirmation_allows_execution() -> None:
    controller, platform = controller_for(
        TypeText(text="approved"), confirmation=lambda action, reason: True
    )
    result = controller.handle_text("type it")
    assert result.execution.success
    assert platform.calls == [("type_text", "approved")]


def test_audio_path_records_transcription_and_end_to_end_timings(tmp_path: Path) -> None:
    controller, _platform = controller_for(OpenApp(app_name="Calculator"))
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"fake")
    result = controller.handle_audio(audio)
    assert result.transcript == "open calculator"
    assert result.execution.success
    assert "transcription" in result.timings_ms
    assert "end_to_end" in result.timings_ms


def test_actual_stt_transcript_and_jev_decision_are_emitted_to_pipeline(tmp_path: Path) -> None:
    events: list[PipelineEvent] = []
    platform = FakePlatform()

    class JevDecision:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            assert text == "could you open calculator for me"
            del context
            return DecisionResult(OpenApp(app_name="Calculator"), 0.94, "jev")

    class FinalSTT:
        def transcribe(self, audio_path: Path) -> str:
            del audio_path
            return "could you open calculator for me"

    controller = AssistantController(
        FinalSTT(),
        DecisionRouter(JevDecision(), 0.5),
        SafetyPolicy(),
        ActionExecutor(platform, FakeBrowser(), FakeWriter()),
        platform,
        FakeTTS(),
        pipeline_listener=events.append,
    )
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"fake")
    controller.handle_audio(audio)

    assert [event.stage for event in events] == [
        PipelineStage.TRANSCRIBING,
        PipelineStage.DECIDING,
        PipelineStage.EXECUTING,
        PipelineStage.DONE,
    ]
    assert events[1].transcript == "could you open calculator for me"
    assert events[2].route_source == "jev"
    assert events[2].confidence == 0.94
    assert events[2].action_target == "Calculator"


def test_non_generative_fast_path_works_without_ollama() -> None:
    class BrokenWriter:
        def generate(self, instruction: str, context: str | None = None) -> str:
            raise AssertionError("writer must not be called")

    platform = FakePlatform()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(None, 0.5),
        SafetyPolicy(),
        ActionExecutor(platform, FakeBrowser(), BrokenWriter()),
        platform,
        FakeTTS(),
    )
    result = controller.handle_text("mute")
    assert result.execution.success
    assert platform.calls == [("media_control", "mute")]


def test_commit_id_prevents_duplicate_action_execution() -> None:
    controller, platform = controller_for(OpenApp(app_name="Calculator"))
    prepared = controller.prepare_text("open Calculator")
    first = controller.commit_prepared(prepared, commit_id="utterance:action")
    duplicate = controller.commit_prepared(prepared, commit_id="utterance:action")
    assert first.execution.success
    assert duplicate.execution.data == {"duplicate": True}
    assert platform.calls == [("open_app", "Calculator")]


def test_final_transcript_after_early_commit_is_authoritative_without_reexecution(
    tmp_path: Path,
) -> None:
    controller, platform = controller_for(OpenApp(app_name="Calculator"))
    prepared = controller.prepare_text("open Calculater")
    controller.commit_prepared(prepared, commit_id="utterance:action")
    audio = tmp_path / "final.wav"
    audio.write_bytes(b"audio")
    finalized = controller.finalize_committed_audio(audio, "utterance:action")
    assert finalized.transcript == "open calculator"
    assert finalized.execution.data == {"duplicate": True}
    assert platform.calls == [("open_app", "Calculator")]


def test_session_context_carries_app_recipient_and_previous_command() -> None:
    controller, platform = controller_for(OpenApp(app_name="unused"))
    opened = controller.handle_text("open WhatsApp")
    assert opened.execution.success
    prompt = controller.handle_text("Message ABC")
    assert not prompt.execution.success
    assert "What should I tell ABC" in prompt.execution.message

    prepared = controller.prepare_text("Tell him what are you doing?")
    assert prepared.decision.action == SendMessage(
        app_name="WhatsApp", recipient="ABC", content="what are you doing?"
    )
    assert controller.session_context.active_app == "WhatsApp"
    assert controller.session_context.pending_recipient == "ABC"
    assert controller.session_context.previous_command == "Message ABC"
    assert platform.calls == [("open_app", "WhatsApp")]


def test_auto_send_off_requires_confirmation_and_on_sends_eligible_message() -> None:
    controller, platform = controller_for(OpenApp(app_name="unused"))
    controller.handle_text("open WhatsApp")
    controller.handle_text("Message ABC")
    blocked = controller.handle_text("Tell him hello")
    assert not blocked.execution.success
    assert [call for call in platform.calls if call[0] == "send_message"] == []

    controller.update_runtime_settings(
        confidence_threshold=0.55, auto_send_messages=True, tts_enabled=False
    )
    sent = controller.handle_text("Tell him hello")
    assert sent.execution.success
    assert platform.calls[-1] == ("send_message", ("WhatsApp", "ABC", "hello"))


def test_auto_send_never_bypasses_non_message_confirmation() -> None:
    controller, platform = controller_for(TypeText(text="dangerous"))
    controller.update_runtime_settings(
        confidence_threshold=0.55, auto_send_messages=True, tts_enabled=False
    )
    result = controller.handle_text("perform the action")
    assert not result.execution.success
    assert platform.calls == []


def test_compound_literal_app_and_search_use_no_writer() -> None:
    class SemanticMustNotRun:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            raise AssertionError("literal compound command must not call Jev")

    platform = FakePlatform()
    browser = FakeBrowser()
    writer = FakeWriter()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(SemanticMustNotRun(), 0.5),
        SafetyPolicy(),
        ActionExecutor(platform, browser, writer),
        platform,
        FakeTTS(),
    )
    result = controller.handle_text("Open Chrome and search for YouTube")
    assert result.execution.success
    assert platform.calls == [("open_app", "Chrome")]
    assert browser.calls == [("search_web", ("YouTube", "Chrome"))]
    assert writer.calls == []


def test_polite_compound_app_and_search_bypasses_jev_and_writer() -> None:
    class SemanticMustNotRun:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            raise AssertionError("explicit compound command must not call Jev")

    class ChromePlatform(FakePlatform):
        def list_applications(self) -> list[str]:
            return [*super().list_applications(), "Google Chrome"]

    platform = ChromePlatform()
    browser = FakeBrowser()
    writer = FakeWriter()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(SemanticMustNotRun(), 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, browser, writer),
        platform,
        FakeTTS(),
    )
    result = controller.handle_text("Can you open Google Chrome and search for YouTube?")
    assert result.execution.success
    assert platform.calls == [("open_app", "Google Chrome")]
    assert browser.calls == [("search_web", ("YouTube", "Google Chrome"))]
    assert writer.calls == []


def test_browser_suffix_alias_uses_native_named_browser_search() -> None:
    class SemanticMustNotRun:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            raise AssertionError("explicit browser search must not call Jev")

    platform = FakePlatform()
    browser = FakeBrowser()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(SemanticMustNotRun(), 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, browser, FakeWriter()),
        platform,
        FakeTTS(),
    )

    result = controller.handle_text("Can you open Safari browser and search for YouTube?")

    assert result.execution.success
    assert platform.calls == [("open_app", "Safari")]
    assert browser.calls == [("search_web", ("YouTube", "Safari"))]


def test_open_youtube_uses_default_browser_without_jev_or_writer() -> None:
    class SemanticMustNotRun:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            raise AssertionError("known website must not call Jev")

    browser = FakeBrowser()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(SemanticMustNotRun(), 0.55),
        SafetyPolicy(),
        ActionExecutor(FakePlatform(), browser, FakeWriter()),
        FakePlatform(),
        FakeTTS(),
    )

    result = controller.handle_text("Can you open YouTube?")

    assert result.execution.success
    assert browser.calls == [("open_url", "https://www.youtube.com/")]
    assert result.action.kind.value == "OPEN_URL"


def test_open_youtube_and_search_uses_direct_results_url_without_jev() -> None:
    class SemanticMustNotRun:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            raise AssertionError("literal YouTube search must not call Jev")

    platform = FakePlatform()
    browser = FakeBrowser()
    writer = FakeWriter()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(SemanticMustNotRun(), 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, browser, writer),
        platform,
        FakeTTS(),
    )

    result = controller.handle_text("Open YouTube and search for Imagine Dragons")

    assert result.execution.success
    assert browser.calls == [
        (
            "open_url",
            "https://www.youtube.com/results?search_query=Imagine+Dragons",
        )
    ]
    assert writer.calls == []


def test_multiple_literal_open_actions_execute_in_spoken_order_without_jev() -> None:
    class SemanticMustNotRun:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            raise AssertionError("literal app sequence must not call Jev")

    platform = FakePlatform()
    writer = FakeWriter()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(SemanticMustNotRun(), 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, FakeBrowser(), writer),
        platform,
        FakeTTS(),
    )

    result = controller.handle_text("Open WhatsApp and later open Calculator")

    assert result.execution.success
    assert platform.calls == [
        ("open_app", "WhatsApp"),
        ("open_app", "Calculator"),
    ]
    assert writer.calls == []


def test_literal_open_sequence_validates_every_app_before_any_execution() -> None:
    class ClarifyUnknownApp:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            return DecisionResult(
                AskUser(question="Which second application should I open?"),
                0.9,
                "jev",
            )

    platform = FakePlatform()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(ClarifyUnknownApp(), 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, FakeBrowser(), FakeWriter()),
        platform,
        FakeTTS(),
    )

    result = controller.handle_text("Open WhatsApp and then open Mystery App")

    assert not result.execution.success
    assert result.execution.message == "Which second application should I open?"
    assert platform.calls == []


def test_notes_then_calculator_executes_complete_mixed_typed_sequence() -> None:
    class SemanticMustNotRun:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            raise AssertionError("bounded mixed sequence must not call Jev")

    platform = FakePlatform()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(SemanticMustNotRun(), 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, FakeBrowser(), FakeWriter()),
        platform,
        FakeTTS(),
        confirmation=confirmation_must_not_run,
    )

    result = controller.handle_text(
        "Open Notes write down good morning and open Calculator add 2+2"
    )

    assert result.execution.success
    assert platform.calls == [
        ("open_app", "Notes"),
        ("focus_app", "Notes"),
        ("keyboard_shortcut", ("command", "n")),
        ("focus_app", "Notes"),
        ("type_text", "good morning"),
        ("open_app", "Calculator"),
        ("focus_app", "Calculator"),
        ("focus_app", "Calculator"),
        ("type_text", "2+2="),
    ]


def test_scroll_down_targets_observed_frontmost_application_without_jev() -> None:
    class SemanticMustNotRun:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            raise AssertionError("exact scroll command must not call Jev")

    platform = FakePlatform()
    platform.active_app = "Safari"
    browser = FakeBrowser()
    events: list[PipelineEvent] = []
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(SemanticMustNotRun(), 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, browser, FakeWriter()),
        platform,
        FakeTTS(),
        pipeline_listener=events.append,
    )

    result = controller.handle_text("scroll down")

    assert result.execution.success
    assert platform.calls == [("scroll", ("down", 600))]
    assert browser.calls == []
    assert controller.session_context.active_app == "Safari"
    assert controller.session_context.last_target == "Safari"
    executing = next(event for event in events if event.stage is PipelineStage.EXECUTING)
    assert executing.action_target == "Safari"


def test_notes_literal_content_uses_typed_actions_without_writer() -> None:
    platform = FakePlatform()
    writer = FakeWriter()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(None, 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, FakeBrowser(), writer),
        platform,
        FakeTTS(),
        confirmation=confirmation_must_not_run,
    )
    result = controller.handle_text("Could you open Notes and write this note: buy milk?")
    assert result.execution.success
    assert platform.calls == [
        ("open_app", "Notes"),
        ("focus_app", "Notes"),
        ("keyboard_shortcut", ("command", "n")),
        ("focus_app", "Notes"),
        ("type_text", "buy milk"),
    ]
    assert writer.calls == []


def test_create_new_note_while_notes_is_already_open_uses_command_n() -> None:
    class SemanticMustNotRun:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            raise AssertionError("explicit new-note command must not call Jev")

    platform = FakePlatform()
    platform.active_app = "Notes"
    writer = FakeWriter()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(SemanticMustNotRun(), 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, FakeBrowser(), writer),
        platform,
        FakeTTS(),
        confirmation=confirmation_must_not_run,
    )

    result = controller.handle_text("Create a new note in Notes and write this note: buy milk.")

    assert result.execution.success
    assert platform.calls == [
        ("open_app", "Notes"),
        ("focus_app", "Notes"),
        ("keyboard_shortcut", ("command", "n")),
        ("focus_app", "Notes"),
        ("type_text", "buy milk"),
    ]
    assert writer.calls == []


def test_create_blank_new_note_while_notes_is_already_open() -> None:
    class SemanticMustNotRun:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            raise AssertionError("explicit new-note command must not call Jev")

    platform = FakePlatform()
    platform.active_app = "Notes"
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(SemanticMustNotRun(), 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, FakeBrowser(), FakeWriter()),
        platform,
        FakeTTS(),
        confirmation=confirmation_must_not_run,
    )

    result = controller.handle_text("Create a new note in Notes.")

    assert result.execution.success
    assert platform.calls == [
        ("open_app", "Notes"),
        ("focus_app", "Notes"),
        ("keyboard_shortcut", ("command", "n")),
    ]


def test_notes_composition_uses_writer_only_for_note_text() -> None:
    platform = FakePlatform()
    writer = FakeWriter("Remember to call Mom.")
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(None, 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, FakeBrowser(), writer),
        platform,
        FakeTTS(),
        confirmation=confirmation_must_not_run,
        writer_model="qwen3.5:2b",
    )
    result = controller.handle_text("Open Notes and write a short note reminding me to call Mom")
    assert result.execution.success
    assert writer.calls == ["Write a short note reminding me to call Mom"]
    assert platform.calls == [
        ("open_app", "Notes"),
        ("focus_app", "Notes"),
        ("keyboard_shortcut", ("command", "n")),
        ("focus_app", "Notes"),
        ("type_text", "Remember to call Mom."),
    ]


def test_write_down_in_notes_extracts_only_literal_content_without_jev_or_writer() -> None:
    class SemanticMustNotRun:
        def decide(self, text: str, context: DecisionContext) -> DecisionResult:
            raise AssertionError("literal Notes command must not call Jev")

    platform = FakePlatform()
    writer = FakeWriter()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(SemanticMustNotRun(), 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, FakeBrowser(), writer),
        platform,
        FakeTTS(),
        confirmation=confirmation_must_not_run,
    )

    result = controller.handle_text("Can you write down buy milk in Notes?")

    assert result.execution.success
    assert platform.calls == [
        ("open_app", "Notes"),
        ("focus_app", "Notes"),
        ("keyboard_shortcut", ("command", "n")),
        ("focus_app", "Notes"),
        ("type_text", "buy milk"),
    ]
    assert writer.calls == []


def test_notes_missing_content_asks_specific_question_without_side_effects() -> None:
    platform = FakePlatform()
    writer = FakeWriter()
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(None, 0.55),
        SafetyPolicy(),
        ActionExecutor(platform, FakeBrowser(), writer),
        platform,
        FakeTTS(),
    )
    result = controller.handle_text("Open Notes and write something down for me")
    assert not result.execution.success
    assert result.execution.message == "What should the new note say?"
    assert platform.calls == []
    assert writer.calls == []


def test_direct_message_content_uses_no_writer() -> None:
    platform = FakePlatform()
    writer = FakeWriter("must not be used")
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(None, 0.5),
        SafetyPolicy(auto_send_messages=True),
        ActionExecutor(platform, FakeBrowser(), writer),
        platform,
        FakeTTS(),
    )
    result = controller.handle_text("Send ABC: what are you doing?")
    assert result.execution.success
    assert writer.calls == []
    assert platform.calls == [("send_message", ("Messages", "ABC", "what are you doing?"))]


def test_generated_message_uses_writer_without_changing_recipient_or_app() -> None:
    platform = FakePlatform()
    writer = FakeWriter("Hello from the writer")
    action = SendMessage(
        app_name="WhatsApp",
        recipient="ABC",
        generation_instruction="Write a polite greeting",
    )
    controller = AssistantController(
        UnusedSTT(),
        DecisionRouter(FixedDecision(action), 0.5),
        SafetyPolicy(auto_send_messages=True),
        ActionExecutor(platform, FakeBrowser(), writer),
        platform,
        FakeTTS(),
        writer_model="qwen3.5:2b",
    )
    result = controller.handle_text("Send ABC a polite greeting")
    assert result.action == action
    assert result.execution.success
    assert writer.calls == ["Write a polite greeting"]
    assert platform.calls == [("send_message", ("WhatsApp", "ABC", "Hello from the writer"))]

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from urllib.parse import quote_plus

import structlog
from pydantic import HttpUrl

from local_assistant.actions.models import (
    Action,
    AskUser,
    FocusApp,
    GenerateText,
    KeyboardShortcut,
    OpenApp,
    OpenUrl,
    Scroll,
    SearchWeb,
    SendMessage,
    TypeText,
)
from local_assistant.decision.router import DecisionRouter
from local_assistant.runtime.executor import ActionExecutor
from local_assistant.runtime.session_context import SessionContext
from local_assistant.runtime.types import (
    ControllerResult,
    DecisionContext,
    DecisionResult,
    ExecutionResult,
    PipelineEvent,
    PipelineStage,
    PlatformAdapter,
    SpeechToTextProvider,
    TextToSpeechProvider,
)
from local_assistant.safety.policy import SafetyAssessment, SafetyPolicy

ConfirmationCallback = Callable[[Action, str], bool]
PipelineListener = Callable[[PipelineEvent], None]


@dataclass(frozen=True, slots=True)
class PreparedAction:
    transcript: str
    decision: DecisionResult
    assessment: SafetyAssessment
    decision_ms: float
    action_target: str | None
    writer_model: str | None


def _action_target(action: Action) -> str | None:
    if isinstance(action, SendMessage):
        return action.recipient
    data = action.model_dump(mode="json", exclude={"kind"})
    for key in (
        "app_name",
        "query",
        "query_generation_instruction",
        "url",
        "instruction",
        "text",
        "selector",
        "key",
        "keys",
        "command",
        "level",
        "path",
        "direction",
        "recipient",
        "question",
        "generation_instruction",
    ):
        value = data.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, list):
            return " + ".join(str(item) for item in value)
        if key == "level":
            return f"{value}%"
        return str(value)
    return None


class AssistantController:
    def __init__(
        self,
        speech_to_text: SpeechToTextProvider,
        router: DecisionRouter,
        safety: SafetyPolicy,
        executor: ActionExecutor,
        platform: PlatformAdapter,
        tts: TextToSpeechProvider,
        dry_run: bool = False,
        confirmation: ConfirmationCallback | None = None,
        pipeline_listener: PipelineListener | None = None,
        writer_model: str | None = None,
    ) -> None:
        self._stt = speech_to_text
        self._router = router
        self._safety = safety
        self._executor = executor
        self._platform = platform
        self._tts = tts
        self._dry_run = dry_run
        self._confirmation = confirmation
        self._pipeline_listener = pipeline_listener
        self._writer_model = writer_model
        self._tts_enabled = True
        self._session_context = SessionContext()
        self._commit_lock = Lock()
        self._commits: dict[str, PreparedAction] = {}
        self._log = structlog.get_logger(__name__)

    def _emit(self, event: PipelineEvent) -> None:
        if self._pipeline_listener is not None:
            self._pipeline_listener(event)

    def handle_audio(self, audio_path: Path) -> ControllerResult:
        total_started = time.perf_counter()
        self._emit(PipelineEvent(PipelineStage.TRANSCRIBING))
        started = time.perf_counter()
        try:
            transcript = self._stt.transcribe(audio_path)
            stt_ms = (time.perf_counter() - started) * 1000
            return self._handle_text(
                transcript,
                total_started=total_started,
                initial_timings={"transcription": stt_ms},
            )
        except Exception as exc:
            self._emit(PipelineEvent(PipelineStage.ERROR, error=str(exc)))
            raise

    def preview_audio(self, audio_path: Path) -> str:
        """Transcribe a listening snapshot without routing or executing it."""
        return self._stt.transcribe(audio_path)

    def finalize_committed_audio(self, audio_path: Path, commit_id: str) -> ControllerResult:
        """Record the authoritative final transcript without repeating its utterance action."""
        total_started = time.perf_counter()
        self._emit(PipelineEvent(PipelineStage.TRANSCRIBING, commit_id=commit_id))
        started = time.perf_counter()
        transcript = self._stt.transcribe(audio_path)
        transcription_ms = (time.perf_counter() - started) * 1000
        with self._commit_lock:
            committed = self._commits.get(commit_id)
        if committed is None:
            raise RuntimeError("Early commit record is unavailable")
        decision = committed.decision
        timings = {
            "transcription": transcription_ms,
            "decision": committed.decision_ms,
            "execution": 0.0,
            "end_to_end": (time.perf_counter() - total_started) * 1000,
        }
        self._log.info(
            "early_commit_finalized",
            commit_id=commit_id,
            transcript=transcript,
            action=decision.action.kind,
            confidence=decision.confidence,
        )
        execution = ExecutionResult(True, "Action already committed", data={"duplicate": True})
        self._emit(
            PipelineEvent(
                PipelineStage.DONE,
                transcript=transcript,
                route_source=decision.source,
                confidence=decision.confidence,
                action=decision.action,
                action_target=committed.action_target,
                timings_ms=timings,
                result="Already executed from stable preview",
                writer_model=committed.writer_model,
                commit_id=commit_id,
            )
        )
        return ControllerResult(transcript, decision.action, execution, timings)

    def prepare_text(self, text: str) -> PreparedAction:
        """Resolve a typed action without executing it, for stable partial evaluation."""
        started = time.perf_counter()
        observed_app = self._platform.frontmost_app()
        if observed_app and observed_app.casefold() != "relay":
            self._session_context = self._session_context.with_active_app(observed_app)
        decision = self._contextual_message_decision(text)
        if decision is None:
            fields = self._session_context.decision_fields()
            context = DecisionContext(
                app_names=tuple(self._platform.list_applications()),
                active_app=fields.get("active_app"),
                last_action=fields.get("last_action"),
                last_target=fields.get("last_target"),
                previous_command=fields.get("previous_command"),
            )
            decision = self._router.decide(text, context)
        decision_ms = (time.perf_counter() - started) * 1000
        assessment = self._assess(decision.action)
        needs_writer = (
            decision.action.kind.value == "GENERATE_TEXT"
            or (
                isinstance(decision.action, SearchWeb)
                and decision.action.query_generation_instruction is not None
            )
            or (
                isinstance(decision.action, SendMessage)
                and decision.action.generation_instruction is not None
            )
        )
        writer_model = self._writer_model if needs_writer else None
        return PreparedAction(
            transcript=text,
            decision=decision,
            assessment=assessment,
            decision_ms=decision_ms,
            action_target=(
                observed_app
                if isinstance(decision.action, Scroll) and observed_app
                else _action_target(decision.action)
            ),
            writer_model=writer_model,
        )

    def _contextual_message_decision(self, text: str) -> DecisionResult | None:
        normalized = " ".join(text.strip().split())
        direct = re.fullmatch(
            r"(?:send|message|text)\s+(.+?)\s*(?::|\s+(?:saying|that)\s+)(.+)",
            normalized,
            re.IGNORECASE,
        )
        if direct:
            recipient, content = direct.groups()
            recipient, content = recipient.strip(), content.strip()
            app = self._session_context.active_app or "Messages"
            return DecisionResult(
                SendMessage(app_name=app, recipient=recipient, content=content), 1, "fast_path"
            )
        recipient_only = re.fullmatch(r"(?:message|text)\s+(.+)", normalized, re.IGNORECASE)
        if recipient_only and len(recipient_only.group(1).split()) <= 4:
            recipient = recipient_only.group(1).strip(" .?!")
            self._session_context = self._session_context.with_pending_recipient(recipient)
            return DecisionResult(
                AskUser(question=f"What should I tell {recipient}?"), 1, "fast_path"
            )
        follow_up = re.fullmatch(
            r"(?:tell|message|text)\s+(?:him|her|them)\s+(.+)",
            normalized,
            re.IGNORECASE,
        )
        if follow_up and self._session_context.pending_recipient:
            app = self._session_context.active_app or "Messages"
            return DecisionResult(
                SendMessage(
                    app_name=app,
                    recipient=self._session_context.pending_recipient,
                    content=follow_up.group(1),
                ),
                1,
                "fast_path",
            )
        return None

    @property
    def session_context(self) -> SessionContext:
        return self._session_context

    def update_runtime_settings(
        self, *, confidence_threshold: float, auto_send_messages: bool, tts_enabled: bool
    ) -> None:
        self._router.set_confidence_threshold(confidence_threshold)
        self._safety.set_auto_send_messages(auto_send_messages)
        self._tts_enabled = tts_enabled

    def handle_text(self, text: str) -> ControllerResult:
        return self._handle_text(text, total_started=time.perf_counter(), initial_timings={})

    def _handle_text(
        self,
        text: str,
        *,
        total_started: float,
        initial_timings: Mapping[str, float],
    ) -> ControllerResult:
        timings = dict(initial_timings)
        self._emit(
            PipelineEvent(
                PipelineStage.DECIDING,
                transcript=text,
                timings_ms=dict(timings),
            )
        )
        compound = self._literal_compound_actions(text)
        if compound:
            result: ControllerResult | None = None
            for action in compound:
                decision = DecisionResult(action, 1.0, "fast_path")
                prepared = PreparedAction(
                    text,
                    decision,
                    self._assess(action),
                    0.0,
                    _action_target(action),
                    self._writer_model if isinstance(action, GenerateText) else None,
                )
                result = self.commit_prepared(
                    prepared,
                    total_started=total_started,
                    initial_timings=timings,
                )
                if not result.execution.success:
                    return result
            if result is not None:
                return result
        prepared = self.prepare_text(text)
        return self.commit_prepared(
            prepared,
            total_started=total_started,
            initial_timings=timings,
        )

    def _literal_compound_actions(self, text: str) -> tuple[Action, ...] | None:
        normalized = " ".join(text.strip().rstrip(".!?").split())
        normalized = re.sub(
            r"^(?:(?:can|could|would) you(?: please)?|please)\s+",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
        explicit_new_note = re.fullmatch(
            r"(?:open|create|start|make)\s+(?:a\s+)?new\s+note(?:\s+in\s+notes)?"
            r"(?:\s+(?:and\s+)?(?:(?:write|type)(?:\s+down)?|saying|"
            r"with\s+(?:the\s+)?text)\s*:?[ ]*(.+))?",
            normalized,
            re.IGNORECASE,
        )
        if explicit_new_note:
            notes_app = self._installed_app("Notes")
            if notes_app is None:
                return None
            actions: list[Action] = [
                OpenApp(app_name=notes_app),
                FocusApp(app_name=notes_app),
                KeyboardShortcut(keys=("command", "n")),
            ]
            note_request = explicit_new_note.group(1)
            if note_request:
                actions.append(self._note_writing_action(note_request.strip()))
            return tuple(actions)

        youtube_search = re.fullmatch(
            r"open\s+(?:the\s+)?youtube\s+and\s+search(?:\s+for)?\s+(.+)",
            normalized,
            re.IGNORECASE,
        )
        if youtube_search:
            query = quote_plus(youtube_search.group(1).strip())
            return (OpenUrl(url=HttpUrl(f"https://www.youtube.com/results?search_query={query}")),)

        notes_then_calculator = re.fullmatch(
            r"open\s+notes\s+(?:and\s+)?(?:write|type)(?:\s+down)?\s+(.+?)\s+"
            r"(?:and\s+then\s+|then\s+|and\s+later\s+|and\s+)"
            r"open\s+calculator(?:\s+and)?\s+"
            r"(?:(?:calculate|compute|add)\s+)?(.+)",
            normalized,
            re.IGNORECASE,
        )
        if notes_then_calculator:
            note_text, requested_expression = notes_then_calculator.groups()
            notes_app = self._installed_app("Notes")
            calculator_app = self._installed_app("Calculator")
            expression = self._calculator_expression(requested_expression)
            if notes_app and calculator_app and note_text.strip() and expression:
                return (
                    OpenApp(app_name=notes_app),
                    FocusApp(app_name=notes_app),
                    KeyboardShortcut(keys=("command", "n")),
                    TypeText(text=note_text.strip()),
                    OpenApp(app_name=calculator_app),
                    FocusApp(app_name=calculator_app),
                    TypeText(text=f"{expression}="),
                )

        open_sequence = re.fullmatch(
            r"open\s+(.+?)\s+(?:and\s+(?:then\s+|later\s+)?|then\s+)open\s+(.+)",
            normalized,
            re.IGNORECASE,
        )
        if open_sequence:
            requested_apps = tuple(part.strip() for part in open_sequence.groups())
            apps = tuple(self._installed_app(requested) for requested in requested_apps)
            if all(app is not None for app in apps):
                return tuple(OpenApp(app_name=app) for app in apps if app is not None)

        website = re.fullmatch(
            r"open\s+(?:the\s+)?youtube(?:\s+(?:website|site))?",
            normalized,
            re.IGNORECASE,
        )
        if website:
            return (OpenUrl(url=HttpUrl("https://www.youtube.com")),)
        search = re.fullmatch(
            r"open\s+(.+?)\s+and\s+search(?:\s+for)?\s+(.+)",
            normalized,
            re.IGNORECASE,
        )
        if search:
            requested_app, query = search.groups()
            app = self._installed_app(requested_app)
            if app and query.strip():
                return OpenApp(app_name=app), SearchWeb(query=query.strip(), browser_app=app)

        open_then_write = re.fullmatch(
            r"open\s+(.+?)\s+(?:and\s+)?(?:write|type)\s+(.+)",
            normalized,
            re.IGNORECASE,
        )
        write_in_app = re.fullmatch(
            r"(?:write|type)(?:\s+down)?\s+(.+)\s+(?:in|into)\s+(?:the\s+)?(.+)",
            normalized,
            re.IGNORECASE,
        )
        if open_then_write:
            requested_app, note_request = open_then_write.groups()
        elif write_in_app:
            note_request, requested_app = write_in_app.groups()
        else:
            return None
        app = self._installed_app(requested_app)
        if app is None or app.casefold().removesuffix(".app") != "notes":
            return None
        note_request = note_request.strip()
        missing = {
            "a note",
            "this note",
            "something",
            "something down",
            "something down for me",
            "a note for me",
        }
        if note_request.casefold().strip(" :") in missing:
            return (
                AskUser(
                    question="What should the new note say?",
                    reason="No note content or writing instruction was provided.",
                ),
            )

        note_action = self._note_writing_action(note_request)
        return (
            OpenApp(app_name=app),
            FocusApp(app_name=app),
            KeyboardShortcut(keys=("command", "n")),
            note_action,
        )

    @staticmethod
    def _note_writing_action(note_request: str) -> Action:
        literal = re.fullmatch(
            r"(?:down\s+|(?:this|a)\s+note\s*:\s*)(.+)", note_request, re.IGNORECASE
        )
        quoted = re.fullmatch(r"""["'](.+)["']""", note_request)
        if literal or quoted:
            literal_match = literal if literal is not None else quoted
            if literal_match is None:  # pragma: no cover - narrowed by the condition
                raise RuntimeError("Note literal extraction failed")
            return TypeText(text=literal_match.group(1).strip())
        if re.search(
            r"\b(?:short|concise|brief|polite|professional|friendly|summarize|rewrite|"
            r"remind(?:ing)?|about)\b",
            note_request,
            re.IGNORECASE,
        ):
            return GenerateText(
                instruction=f"Write {note_request}",
                type_after_generation=True,
            )
        return TypeText(text=note_request)

    def _installed_app(self, requested_app: str) -> str | None:
        requested = re.sub(
            r"\s+(?:web\s+)?browser$", "", requested_app.strip(), flags=re.IGNORECASE
        )
        installed = tuple(self._platform.list_applications())
        candidates = [requested]
        aliases = {"chrome": "google chrome"}
        alias = aliases.get(requested.casefold())
        if alias is not None:
            candidates.append(alias)
        for candidate in candidates:
            apps = [
                app
                for app in installed
                if app.casefold().removesuffix(".app") == candidate.casefold()
            ]
            if len(apps) == 1:
                return apps[0]
        return None

    @staticmethod
    def _calculator_expression(value: str) -> str | None:
        expression = value.casefold().strip()
        replacements = (
            ("divided by", "/"),
            ("multiplied by", "*"),
            ("times", "*"),
            ("plus", "+"),
            ("minus", "-"),
        )
        for phrase, operator in replacements:
            expression = expression.replace(phrase, operator)
        expression = expression.replace(" ", "")
        if not re.fullmatch(r"[0-9+\-*/().%]+", expression):
            return None
        if not re.search(r"\d", expression):
            return None
        return expression

    def _assess(self, action: Action) -> SafetyAssessment:
        return self._safety.assess(action, active_app=self._session_context.active_app)

    def commit_prepared(
        self,
        prepared: PreparedAction,
        *,
        commit_id: str | None = None,
        total_started: float | None = None,
        initial_timings: Mapping[str, float] | None = None,
    ) -> ControllerResult:
        decision = prepared.decision
        if commit_id is not None:
            with self._commit_lock:
                committed = self._commits.get(commit_id)
                if committed is not None:
                    committed_decision = committed.decision
                    execution = ExecutionResult(
                        True,
                        "Action already committed",
                        data={"duplicate": True},
                    )
                    timings = {
                        **dict(initial_timings or {}),
                        "decision": prepared.decision_ms,
                        "execution": 0.0,
                        "end_to_end": (
                            (time.perf_counter() - total_started) * 1000
                            if total_started is not None
                            else 0.0
                        ),
                    }
                    self._emit(
                        PipelineEvent(
                            PipelineStage.DONE,
                            transcript=prepared.transcript,
                            route_source=committed_decision.source,
                            confidence=committed_decision.confidence,
                            action=committed_decision.action,
                            action_target=committed.action_target,
                            timings_ms=timings,
                            result="Already executed from stable preview",
                            writer_model=committed.writer_model,
                            commit_id=commit_id,
                        )
                    )
                    return ControllerResult(
                        prepared.transcript,
                        committed_decision.action,
                        execution,
                        timings,
                    )
                self._commits[commit_id] = prepared
        total_started = total_started if total_started is not None else time.perf_counter()
        timings = dict(initial_timings or {})
        timings["decision"] = prepared.decision_ms
        assessment = prepared.assessment
        self._log.info(
            "action_decided",
            action=decision.action.kind,
            source=decision.source,
            confidence=decision.confidence,
            risk=assessment.risk,
        )
        self._emit(
            PipelineEvent(
                PipelineStage.EXECUTING,
                transcript=prepared.transcript,
                route_source=decision.source,
                confidence=decision.confidence,
                action=decision.action,
                action_target=prepared.action_target,
                timings_ms=dict(timings),
                writer_model=prepared.writer_model,
                commit_id=commit_id,
            )
        )

        if self._dry_run:
            execution = ExecutionResult(
                True,
                f"Dry run: would execute {decision.action.kind}",
                data=decision.action.model_dump(mode="json"),
            )
            timings["execution"] = 0.0
        elif assessment.requires_confirmation and (
            self._confirmation is None or not self._confirmation(decision.action, assessment.reason)
        ):
            execution = ExecutionResult(False, "Action cancelled: confirmation required")
            timings["execution"] = 0.0
        else:
            started = time.perf_counter()
            focus_failure: ExecutionResult | None = None
            if self._types_into_focused_app(decision.action) and self._session_context.active_app:
                focus = self._platform.focus_app(self._session_context.active_app)
                if not focus.success:
                    focus_failure = ExecutionResult(
                        False,
                        f"Could not focus {self._session_context.active_app}: {focus.message}",
                    )
            execution = focus_failure or self._executor.execute(decision.action)
            execution_ms = (time.perf_counter() - started) * 1000
            timings["execution"] = execution_ms
            if execution.success and self._tts_enabled:
                self._tts.speak(execution.message)
        if execution.success or isinstance(decision.action, AskUser):
            self._session_context = self._session_context.after(
                prepared.transcript, decision.action, prepared.action_target
            )
        timings["end_to_end"] = (time.perf_counter() - total_started) * 1000
        stage = PipelineStage.DONE if execution.success else PipelineStage.ERROR
        self._emit(
            PipelineEvent(
                stage,
                transcript=prepared.transcript,
                route_source=decision.source,
                confidence=decision.confidence,
                action=decision.action,
                action_target=prepared.action_target,
                timings_ms=dict(timings),
                result=execution.message if execution.success else None,
                error=None if execution.success else execution.message,
                writer_model=prepared.writer_model,
                commit_id=commit_id,
            )
        )
        return ControllerResult(prepared.transcript, decision.action, execution, timings)

    @staticmethod
    def _types_into_focused_app(action: Action) -> bool:
        return isinstance(action, TypeText) or (
            isinstance(action, GenerateText) and action.type_after_generation
        )

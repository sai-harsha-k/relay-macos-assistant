from __future__ import annotations

from local_assistant.actions.models import AskUser
from local_assistant.decision.fast_path import DeterministicCommandParser
from local_assistant.runtime.errors import ProviderUnavailableError
from local_assistant.runtime.types import DecisionContext, DecisionProvider, DecisionResult


class DecisionRouter:
    def __init__(
        self,
        semantic_provider: DecisionProvider | None,
        confidence_threshold: float,
        fast_path: DeterministicCommandParser | None = None,
    ) -> None:
        self._semantic = semantic_provider
        self._threshold = confidence_threshold
        self._fast_path = fast_path or DeterministicCommandParser()

    def decide(self, text: str, context: DecisionContext) -> DecisionResult:
        if fast := self._fast_path.parse(text, context):
            return fast
        if self._semantic is None:
            return DecisionResult(
                action=AskUser(
                    question=(
                        "I need Jev to interpret that request. Configure TYPESAFE_API_KEY "
                        "or use an exact command."
                    ),
                    reason="Semantic routing is unavailable.",
                ),
                confidence=1.0,
                source="fallback",
            )
        try:
            decision = self._semantic.decide(text, context)
        except ProviderUnavailableError as exc:
            return DecisionResult(
                action=AskUser(question=str(exc), reason="Semantic routing failed."),
                confidence=1.0,
                source="fallback",
            )
        if decision.confidence < self._threshold:
            return DecisionResult(
                action=AskUser(
                    question="I am not confident enough to act. Could you be more specific?",
                    reason=(
                        f"Decision confidence {decision.confidence:.2f} is below "
                        f"{self._threshold:.2f}."
                    ),
                ),
                confidence=decision.confidence,
                source="confidence_gate",
            )
        return decision

    def set_confidence_threshold(self, threshold: float) -> None:
        if not 0 <= threshold <= 1:
            raise ValueError("confidence threshold must be between 0 and 1")
        self._threshold = threshold

from dataclasses import dataclass

from local_assistant.actions.models import ActionKind, SearchWeb
from local_assistant.decision.router import DecisionRouter
from local_assistant.runtime.types import DecisionContext, DecisionResult


@dataclass
class StubDecisionProvider:
    confidence: float

    def decide(self, text: str, context: DecisionContext) -> DecisionResult:
        del text, context
        return DecisionResult(SearchWeb(query="weather"), self.confidence, "stub")


def test_router_confidence_threshold_asks_user() -> None:
    result = DecisionRouter(StubDecisionProvider(0.49), 0.5).decide(
        "maybe search", DecisionContext()
    )
    assert result.action.kind is ActionKind.ASK_USER
    assert result.source == "confidence_gate"


def test_router_accepts_threshold_boundary() -> None:
    result = DecisionRouter(StubDecisionProvider(0.5), 0.5).decide(
        "search weather", DecisionContext()
    )
    assert result.action.kind is ActionKind.SEARCH_WEB


def test_missing_jev_is_clear_and_recoverable() -> None:
    result = DecisionRouter(None, 0.5).decide("search weather", DecisionContext())
    assert result.action.kind is ActionKind.ASK_USER
    assert "TYPESAFE_API_KEY" in result.action.question

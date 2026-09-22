import os

import pytest
from dotenv import load_dotenv

from local_assistant.actions.models import ActionKind
from local_assistant.decision.jev import TypeSafeJevDecisionProvider
from local_assistant.runtime.types import DecisionContext


@pytest.mark.integration
def test_real_jev_routes_search() -> None:
    load_dotenv()
    if not os.environ.get("TYPESAFE_API_KEY"):
        pytest.skip("TYPESAFE_API_KEY is absent")
    result = TypeSafeJevDecisionProvider().decide(
        "search the web for weather in Denver", DecisionContext(app_names=("Safari",))
    )
    assert result.action.kind is ActionKind.SEARCH_WEB
    assert result.confidence > 0

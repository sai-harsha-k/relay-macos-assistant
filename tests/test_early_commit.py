from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from local_assistant.actions.models import OpenApp, TypeText
from local_assistant.app.early_commit import EarlyCommit, EarlyCommitCoordinator
from local_assistant.runtime.controller import PreparedAction
from local_assistant.runtime.types import DecisionResult
from local_assistant.safety.policy import SafetyPolicy


@dataclass
class PendingCall:
    callback: Callable[[], None]
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if not self.cancelled:
            self.callback()


class FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[PendingCall] = []
        self.delay = 0.0

    def __call__(self, delay: float, callback: Callable[[], None]) -> PendingCall:
        self.delay = delay
        call = PendingCall(callback)
        self.calls.append(call)
        return call


def prepared(
    text: str,
    app: str = "Calculator",
    *,
    confidence: float = 0.97,
    source: str = "jev",
) -> PreparedAction:
    action = OpenApp(app_name=app)
    return PreparedAction(
        text,
        DecisionResult(action, confidence, source),
        SafetyPolicy().assess(action),
        12.0,
        app,
        None,
    )


def coordinator(
    commits: list[EarlyCommit],
    scheduler: FakeScheduler,
    revision: list[int],
) -> EarlyCommitCoordinator:
    return EarlyCommitCoordinator(
        "utterance-1",
        commits.append,
        lambda: revision[0],
        confidence_threshold=0.9,
        stability_ms=350,
        scheduler=scheduler,
    )


def test_stable_high_confidence_preview_commits_once() -> None:
    commits: list[EarlyCommit] = []
    scheduler = FakeScheduler()
    revision = [4]
    policy = coordinator(commits, scheduler, revision)
    assert policy.observe(prepared("open Calculator"), revision=4)
    assert scheduler.delay == 0.35
    scheduler.calls[-1].fire()
    assert len(commits) == 1
    assert commits[0].prepared.decision.action == OpenApp(app_name="Calculator")
    policy.observe(prepared("open Calculator"), revision=4)
    assert len(commits) == 1


def test_changing_preview_and_correction_cancel_previous_candidate() -> None:
    commits: list[EarlyCommit] = []
    scheduler = FakeScheduler()
    revision = [1]
    policy = coordinator(commits, scheduler, revision)
    policy.observe(prepared("open Calculator"), revision=1)
    calculator_call = scheduler.calls[-1]
    revision[0] = 2
    policy.observe(prepared("open Safari", app="Safari"), revision=2)
    calculator_call.fire()
    assert commits == []
    scheduler.calls[-1].fire()
    assert commits[0].prepared.decision.action == OpenApp(app_name="Safari")


def test_audio_change_after_preview_prevents_commit() -> None:
    commits: list[EarlyCommit] = []
    scheduler = FakeScheduler()
    revision = [7]
    policy = coordinator(commits, scheduler, revision)
    policy.observe(prepared("open Calculator"), revision=7)
    revision[0] = 8
    scheduler.calls[-1].fire()
    assert commits == []


def test_unfinished_or_low_confidence_preview_never_commits() -> None:
    commits: list[EarlyCommit] = []
    scheduler = FakeScheduler()
    revision = [1]
    policy = coordinator(commits, scheduler, revision)
    assert not policy.observe(prepared("open"), revision=1)
    assert not policy.observe(prepared("open Calculator", confidence=0.89), revision=1)
    assert scheduler.calls == []


def test_consequential_action_never_commits_early() -> None:
    action = TypeText(text="hello")
    candidate = PreparedAction(
        "type hello",
        DecisionResult(action, 0.99, "jev"),
        SafetyPolicy().assess(action),
        10.0,
        "hello",
        None,
    )
    commits: list[EarlyCommit] = []
    scheduler = FakeScheduler()
    policy = coordinator(commits, scheduler, [1])
    assert not policy.observe(candidate, revision=1)
    assert scheduler.calls == []

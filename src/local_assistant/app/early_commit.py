from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from local_assistant.actions.models import ActionKind
from local_assistant.runtime.controller import PreparedAction
from local_assistant.safety.policy import RiskLevel


class Cancellable(Protocol):
    def cancel(self) -> None: ...


class Scheduler(Protocol):
    def __call__(self, delay: float, callback: Callable[[], None]) -> Cancellable: ...


def _schedule_timer(delay: float, callback: Callable[[], None]) -> Cancellable:
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return timer


_UNFINISHED_WORDS = {
    "a",
    "an",
    "and",
    "actually",
    "at",
    "for",
    "in",
    "of",
    "on",
    "open",
    "or",
    "search",
    "the",
    "to",
    "with",
}
_EARLY_ACTIONS = {
    ActionKind.OPEN_APP,
    ActionKind.FOCUS_APP,
    ActionKind.OPEN_URL,
    ActionKind.SEARCH_WEB,
    ActionKind.SCROLL,
    ActionKind.SET_VOLUME,
    ActionKind.MEDIA_CONTROL,
    ActionKind.OPEN_FOLDER,
    ActionKind.FIND_FILE,
    ActionKind.READ_CLIPBOARD,
    ActionKind.TAKE_SCREENSHOT,
}


def transcript_looks_complete(text: str) -> bool:
    normalized = " ".join(text.casefold().strip().split())
    if len(normalized.split()) < 2:
        return normalized in {"mute", "unmute", "play", "pause", "screenshot"}
    if normalized.endswith(("-", "—", ",", ":", ";", "...")):
        return False
    return normalized.rsplit(" ", 1)[-1].strip(".!?") not in _UNFINISHED_WORDS


@dataclass(frozen=True, slots=True)
class EarlyCommit:
    commit_id: str
    prepared: PreparedAction


class EarlyCommitCoordinator:
    """Commits one stable, low-risk typed action per microphone utterance."""

    def __init__(
        self,
        utterance_id: str,
        commit: Callable[[EarlyCommit], None],
        revision_provider: Callable[[], int],
        *,
        confidence_threshold: float,
        stability_ms: int,
        scheduler: Scheduler = _schedule_timer,
    ) -> None:
        self._utterance_id = utterance_id
        self._commit = commit
        self._revision_provider = revision_provider
        self._confidence_threshold = confidence_threshold
        self._stability_seconds = stability_ms / 1000
        self._scheduler = scheduler
        self._pending: Cancellable | None = None
        self._token = 0
        self._committed = False
        self._lock = threading.Lock()

    @property
    def has_pending(self) -> bool:
        with self._lock:
            return self._pending is not None and not self._committed

    def observe(self, prepared: PreparedAction, revision: int) -> bool:
        decision = prepared.decision
        eligible = (
            not self._committed
            and prepared.assessment.risk is RiskLevel.LOW
            and not prepared.assessment.requires_confirmation
            and decision.action.kind in _EARLY_ACTIONS
            and decision.action.kind is not ActionKind.ASK_USER
            and transcript_looks_complete(prepared.transcript)
            and (
                decision.source == "fast_path"
                or (decision.source == "jev" and decision.confidence >= self._confidence_threshold)
            )
        )
        if not eligible:
            self._clear_pending()
            return False

        action_payload = decision.action.model_dump(mode="json")
        action_key = json.dumps(action_payload, sort_keys=True, default=str)
        with self._lock:
            self._token += 1
            token = self._token
            pending, self._pending = self._pending, None
        if pending is not None:
            pending.cancel()

        def stable_commit() -> None:
            with self._lock:
                if self._committed or token != self._token:
                    return
                if self._revision_provider() != revision:
                    return
                self._committed = True
                self._pending = None
            digest = hashlib.sha256(action_key.encode()).hexdigest()[:16]
            self._commit(EarlyCommit(f"{self._utterance_id}:{digest}", prepared))

        pending = self._scheduler(self._stability_seconds, stable_commit)
        with self._lock:
            if token == self._token and not self._committed:
                self._pending = pending
            else:
                pending.cancel()
        return True

    def _clear_pending(self) -> None:
        with self._lock:
            self._token += 1
            pending, self._pending = self._pending, None
        if pending is not None:
            pending.cancel()

    def close(self) -> None:
        self._clear_pending()

from __future__ import annotations

from dataclasses import dataclass, replace

from local_assistant.actions.models import Action, AskUser, FocusApp, OpenApp, SendMessage


@dataclass(frozen=True, slots=True)
class SessionContext:
    active_app: str | None = None
    last_action: str | None = None
    last_target: str | None = None
    previous_command: str | None = None
    pending_recipient: str | None = None

    def decision_fields(self) -> dict[str, str]:
        values = {
            "active_app": self.active_app,
            "last_action": self.last_action,
            "last_target": self.last_target,
            "previous_command": self.previous_command,
        }
        return {key: value for key, value in values.items() if value}

    def with_pending_recipient(self, recipient: str) -> SessionContext:
        return replace(self, pending_recipient=recipient, last_target=recipient)

    def with_active_app(self, app_name: str) -> SessionContext:
        return replace(self, active_app=app_name)

    def after(self, command: str, action: Action, target: str | None) -> SessionContext:
        active_app = self.active_app
        pending_recipient = self.pending_recipient
        if isinstance(action, (OpenApp, FocusApp)):
            active_app = action.app_name
        if isinstance(action, SendMessage):
            active_app = action.app_name
            pending_recipient = None
        if isinstance(action, AskUser):
            return replace(self, previous_command=command)
        return SessionContext(
            active_app=active_app,
            last_action=action.kind.value,
            last_target=target,
            previous_command=command,
            pending_recipient=pending_recipient,
        )

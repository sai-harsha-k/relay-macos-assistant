from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from local_assistant.app.hotkey import HotkeySpec


class RelayPreferences(BaseModel):
    """Non-secret, user-facing Relay preferences."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    continuous_listening: bool = False
    auto_send_messages: bool = False
    jev_confidence_threshold: float = Field(default=0.55, ge=0, le=1)
    silence_endpoint_ms: int = Field(default=550, ge=300, le=3000)
    transcript_stability_ms: int = Field(default=400, ge=250, le=2000)
    tts_enabled: bool = True
    overlay_always_on_top: bool = True
    overlay_x: float | None = None
    overlay_y: float | None = None
    local_model_name: str = Field(default="qwen3.5:2b", min_length=1, max_length=200)
    push_to_talk_hotkey: str = "command+option+space"

    @field_validator("push_to_talk_hotkey")
    @classmethod
    def validate_hotkey(cls, value: str) -> str:
        HotkeySpec.parse(value)
        return value


class PreferencesService:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> RelayPreferences:
        try:
            payload: Any = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and "push_to_talk_hotkey" not in payload:
                payload["continuous_listening"] = False
            return RelayPreferences.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError, TypeError):
            return RelayPreferences()

    def save(self, preferences: RelayPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(preferences.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.path)

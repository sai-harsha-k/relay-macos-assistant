from pathlib import Path

import pytest
from pydantic import ValidationError

from local_assistant.app.preferences import PreferencesService, RelayPreferences


def test_preferences_use_safe_defaults_when_file_is_missing(tmp_path: Path) -> None:
    preferences = PreferencesService(tmp_path / "settings.json").load()
    assert not preferences.continuous_listening
    assert not preferences.auto_send_messages
    assert preferences.push_to_talk_hotkey == "command+option+space"
    assert preferences.silence_endpoint_ms == 550
    assert preferences.transcript_stability_ms == 400


def test_preferences_round_trip_without_secrets(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    service = PreferencesService(path)
    expected = RelayPreferences(
        continuous_listening=False,
        auto_send_messages=True,
        jev_confidence_threshold=0.78,
        silence_endpoint_ms=600,
        transcript_stability_ms=450,
        tts_enabled=False,
        local_model_name="qwen3.5:2b",
    )
    service.save(expected)
    assert service.load() == expected
    assert "TYPESAFE_API_KEY" not in path.read_text(encoding="utf-8")


def test_auto_send_can_persist_the_continuous_interaction_mode(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    service = PreferencesService(path)
    preferences = RelayPreferences(
        auto_send_messages=True,
        continuous_listening=True,
    )
    service.save(preferences)
    assert service.load().auto_send_messages
    assert service.load().continuous_listening


def test_malformed_preferences_fall_back_without_crashing(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"silence_endpoint_ms": "broken"', encoding="utf-8")
    assert PreferencesService(path).load() == RelayPreferences()


def test_existing_continuous_defaults_migrate_to_push_to_talk(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"continuous_listening": true}', encoding="utf-8")
    preferences = PreferencesService(path).load()
    assert not preferences.continuous_listening
    assert preferences.push_to_talk_hotkey == "command+option+space"


def test_two_modifier_hotkey_is_valid_but_one_modifier_is_rejected() -> None:
    assert RelayPreferences(push_to_talk_hotkey="control+command").push_to_talk_hotkey == (
        "control+command"
    )
    with pytest.raises(ValidationError, match="needs at least two modifiers"):
        RelayPreferences(push_to_talk_hotkey="command")

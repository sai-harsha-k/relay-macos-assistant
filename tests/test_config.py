from pathlib import Path

import pytest

from local_assistant.config.settings import Settings


def test_configuration_loading_and_types(tmp_path: Path) -> None:
    settings = Settings.load(
        env_file=None,
        environ={
            "TYPESAFE_API_KEY": "unit-test-value",
            "ASSISTANT_WHISPER_MODEL": "tiny.en",
            "ASSISTANT_DECISION_CONFIDENCE": "0.72",
            "ASSISTANT_EARLY_COMMIT_CONFIDENCE": "0.96",
            "ASSISTANT_EARLY_COMMIT_STABILITY_MS": "375",
            "ASSISTANT_DRY_RUN": "yes",
            "ASSISTANT_TTS": "no",
        },
    )
    assert settings.whisper_model == "tiny.en"
    assert settings.decision_confidence == 0.72
    assert settings.early_commit_confidence == 0.96
    assert settings.early_commit_stability_ms == 375
    assert settings.dry_run
    assert not settings.tts_enabled
    assert settings.has_typesafe_key


def test_secret_safe_dump_never_contains_key() -> None:
    settings = Settings.load(env_file=None, environ={"TYPESAFE_API_KEY": "unit-test-value"})
    serialized = repr(settings.secret_safe_dict())
    assert "top-secret" not in serialized
    assert settings.secret_safe_dict()["typesafe_api_key_configured"] is True


def test_invalid_boolean_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be true or false"):
        Settings.load(env_file=None, environ={"ASSISTANT_DRY_RUN": "perhaps"})


def test_api_key_is_optional_at_startup() -> None:
    settings = Settings.load(env_file=None, environ={})
    assert not settings.has_typesafe_key

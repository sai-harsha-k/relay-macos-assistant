from __future__ import annotations

from pathlib import Path
from typing import Any

from local_assistant.app.health import HealthLevel, HealthService
from local_assistant.app.permissions import macos_accessibility_trusted
from local_assistant.config.settings import Settings


class GrantedPermissions:
    def microphone(self) -> tuple[bool, str]:
        return True, "granted"

    def accessibility(self) -> tuple[bool, str]:
        return True, "granted"

    def screen_recording(self) -> tuple[bool, str]:
        return True, "granted"


def test_accessibility_check_uses_native_application_services_symbol() -> None:
    class Check:
        def __init__(self) -> None:
            self.argtypes: list[object] = [object()]
            self.restype: object = object()

        def __call__(self) -> int:
            return 1

    class Framework:
        AXIsProcessTrusted = Check()

    loaded: list[str] = []

    def load(path: str) -> Any:
        loaded.append(path)
        return Framework()

    assert macos_accessibility_trusted(load)
    assert loaded == [
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    ]
    assert Framework.AXIsProcessTrusted.argtypes == []


def test_health_reports_dependencies_without_exposing_secret(tmp_path: Path) -> None:
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    settings = Settings(
        typesafe_api_key="unit-test-value",
        whisper_binary="whisper-cli",
        whisper_model_path=model,
    )
    report = HealthService(
        settings,
        GrantedPermissions(),
        which=lambda command: f"/bin/{command}",
        ollama_probe=lambda url, name: (True, True, "reachable"),
    ).run()
    assert not report.has_errors
    assert all(check.level is HealthLevel.OK for check in report.checks)
    assert "super-secret" not in report.format()


def test_unavailable_ollama_is_warning_not_fatal(tmp_path: Path) -> None:
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    report = HealthService(
        Settings(whisper_model_path=model),
        GrantedPermissions(),
        which=lambda command: f"/bin/{command}",
        ollama_probe=lambda url, name: (False, False, "unreachable"),
    ).run()
    assert not report.has_errors
    ollama = next(check for check in report.checks if check.name == "Ollama")
    assert ollama.level is HealthLevel.WARNING
    assert "generative commands" in report.format()

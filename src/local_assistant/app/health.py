from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import httpx

from local_assistant.app.permissions import macos_accessibility_trusted
from local_assistant.config.settings import Settings


class HealthLevel(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    level: HealthLevel
    detail: str


@dataclass(frozen=True, slots=True)
class HealthReport:
    checks: tuple[HealthCheck, ...]

    @property
    def has_errors(self) -> bool:
        return any(check.level is HealthLevel.ERROR for check in self.checks)

    def format(self) -> str:
        symbols = {
            HealthLevel.OK: "✓",
            HealthLevel.WARNING: "!",
            HealthLevel.ERROR: "✗",
        }
        return "\n".join(
            f"{symbols[check.level]} {check.name}: {check.detail}" for check in self.checks
        )


class PermissionProbe(Protocol):
    def microphone(self) -> tuple[bool, str]: ...
    def accessibility(self) -> tuple[bool, str]: ...
    def screen_recording(self) -> tuple[bool, str]: ...


class MacOSPermissionProbe:
    def microphone(self) -> tuple[bool, str]:
        try:
            import AVFoundation  # type: ignore[import-untyped]

            status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
                AVFoundation.AVMediaTypeAudio
            )
            authorized = status == AVFoundation.AVAuthorizationStatusAuthorized
            detail = "granted" if authorized else "not granted; enable Relay in Privacy & Security"
            return authorized, detail
        except Exception as exc:
            return False, f"could not determine ({exc})"

    def accessibility(self) -> tuple[bool, str]:
        try:
            trusted = macos_accessibility_trusted()
            detail = "granted" if trusted else "not granted; enable Relay in Accessibility"
            return trusted, detail
        except Exception as exc:
            return False, f"could not determine ({exc})"

    def screen_recording(self) -> tuple[bool, str]:
        try:
            import Quartz  # type: ignore[import-untyped]

            preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
            if preflight is None:
                return False, "status API unavailable"
            granted = bool(preflight())
            detail = "granted" if granted else "not granted; required only for screenshots"
            return granted, detail
        except Exception as exc:
            return False, f"could not determine ({exc})"


OllamaProbe = Callable[[str, str], tuple[bool, bool, str]]


def probe_ollama(base_url: str, model: str) -> tuple[bool, bool, str]:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=2.0)
        response.raise_for_status()
        payload: Any = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return False, False, f"unreachable ({exc})"
    models = payload.get("models", []) if isinstance(payload, dict) else []
    names = {item.get("name") for item in models if isinstance(item, dict)}
    found = model in names or f"{model}:latest" in names
    return True, found, "reachable" if found else f"reachable; model {model!r} is not installed"


class HealthService:
    def __init__(
        self,
        settings: Settings,
        permissions: PermissionProbe,
        *,
        which: Callable[[str], str | None] = shutil.which,
        path_exists: Callable[[Path], bool] = Path.is_file,
        ollama_probe: OllamaProbe = probe_ollama,
    ) -> None:
        self._settings = settings
        self._permissions = permissions
        self._which = which
        self._path_exists = path_exists
        self._ollama_probe = ollama_probe

    def run(self) -> HealthReport:
        checks: list[HealthCheck] = []
        for name, required, probe in (
            ("Microphone permission", True, self._permissions.microphone),
            ("Accessibility permission", True, self._permissions.accessibility),
            ("Screen Recording permission", False, self._permissions.screen_recording),
        ):
            granted, detail = probe()
            level = (
                HealthLevel.OK
                if granted
                else HealthLevel.ERROR
                if required
                else HealthLevel.WARNING
            )
            checks.append(HealthCheck(name, level, detail))

        binary = self._settings.whisper_binary
        binary_path = str(Path(binary)) if Path(binary).is_file() else self._which(binary)
        checks.append(
            HealthCheck(
                "whisper.cpp",
                HealthLevel.OK if binary_path else HealthLevel.ERROR,
                binary_path or f"{binary!r} was not found",
            )
        )
        model_path = self._settings.whisper_model_path.expanduser()
        model_exists = self._path_exists(model_path)
        checks.append(
            HealthCheck(
                "Whisper model",
                HealthLevel.OK if model_exists else HealthLevel.ERROR,
                str(model_path) if model_exists else f"missing: {model_path}",
            )
        )
        checks.append(
            HealthCheck(
                "TypeSafe Jev",
                HealthLevel.OK if self._settings.has_typesafe_key else HealthLevel.WARNING,
                "credential configured"
                if self._settings.has_typesafe_key
                else "TYPESAFE_API_KEY is absent; deterministic commands still work",
            )
        )
        reachable, model_found, ollama_detail = self._ollama_probe(
            self._settings.ollama_url, self._settings.ollama_model
        )
        checks.append(
            HealthCheck(
                "Ollama",
                HealthLevel.OK if reachable else HealthLevel.WARNING,
                ollama_detail,
            )
        )
        checks.append(
            HealthCheck(
                "Ollama model",
                HealthLevel.OK if model_found else HealthLevel.WARNING,
                self._settings.ollama_model
                if model_found
                else "unavailable; only generative commands are affected",
            )
        )
        return HealthReport(tuple(checks))

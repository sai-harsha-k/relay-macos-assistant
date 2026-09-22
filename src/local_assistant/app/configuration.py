from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppConfiguration:
    path: Path
    created: bool


def relay_support_directory() -> Path:
    return Path.home() / "Library" / "Application Support" / "Relay"


def ensure_relay_configuration(path: Path | None = None) -> AppConfiguration:
    target = path or relay_support_directory() / ".env"
    if target.exists():
        return AppConfiguration(target, False)
    target.parent.mkdir(parents=True, exist_ok=True)
    model = target.parent / "models" / "ggml-base.en.bin"
    whisper = next(
        (
            candidate
            for candidate in (
                Path("/opt/homebrew/bin/whisper-cli"),
                Path("/usr/local/bin/whisper-cli"),
            )
            if candidate.is_file()
        ),
        Path("whisper-cli"),
    )
    content = (
        "# Relay local configuration. Add secrets manually; this file is never committed.\n"
        "TYPESAFE_API_KEY=\n"
        f"ASSISTANT_WHISPER_BINARY={whisper}\n"
        f"ASSISTANT_WHISPER_MODEL_PATH={model}\n"
        "ASSISTANT_WHISPER_MODEL=base.en\n"
        "ASSISTANT_WHISPER_USE_GPU=true\n"
        "ASSISTANT_OLLAMA_URL=http://127.0.0.1:11434\n"
        "ASSISTANT_EARLY_COMMIT_CONFIDENCE=0.90\n"
        "ASSISTANT_LIVE_PREVIEW_INTERVAL_MS=400\n"
        "ASSISTANT_DRY_RUN=false\n"
        "ASSISTANT_LOG_LEVEL=INFO\n"
        "ASSISTANT_SEARCH_ENGINE=https://www.google.com/search?q={query}\n"
        "ASSISTANT_BROWSER_HEADLESS=false\n"
    )
    target.write_text(content, encoding="utf-8")
    os.chmod(target, 0o600)
    return AppConfiguration(target, True)

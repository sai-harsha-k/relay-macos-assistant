from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    typesafe_api_key: SecretStr | None = None
    whisper_model: str = "base.en"
    whisper_model_path: Path = Path("models/ggml-base.en.bin")
    whisper_binary: str = "whisper-cli"
    whisper_use_gpu: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:2b"
    decision_confidence: float = Field(default=0.55, ge=0, le=1)
    early_commit_confidence: float = Field(default=0.9, ge=0, le=1)
    early_commit_stability_ms: int = Field(default=350, ge=250, le=2000)
    live_preview_interval_ms: int = Field(default=400, ge=250, le=5000)
    utterance_silence_ms: int = Field(default=900, ge=500, le=5000)
    dry_run: bool = False
    tts_enabled: bool = True
    log_level: str = "INFO"
    search_engine: str = "https://www.google.com/search?q={query}"
    browser_headless: bool = False

    @field_validator("search_engine")
    @classmethod
    def require_query_placeholder(cls, value: str) -> str:
        if "{query}" not in value:
            raise ValueError("search engine URL must include {query}")
        return value

    @classmethod
    def load(
        cls,
        env_file: Path | None = Path(".env"),
        environ: Mapping[str, str] | None = None,
    ) -> Settings:
        if env_file is not None:
            load_dotenv(env_file, override=False)
        source = os.environ if environ is None else environ

        def optional(name: str) -> str | None:
            value = source.get(name)
            return value if value else None

        def boolean(name: str, default: bool) -> bool:
            value = optional(name)
            if value is None:
                return default
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
            raise ValueError(f"{name} must be true or false")

        key = optional("TYPESAFE_API_KEY")
        return cls(
            typesafe_api_key=SecretStr(key) if key else None,
            whisper_model=source.get("ASSISTANT_WHISPER_MODEL", "base.en"),
            whisper_model_path=Path(
                source.get("ASSISTANT_WHISPER_MODEL_PATH", "models/ggml-base.en.bin")
            ).expanduser(),
            whisper_binary=source.get("ASSISTANT_WHISPER_BINARY", "whisper-cli"),
            whisper_use_gpu=boolean("ASSISTANT_WHISPER_USE_GPU", True),
            ollama_url=source.get("ASSISTANT_OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_model=source.get("ASSISTANT_OLLAMA_MODEL", "qwen3.5:2b"),
            decision_confidence=float(source.get("ASSISTANT_DECISION_CONFIDENCE", "0.55")),
            early_commit_confidence=float(source.get("ASSISTANT_EARLY_COMMIT_CONFIDENCE", "0.9")),
            early_commit_stability_ms=int(source.get("ASSISTANT_EARLY_COMMIT_STABILITY_MS", "350")),
            live_preview_interval_ms=int(source.get("ASSISTANT_LIVE_PREVIEW_INTERVAL_MS", "400")),
            utterance_silence_ms=int(source.get("ASSISTANT_UTTERANCE_SILENCE_MS", "900")),
            dry_run=boolean("ASSISTANT_DRY_RUN", False),
            tts_enabled=boolean("ASSISTANT_TTS", True),
            log_level=source.get("ASSISTANT_LOG_LEVEL", "INFO").upper(),
            search_engine=source.get(
                "ASSISTANT_SEARCH_ENGINE", "https://www.google.com/search?q={query}"
            ),
            browser_headless=boolean("ASSISTANT_BROWSER_HEADLESS", False),
        )

    @property
    def has_typesafe_key(self) -> bool:
        return self.typesafe_api_key is not None

    def secret_safe_dict(self) -> dict[str, object]:
        data = self.model_dump(exclude={"typesafe_api_key"}, mode="json")
        data["typesafe_api_key_configured"] = self.has_typesafe_key
        return data

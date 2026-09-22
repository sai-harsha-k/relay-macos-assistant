from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from local_assistant.actions.models import Action
from local_assistant.audio.whisper_cpp import WhisperCppSpeechToTextProvider
from local_assistant.browser.playwright_adapter import PlaywrightBrowserAdapter
from local_assistant.config.settings import Settings
from local_assistant.decision.jev import TypeSafeJevDecisionProvider
from local_assistant.decision.router import DecisionRouter
from local_assistant.generation.ollama import OllamaWriterProvider
from local_assistant.platforms.macos import MacOSPlatformAdapter
from local_assistant.runtime.controller import AssistantController, PipelineListener
from local_assistant.runtime.errors import ProviderUnavailableError
from local_assistant.runtime.executor import ActionExecutor
from local_assistant.safety.policy import SafetyPolicy
from local_assistant.voice.tts import MacOSTextToSpeechProvider, NullTextToSpeechProvider

ConfirmationCallback = Callable[[Action, str], bool]


@dataclass(slots=True)
class RuntimeBundle:
    """Controller plus the stateful providers whose resources must be released."""

    controller: AssistantController
    browser: PlaywrightBrowserAdapter
    writer: OllamaWriterProvider

    def close(self) -> None:
        self.browser.close()
        self.writer.close()


def build_runtime(
    settings: Settings,
    *,
    dry_run: bool = False,
    assume_yes: bool = False,
    tts: bool = True,
    confirmation: ConfirmationCallback | None = None,
    pipeline_listener: PipelineListener | None = None,
) -> RuntimeBundle:
    platform = MacOSPlatformAdapter()
    try:
        semantic = TypeSafeJevDecisionProvider() if settings.has_typesafe_key else None
    except ProviderUnavailableError:
        semantic = None
    router = DecisionRouter(semantic, settings.decision_confidence)
    browser = PlaywrightBrowserAdapter(
        settings.search_engine,
        settings.browser_headless,
        native_url_opener=platform.open_url,
    )
    writer = OllamaWriterProvider(settings.ollama_url, settings.ollama_model)
    executor = ActionExecutor(platform, browser, writer)
    confirm = (lambda action, reason: True) if assume_yes else confirmation
    controller = AssistantController(
        WhisperCppSpeechToTextProvider(
            settings.whisper_binary,
            settings.whisper_model_path,
            use_gpu=settings.whisper_use_gpu,
        ),
        router,
        SafetyPolicy(),
        executor,
        platform,
        MacOSTextToSpeechProvider() if tts and settings.tts_enabled else NullTextToSpeechProvider(),
        dry_run=dry_run or settings.dry_run,
        confirmation=confirm,
        pipeline_listener=pipeline_listener,
        writer_model=settings.ollama_model,
    )
    return RuntimeBundle(controller, browser, writer)

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer

from local_assistant.config.settings import Settings
from local_assistant.observability.logging_setup import configure_logging
from local_assistant.runtime.factory import RuntimeBundle, build_runtime
from local_assistant.runtime.types import ControllerResult
from local_assistant.voice.ptt import PushToTalkRecorder, TemporaryAudio

app = typer.Typer(no_args_is_help=True, help="Local-first voice-operated desktop assistant")


def _confirm(action: object, reason: str) -> bool:
    del action
    return typer.confirm(f"{reason} Continue?", default=False)


def _build_controller(
    settings: Settings, dry_run: bool, assume_yes: bool, tts: bool
) -> RuntimeBundle:
    return build_runtime(
        settings,
        dry_run=dry_run,
        assume_yes=assume_yes,
        tts=tts,
        confirmation=_confirm,
    )


def _print_result(result: ControllerResult) -> None:
    payload = {
        "transcript": result.transcript,
        "action": result.action.model_dump(mode="json"),
        "success": result.execution.success,
        "message": result.execution.message,
        "data": result.execution.data,
        "timings_ms": result.timings_ms,
    }
    typer.echo(json.dumps(payload, indent=2, default=str))


@app.command()
def run(
    text: Annotated[str | None, typer.Option(help="Typed command; skips microphone/STT")] = None,
    audio: Annotated[Path | None, typer.Option(help="16 kHz mono WAV input")] = None,
    ptt: Annotated[bool, typer.Option(help="Record once using terminal push-to-talk")] = False,
    dry_run: Annotated[bool, typer.Option(help="Report the action without side effects")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm consequential actions")] = False,
    no_tts: Annotated[bool, typer.Option(help="Disable spoken status")] = False,
) -> None:
    """Handle one text, audio-file, or push-to-talk command."""
    settings = Settings.load()
    configure_logging(settings.log_level)
    selected = sum(value is not None and value is not False for value in (text, audio, ptt))
    if selected != 1:
        raise typer.BadParameter("Choose exactly one of --text, --audio, or --ptt")
    runtime = _build_controller(settings, dry_run, yes, not no_tts)
    try:
        if text is not None:
            result = runtime.controller.handle_text(text)
        elif audio is not None:
            result = runtime.controller.handle_audio(audio)
        else:
            with TemporaryAudio(PushToTalkRecorder().record()) as recording:
                result = runtime.controller.handle_audio(recording)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        runtime.close()
    _print_result(result)
    if not result.execution.success:
        raise typer.Exit(2)


@app.command()
def doctor() -> None:
    """Report local dependency and configuration status without exposing secrets."""
    settings = Settings.load()
    checks = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "whisper_binary": shutil.which(settings.whisper_binary),
        "whisper_model": str(settings.whisper_model_path),
        "whisper_model_exists": settings.whisper_model_path.is_file(),
        "ollama_binary": shutil.which("ollama"),
        "typesafe_api_key_configured": bool(os.environ.get("TYPESAFE_API_KEY")),
    }
    typer.echo(json.dumps(checks, indent=2))


if __name__ == "__main__":
    app()

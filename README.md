# Relay

Relay is an experimental, local-first macOS voice automation project. It explores whether
[TypeSafe Jev](https://www.typesafe.ai/) can serve as a fast, bounded semantic decision layer
between local speech recognition and a small set of validated desktop actions.

Relay is not production-ready. It is a working research prototype with real macOS automation,
an intentionally narrow action vocabulary, and explicit safety boundaries. OpenAI or Codex is not
required at runtime.

## Screenshots and demo

https://github.com/user-attachments/assets/c165af17-d9c7-4100-870c-984545042e86

The map viewport shown during the demonstration is intentionally blurred to remove location data.

## How it works

Relay uses the least complex mechanism that can safely handle a request:

1. Exact, unambiguous commands use a deterministic fast path.
2. Requests requiring semantic selection are routed by Jev into typed actions.
3. Language generation or transformation is delegated to a local Ollama model only when needed.
4. Every action passes through validation, risk classification, confirmation policy, execution,
   and result verification.

```text
Global push-to-talk / microphone
               |
               v
      whisper.cpp (local STT)
               |
               v
   +-- deterministic fast path --+
   |                              |
   +--------> Jev router <--------+
                  |
                  +---- language task only ----> Ollama / Qwen
                  |                                  |
                  <----------- generated data -------+
                  |
                  v
          validated typed actions
                  |
                  v
       safety and confirmation policy
                  |
                  v
   macOS / browser adapter -> verification -> UI status
```

The local writer model never chooses an action, recipient, risk level, or permission to execute.
Its output is data returned to the controller. Jev receives compact routing context rather than a
full conversation history.

## What works

- A menu-bar `Relay.app` with a floating status panel and configurable global push-to-talk hotkey.
- Local speech recognition through `whisper.cpp` with a configurable model.
- Deterministic routing for a small set of high-confidence commands.
- Jev conversion into validated typed actions with configurable confidence thresholds.
- Optional Ollama/Qwen generation for writing, rewriting, summarization, and query transformation.
- Core macOS actions such as opening/focusing applications, keyboard input, clipboard access,
  screenshots, volume/media control, scrolling, and opening files or URLs.
- Browser automation through Playwright where it is more reliable than coordinate-based GUI input.
- Dry-run mode, confirmation gates, structured logging, dependency diagnostics, and mockable provider
  interfaces.
- CLI operation independent of the macOS app shell.

## Current limitations

- macOS is the only implemented platform adapter.
- Jev routing is an external API operation and requires `TYPESAFE_API_KEY`; Relay is local-first,
  not fully offline. Exact deterministic commands continue to work without it.
- Desktop UI automation depends on macOS Accessibility permission and application-specific
  accessibility behavior. Complex or rapidly changing interfaces remain fragile.
- Browser automation uses a Playwright-managed browser, not arbitrary existing browser tabs.
- Multi-step planning and visual perception are deliberately limited. Relay does not include a
  vision model or unrestricted shell execution.
- Communication actions and other consequential operations may require confirmation. Verification
  cannot guarantee that every third-party application completed an action.
- The app is unsigned and intended for development use; macOS may require an explicit first launch.

## Requirements

- macOS
- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- `whisper.cpp` and a compatible Whisper model
- PortAudio (for microphone capture)
- [Ollama](https://ollama.com/) and `qwen3.5:2b` for optional generation
- A TypeSafe API key for Jev routing
- Playwright Chromium for browser automation

## Installation on macOS

Install system dependencies with Homebrew:

```bash
brew install python@3.12 uv cmake ffmpeg portaudio whisper-cpp ollama
```

Clone the repository and install all Python extras:

```bash
git clone https://github.com/sai-harsha-k/relay-macos-assistant.git
cd relay-macos-assistant
uv sync --all-extras
```

Create local configuration. Never commit this file:

```bash
cp .env.example .env
```

Add your TypeSafe credential to `.env`:

```dotenv
TYPESAFE_API_KEY=your_key_here
```

Download a Whisper model. The configured development default is `base.en`:

```bash
mkdir -p models
curl -fL \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin \
  -o models/ggml-base.en.bin
```

To use another model, set both its identifier and path in `.env`. For example:

```dotenv
ASSISTANT_WHISPER_MODEL=large-v3-turbo-q5_0
ASSISTANT_WHISPER_MODEL_PATH=models/ggml-large-v3-turbo-q5_0.bin
```

Start Ollama and download the default optional writer model:

```bash
brew services start ollama
ollama pull qwen3.5:2b
```

Install the Playwright browser:

```bash
uv run playwright install chromium
```

The idempotent bootstrap script checks these dependencies and asks before downloading heavyweight
components:

```bash
./scripts/bootstrap_macos.sh
```

It never writes a secret.

## Permissions

On first use, grant Relay (or the terminal used for development) access in **System Settings >
Privacy & Security**:

- **Microphone** for voice capture.
- **Accessibility** for the global hotkey and desktop actions.
- **Screen Recording** only for workflows that capture the screen.

Relay starts idle. Hold the configured shortcut (default: Command + Option + Space), speak, and
release it to transcribe and submit the utterance. Quit Relay completely from its menu-bar item.

## Run the CLI

Verify the editable installation and dependencies:

```bash
uv run python -c "import local_assistant; print(local_assistant.__file__)"
uv run local-assistant doctor
```

Useful commands:

```bash
uv run local-assistant run --text "open Calculator"
uv run local-assistant run --text "open Calculator" --dry-run
uv run local-assistant run --ptt
uv run local-assistant run --audio path/to/audio.wav
```

## Build and launch Relay.app

```bash
./scripts/build_relay_app.sh
open outputs/Relay.app
```

For launch diagnostics, run the app executable directly:

```bash
./outputs/Relay.app/Contents/MacOS/Relay
```

The app bundle and generated icon formats are build outputs and are intentionally excluded from
version control. Replace `assets/relay-logo.svg` to change the development branding.

## Configuration

Secrets and environment-specific runtime paths belong in `.env`. The committed `.env.example`
contains only safe defaults and an empty credential placeholder. Common variables include:

- `TYPESAFE_API_KEY`
- `ASSISTANT_WHISPER_MODEL`
- `ASSISTANT_WHISPER_MODEL_PATH`
- `ASSISTANT_WHISPER_BINARY`
- `ASSISTANT_OLLAMA_URL`
- `ASSISTANT_DRY_RUN`
- `ASSISTANT_LOG_LEVEL`
- `ASSISTANT_SEARCH_ENGINE`
- `ASSISTANT_BROWSER_HEADLESS`

User preferences are stored outside the repository at:

```text
~/Library/Application Support/Relay/settings.json
```

This file contains preferences such as hotkey, confidence threshold, TTS, and model name—not API
keys.

## Safety model

The executor accepts validated typed actions, never free-form model instructions. Low-risk,
reversible operations can execute directly. Communications, destructive changes, purchases,
financial activity, software installation, security/account changes, and other consequential
operations require the applicable confirmation policy. Auto-send affects eligible communication
actions only and does not bypass higher-risk safeguards.

There is no arbitrary model-generated shell action in v0.1.

## Development

```bash
uv run pytest -m "not integration and not e2e"
uv run pytest -m integration
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Normal unit tests mock external providers and make no Jev or Ollama calls. Integration tests skip
when their required local component or credential is unavailable. See
[`docs/VERIFICATION.md`](docs/VERIFICATION.md) for the latest verified scope.


## What this experiment taught us

- Bounded desktop actions are a strong fit for Jev.
- Obvious commands are faster and more reliable when handled deterministically.
- Jev is useful for semantic selection and routing when paired with explicit confidence handling.
- Local LLMs add value when language must be generated or transformed, not for choosing known
  actions or executing them.
- Complex multi-step GUI tasks still benefit from stronger planning and perception models.
- Relay intentionally explores both where a fast decision model works well and where it does not.

## License

Relay is available under the [MIT License](LICENSE).

## Acknowledgements

Implementation ideas were studied from
[`jev-voice`](https://github.com/kevinbadi/jev-voice) and
[`typesafe-computer-use`](https://github.com/awlevin/typesafe-computer-use). Relay is an independent
implementation; no source from those projects is vendored here.

# v0.1 Product Specification

## Product principle

Choose the least expensive capable mechanism: deterministic local code, then TypeSafe Jev for bounded semantic decisions, then a small local writer only for genuine language generation. Models never execute operating-system actions.

## Supported runtime

- macOS 14+; Python 3.12.
- Global hold-to-talk microphone capture (default Command + Option + Space), terminal push-to-talk, or 16 kHz mono WAV input.
- Local whisper.cpp transcription, configurable GGML model, default `base.en`.
- TypeSafe Jev routing through the official Python SDK and only `TYPESAFE_API_KEY`.
- Local Ollama writer, default `qwen3.5:2b`.
- macOS native browser navigation and Playwright Chromium DOM interaction.
- Optional local status speech through macOS `say`.

## Behavior

The controller first attempts a deliberately small fast path for exact media, mute, volume, screenshot, and exact installed-app open commands. Everything else requires Jev. Jev chooses compact action, installed-app, and literal payload candidate identifiers in one fan-out call. It does not invent arguments. Decisions below the configured confidence threshold produce `ASK_USER`.

`GENERATE_TEXT` calls `WriterProvider`. `SEARCH_WEB` and `SEND_MESSAGE` can also carry a typed, mutually exclusive field-generation instruction when Jev determines that a query or message genuinely requires writing, rewriting, summarization, or transformation. Literal transcript values bypass the writer. Only the requested language field is generated; action type, application, recipient, risk, and execution permission remain validated controller data. Ollama availability does not affect non-generative actions.

## Action contract

The supported actions are `OPEN_APP`, `CLOSE_APP`, `FOCUS_APP`, `OPEN_URL`, `SEARCH_WEB`, `TYPE_TEXT`, `GENERATE_TEXT`, `SEND_MESSAGE`, `PRESS_KEY`, `KEYBOARD_SHORTCUT`, `CLICK_ELEMENT`, `SCROLL`, `SET_VOLUME`, `MEDIA_CONTROL`, `OPEN_FOLDER`, `FIND_FILE`, `READ_CLIPBOARD`, `WRITE_CLIPBOARD`, `TAKE_SCREENSHOT`, and `ASK_USER`.

Every action is a frozen Pydantic structure with a discriminator and forbidden unknown fields. There is deliberately no shell action.

## Safety

Read-only or readily reversible operations are low risk and execute directly. Search and writing into a known local editor such as Notes or TextEdit do not require confirmation. Typing into an unverified destination, sending, or altering application/clipboard state requires confirmation. Generation is low risk while it remains data; generation-plus-typing inherits the destination's policy. Dry-run returns the full proposed typed action and never dispatches.

`SEND_MESSAGE` is a purpose-built communications action. It requires confirmation unless the communications-only Auto-send preference is enabled and its existing eligibility rules pass. The vocabulary still cannot delete files, purchase, install software, grant permissions, change accounts/security, submit arbitrary forms, or execute commands; such intentions resolve to clarification/refusal. Auto-send and generated text can never relax those boundaries.

## Failure contract

Missing Jev credentials do not prevent startup or fast-path actions. Missing/unavailable Ollama produces a recoverable generation error. Missing whisper binary/model produces an actionable transcription error. Adapter failures become unsuccessful `ExecutionResult` values rather than crashing the voice loop.

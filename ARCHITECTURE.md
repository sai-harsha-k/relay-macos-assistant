# Architecture

## Dependency direction

```text
voice/audio ─┐
             ▼
          controller → decision router → deterministic parser
              │              └───────→ DecisionProvider (Jev)
              ▼
          typed Action → safety policy → action executor
                                        ├→ PlatformAdapter (macOS)
                                        ├→ BrowserAdapter (native navigation / Playwright DOM)
                                        └→ WriterProvider (Ollama, data only)
```

Core decision, action, safety, and orchestration modules import protocols, not concrete OS implementations. A future Linux port supplies platform, audio, and TTS adapters without changing policy or control flow.

The macOS application is a thin shell around that same graph:

```text
Relay.app / rumps status item
  → fixed native overlay + typed settings service
  → Quartz global hold-to-talk event tap
  → VoiceSession (release-to-submit utterance lifecycle)
  → MicrophoneRecorder (active only while the shortcut is held)
  → shared AssistantController
```

`VoiceSession`, the global-hotkey adapter, health reporting, preferences, single-instance locking, and owned-process cleanup live under `app`; none changes the decision or safety boundary. With Auto-send off, repeated key events are suppressed, release submits one authoritative final transcript, and the session returns to idle after execution. With Auto-send on, the same recorder uses the configured silence endpoint to commit an utterance, runs the existing controller, then starts a fresh recording; preview text is not repeatedly routed. Disabling Auto-send cancels continuous capture and restores release-to-submit push-to-talk. The app never owns Ollama and therefore never stops it. py2app supplies a Terminal-free `LSUIElement` bundle, while the CLI entry point remains independently available.

At committed-command time, the macOS adapter supplies the actual frontmost application as bounded session/decision context. Contextual `SCROLL` dispatch uses native page/arrow input against that application. `CLICK_ELEMENT` uses the same frontmost-app boundary and activates an exact, non-editable Accessibility-tree label/title. It does not assume that a Playwright page or the last Relay-opened app is still active. Explicit visible video titles are literal data; vague targets fail closed with `ASK_USER`.

The controller may emit a short ordered tuple of typed actions only for conservatively parsed literal compounds. It validates every referenced app before dispatch, executes in order, and stops on the first failure. Jev and the writer do not generate arbitrary action lists.

## Modules

- `audio`: `SpeechToTextProvider` implementation around `whisper-cli`.
- `decision`: conservative fast path, compact Jev provider, confidence gate.
- `generation`: persistent HTTP-client-backed Ollama writer.
- `perception`: accessibility-tree surface, intentionally separate from decisions.
- `actions`: complete discriminated action union and validation entry point.
- `platforms`: macOS native process/AppleScript adapter implementations.
- `browser`: native named/default-browser navigation plus Playwright interaction for explicitly Relay-owned pages, isolated on its own worker thread.
- `safety`: centralized risk classification and confirmation requirement.
- `voice`: push-to-talk recorder and local TTS.
- `app`: Relay menu-bar lifecycle, global push-to-talk hotkey, bounded voice session, typed local preferences, single-instance lock, diagnostics, and owned-resource cleanup.
- `runtime`: provider protocols, executor dispatch, controller, result types.
- `config` and `observability`: environment configuration and JSON structured logs.

## Trust boundaries

External inputs become actions only through Pydantic validation. Jev can select only provided action IDs and candidates. Literal action fields are extracted without generation. For language work, `SEARCH_WEB` and `SEND_MESSAGE` can carry a mutually exclusive field-level generation instruction; the executor sends only that instruction to the writer and substitutes only the returned text field. The writer cannot change action type, application, recipient, risk, or execution permission. The executor has no generic command method. Subprocesses use argument lists rather than a shell. Secrets are loaded from environment and excluded from serialized configuration.

Risk is contextual but deterministic: Jev identifies a typed intent and confidence, while `SafetyPolicy` retains authority over execution. Searches and reversible typing into a controller-known local editor are low risk. Communication sends, typing into an unverified destination, and other consequential actions remain confirmation-gated; the communications-only Auto-send preference cannot affect unrelated risks.

## Token and latency discipline

Static compact action metadata is module-level. Each Jev request contains one committed utterance, short stable action descriptions, at most 80 discovered applications, at most six locally extracted literal payloads, and only the current app/last action/last target/immediately previous command. No full history or screenshots are sent. Ollama receives one short task plus only explicitly relevant context and uses `keep_alive=10m`.

## Local settings boundary

`.env` is reserved for credentials and machine/runtime configuration. Non-secret user preferences use a validated `RelayPreferences` model persisted atomically to `~/Library/Application Support/Relay/settings.json`. Malformed files fall back to safe defaults. The communications auto-send preference is consumed only by `SafetyPolicy` for the typed `SEND_MESSAGE` action and cannot relax other confirmation requirements.

The replaceable branding source is `assets/relay-logo.svg`. The build derives both ICNS and menu-bar PNG assets from it, with the legacy icon retained only as a fallback.

## Verification

Pure unit tests inject provider and adapter fakes. Integration tests are separately marked and self-skip when dependencies/credentials are absent. See `docs/VERIFICATION.md` for the exact observed environment and commands.

## Packaging invariant

Hatchling still emits its normal editable `.pth`, but the wheel target force-includes `src/local_assistant` so imports do not depend solely on that file. This is required because CPython 3.12+ intentionally ignores `.pth` files carrying macOS' `UF_HIDDEN` flag. The clean-install integration smoke test reproduces that filesystem state explicitly.

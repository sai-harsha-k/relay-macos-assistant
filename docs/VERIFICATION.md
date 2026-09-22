# Verification Report

This document records what was actually exercised. It is not a claim that Relay is production-ready
or that every third-party macOS interface is supported.

## Release-audit environment

- Date: 2026-09-21
- Platform: macOS
- Python: 3.12.14
- Dependency runner: `uv`
- Test framework: pytest 8.4.2

Machine names, user-specific paths, credentials, local logs, screenshots, model files, app bundles,
and cache contents are intentionally excluded from this public report.

## Release-audit checks

Commands were run with an ignored repository-local uv cache:

```bash
UV_CACHE_DIR=work/uv-cache uv run pytest -m "not integration and not e2e"
UV_CACHE_DIR=work/uv-cache uv run ruff check .
UV_CACHE_DIR=work/uv-cache uv run ruff format --check .
UV_CACHE_DIR=work/uv-cache uv run mypy
```

Results:

- Unit tests: **158 passed**, 5 integration tests deselected.
- Ruff lint: **passed**.
- Ruff formatting check: **passed** (87 files already formatted).
- mypy strict check: **passed** (65 source files).

## Integration tests

The integration suite is intentionally separate because it depends on local software, downloaded
models, macOS permissions, or external credentials:

- Jev integration skips when `TYPESAFE_API_KEY` is absent.
- Ollama integration skips when the configured local service/model is unavailable.
- Whisper integration requires a compatible `whisper.cpp` binary and local model.
- Playwright integration requires an installed Playwright browser.
- Packaging integration creates a clean virtual environment and validates imports and the CLI.

These integration tests were not rerun as part of the public-release privacy audit. Run them locally
with:

```bash
uv run pytest -m integration
```

## Manual macOS verification

The following behaviors require a real interactive macOS session and cannot be established by unit
tests alone:

- Microphone, Accessibility, and optional Screen Recording permission prompts.
- Press-and-hold global hotkey behavior across other applications.
- Audio capture using the selected input device and installed Whisper model.
- Application-specific accessibility automation and third-party UI verification.
- Unsigned `Relay.app` launch behavior under the host's Gatekeeper settings.

## Known limitations

- The application shell and platform adapter are macOS-only in v0.1.
- Jev routing requires network access and a user-supplied credential.
- Browser and third-party application interfaces can change independently of Relay.
- Complex multi-step GUI automation lacks a general planner or visual reasoning model.
- Latency varies substantially with Whisper model, hardware, Jev network response, local writer
  model, and target application behavior.

## Security and privacy boundary

The normal unit suite uses mocks and does not make Jev or Ollama calls. `.env`, local preferences,
downloaded models, virtual environments, caches, logs, screenshots, recordings, builds, and app
bundles are excluded from version control. The committed `.env.example` contains no credential.

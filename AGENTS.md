# Repository guidance

- Preserve the decision order: deterministic code, Jev, then local writer.
- Never add model-driven shell/OS execution; writers return data only.
- Keep OS code behind adapters and secrets out of logs/tests.
- Run offline unit tests, lint, and types before claiming completion.
- Record only actually executed checks in `docs/VERIFICATION.md`.

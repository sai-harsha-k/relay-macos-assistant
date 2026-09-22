import os

import httpx
import pytest

from local_assistant.generation.ollama import OllamaWriterProvider


@pytest.mark.integration
def test_configured_ollama_model_generates_short_response() -> None:
    url = os.environ.get("ASSISTANT_OLLAMA_URL", "http://127.0.0.1:11434")
    model = os.environ.get("ASSISTANT_OLLAMA_MODEL", "qwen3.5:2b")
    try:
        tags = httpx.get(f"{url}/api/tags", timeout=2)
        tags.raise_for_status()
    except Exception:
        pytest.skip("Ollama server is not running")
    available = {item["name"] for item in tags.json().get("models", [])}
    if model not in available:
        pytest.skip(f"Ollama model {model} is not installed")
    response = OllamaWriterProvider(url, model, timeout_seconds=180).generate(
        "Reply with exactly four words greeting a local user."
    )
    assert response.strip()

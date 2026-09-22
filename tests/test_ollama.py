import httpx
import pytest

from local_assistant.generation.ollama import OllamaWriterProvider
from local_assistant.runtime.errors import ProviderResponseError, ProviderUnavailableError


def client_for(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama.test")


def test_ollama_returns_generated_text_and_sends_keep_alive() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"response": " Hello there. ", "done": True})

    provider = OllamaWriterProvider(model="qwen3.5:2b", client=client_for(handler))
    assert provider.generate("write a greeting") == "Hello there."
    assert captured["model"] == "qwen3.5:2b"
    assert captured["keep_alive"] == "10m"
    assert captured["think"] is False


def test_ollama_unavailable_is_recoverable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider = OllamaWriterProvider(client=client_for(handler))
    with pytest.raises(ProviderUnavailableError, match="Ollama is unavailable"):
        provider.generate("hello")


def test_ollama_missing_model_has_actionable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found", request=request)

    provider = OllamaWriterProvider(model="missing", client=client_for(handler))
    with pytest.raises(ProviderUnavailableError, match="ollama pull missing"):
        provider.generate("hello")


def test_ollama_empty_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": ""}, request=request)

    with pytest.raises(ProviderResponseError):
        OllamaWriterProvider(client=client_for(handler)).generate("hello")

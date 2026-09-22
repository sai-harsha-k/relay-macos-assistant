from __future__ import annotations

from typing import Any

import httpx

from local_assistant.runtime.errors import ProviderResponseError, ProviderUnavailableError


class OllamaWriterProvider:
    """Local writer. Returned text is data; this class has no action adapter."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen3.5:2b",
        timeout_seconds: float = 120,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_seconds)

    def generate(self, instruction: str, context: str | None = None) -> str:
        prompt = instruction.strip()
        if context:
            prompt = f"Context:\n{context.strip()}\n\nTask:\n{prompt}"
        payload: dict[str, Any] = {
            "model": self._model,
            "system": (
                "Write only the requested text. Do not issue commands or describe computer actions."
            ),
            "prompt": prompt,
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {"temperature": 0.3, "num_predict": 256},
        }
        try:
            response = self._client.post("/api/generate", json=payload)
            response.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ProviderUnavailableError(
                f"Ollama is unavailable. Start it and pull {self._model!r}, then retry."
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            if exc.response.status_code == 404:
                raise ProviderUnavailableError(
                    f"Ollama model {self._model!r} is unavailable. Run: ollama pull {self._model}"
                ) from exc
            raise ProviderUnavailableError(f"Ollama returned an error: {detail}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Ollama returned invalid JSON") from exc
        text = data.get("response") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ProviderResponseError("Ollama returned no generated text")
        return text.strip()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

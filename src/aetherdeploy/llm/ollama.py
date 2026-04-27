from __future__ import annotations

import json

import httpx

from .base import LLMBackend

_MAX_RETRIES = 3


class OllamaBackend(LLMBackend):
    """Backend LLM usando Ollama corriendo localmente.

    Requiere: ollama serve + ollama pull gemma3
    """

    def __init__(self, model: str = "gemma3", base_url: str = "http://localhost:11434"):
        self._model = model
        self._client = httpx.AsyncClient(base_url=base_url, timeout=120.0)

    async def complete(self, messages: list[dict], system: str = "") -> str:
        payload = {"model": self._model, "messages": messages, "stream": False}
        if system:
            payload["system"] = system

        resp = await self._client.post("/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    async def complete_json(self, messages: list[dict], system: str = "") -> dict:
        json_system = (system + "\n\n" if system else "") + "Responde ÚNICAMENTE con JSON válido, sin texto adicional."
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "format": "json",
        }
        if json_system:
            payload["system"] = json_system

        for attempt in range(_MAX_RETRIES):
            resp = await self._client.post("/api/chat", json=payload)
            resp.raise_for_status()
            content = resp.json()["message"]["content"]
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                if attempt == _MAX_RETRIES - 1:
                    raise ValueError(f"El LLM no retornó JSON válido tras {_MAX_RETRIES} intentos: {content!r}")
        return {}

    async def aclose(self) -> None:
        await self._client.aclose()

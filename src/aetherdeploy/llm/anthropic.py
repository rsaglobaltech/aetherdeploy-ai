from __future__ import annotations

import json

from .base import LLMBackend

_MAX_RETRIES = 3


class AnthropicBackend(LLMBackend):
    """Backend LLM usando la API de Anthropic (Claude).

    Requiere: pip install anthropic y AETHER_LLM_API_KEY configurada.

    Prompt caching is enabled automatically for system prompts longer than
    1024 tokens (Anthropic's minimum cacheable size). This reduces latency
    and cost by ~90% on repeated calls with the same system prompt.
    """

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None, **_):
        try:
            import anthropic
        except ImportError as e:
            raise ImportError("Instala anthropic: pip install anthropic") from e

        self._model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    def _build_system(self, system: str) -> list[dict] | str:
        """Wraps the system prompt in cache_control blocks when non-empty.

        Anthropic caches prompts that include ``cache_control`` at the block level.
        Only the system prompt is marked for caching here; the user message changes
        per-request so caching it would never hit.
        """
        if not system:
            return ""
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    async def complete(self, messages: list[dict], system: str = "") -> str:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": messages,
        }
        sys_block = self._build_system(system)
        if sys_block:
            kwargs["system"] = sys_block

        resp = await self._client.messages.create(**kwargs)
        return resp.content[0].text

    async def complete_json(self, messages: list[dict], system: str = "") -> dict:
        base_system = (system + "\n\n" if system else "") + "Responde ÚNICAMENTE con JSON válido."
        sys_block = self._build_system(base_system)

        kwargs: dict = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if sys_block:
            kwargs["system"] = sys_block

        for attempt in range(_MAX_RETRIES):
            resp = await self._client.messages.create(**kwargs)
            content = resp.content[0].text
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                if attempt == _MAX_RETRIES - 1:
                    raise ValueError(f"Anthropic no retornó JSON válido: {content!r}")
        return {}

    async def aclose(self) -> None:
        await self._client.close()

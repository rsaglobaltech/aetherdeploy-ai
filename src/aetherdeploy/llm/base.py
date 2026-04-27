from __future__ import annotations

import json
import inspect
from abc import ABC, abstractmethod


class LLMBackend(ABC):
    """Contrato único que todos los backends LLM deben implementar.

    Cambiar de Ollama a Anthropic u otro proveedor = cambiar el backend
    en config, sin tocar ningún nodo del agente.
    """

    @abstractmethod
    async def complete(self, messages: list[dict], system: str = "") -> str:
        """Envía mensajes y retorna la respuesta como string."""

    @abstractmethod
    async def complete_json(self, messages: list[dict], system: str = "") -> dict:
        """Retorna la respuesta parseada como JSON. Reintenta si el formato falla."""


class LLMBackendFactory:
    @staticmethod
    def create(backend: str, **kwargs) -> LLMBackend:
        if backend == "ollama":
            from .ollama import OllamaBackend
            return OllamaBackend(**_supported_kwargs(OllamaBackend, kwargs))
        if backend == "anthropic":
            from .anthropic import AnthropicBackend
            return AnthropicBackend(**_supported_kwargs(AnthropicBackend, kwargs))
        raise ValueError(f"Backend LLM desconocido: '{backend}'. Opciones: ollama, anthropic")


def _supported_kwargs(cls: type, kwargs: dict) -> dict:
    signature = inspect.signature(cls)
    return {key: value for key, value in kwargs.items() if key in signature.parameters}

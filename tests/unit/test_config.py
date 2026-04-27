from __future__ import annotations

import pytest


def test_default_config(mock_config):
    from aetherdeploy.config import get_config
    cfg = get_config()
    assert cfg.llm_backend == "ollama"
    assert cfg.llm_model == "gemma3"
    assert cfg.default_provider == "aws"
    assert cfg.default_region == "us-east-1"
    assert cfg.otel_enabled is False


def test_llm_backend_factory_ollama():
    from aetherdeploy.llm import LLMBackendFactory
    from aetherdeploy.llm.ollama import OllamaBackend
    backend = LLMBackendFactory.create("ollama", model="gemma3")
    assert isinstance(backend, OllamaBackend)


def test_llm_backend_factory_unknown():
    from aetherdeploy.llm import LLMBackendFactory
    with pytest.raises(ValueError, match="desconocido"):
        LLMBackendFactory.create("unknown_backend")

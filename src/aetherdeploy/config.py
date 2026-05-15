from __future__ import annotations

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_VALID_LLM_BACKENDS = {"ollama", "anthropic"}


class AetherConfig(BaseSettings):
    # LLM — intercambiable sin tocar el resto del sistema
    llm_backend: str = "ollama"
    llm_model: str = "gemma3:1b"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str | None = None
    llm_wait_forever: bool = False
    llm_retry_interval: float = 3.0

    # Cloud
    default_provider: Literal["aws", "gcp", "azure"] = "aws"
    default_region: str = "us-east-1"
    terraform_binary: str = "terraform"

    # State backend (MEJORAS.md §1.1) — "remote" requires cloud creds at apply time
    state_backend: Literal["remote", "local"] = "remote"

    # Observabilidad
    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4317"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AETHER_",
        extra="ignore",
    )

    @field_validator("llm_backend")
    @classmethod
    def check_llm_backend(cls, v: str) -> str:
        if v not in _VALID_LLM_BACKENDS:
            raise ValueError(
                f"AETHER_LLM_BACKEND must be one of {sorted(_VALID_LLM_BACKENDS)}, got '{v}'"
            )
        return v


# Instancia global lazy — se crea la primera vez que se importa.
# Llama a reset_config() entre tests para forzar una nueva lectura del entorno.
_config: AetherConfig | None = None


def get_config() -> AetherConfig:
    global _config
    if _config is None:
        _config = AetherConfig()
    return _config


def reset_config() -> None:
    """Clears the cached config so the next call to get_config() re-reads env vars.

    Intended for use in tests that modify environment variables between calls.
    """
    global _config
    _config = None

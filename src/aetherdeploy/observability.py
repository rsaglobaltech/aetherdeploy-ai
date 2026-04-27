"""Instrumentación OpenTelemetry para AetherDeploy.

Configuración::

    AETHER_OTEL_ENABLED=true
    AETHER_OTEL_ENDPOINT=http://localhost:4317   # OTLP gRPC

Compatible con Langfuse (exporta via OTLP).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_LOGGER_NAME = "aetherdeploy"
_MAX_FIELD_CHARS = 4000
_SECRET_KEYS = re.compile(r"(secret|token|password|credential|access_key|api_key)", re.I)
_AWS_ACCESS_KEY_RE = re.compile(r"A(KIA|SIA)[0-9A-Z]{16}")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "structured", None)
        if isinstance(extra, dict):
            payload.update(_sanitize(extra))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(
    log_file: str | None = None,
    level: str | None = None,
    to_stderr: bool | None = None,
) -> Path:
    """Configura logging estructurado JSONL para el sistema agéntico.

    Por defecto escribe en ``.aetherdeploy/logs/agent.jsonl`` y no usa stderr,
    para no contaminar el stream JSON que consume la UI.
    """
    log_level = (level or os.environ.get("AETHER_LOG_LEVEL") or "INFO").upper()
    target = Path(log_file or os.environ.get("AETHER_LOG_FILE") or ".aetherdeploy/logs/agent.jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logger.propagate = False
    logger.handlers.clear()

    formatter = _JsonFormatter()
    file_handler = logging.FileHandler(target, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stderr_enabled = (
        to_stderr
        if to_stderr is not None
        else os.environ.get("AETHER_LOG_TO_STDERR", "").lower() in {"1", "true", "yes"}
    )
    if stderr_enabled:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    log_event("logging.configured", level=log_level, log_file=str(target), stderr=stderr_enabled)
    return target


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        setup_logging()
    return logger


def log_event(event_name: str, level: str = "INFO", **fields: Any) -> None:
    """Escribe un evento estructurado saneado."""
    logger = get_logger()
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        event_name,
        extra={"structured": {"event": event_name, **fields}},
    )


def _sanitize(value: Any, key: str = "") -> Any:
    if _SECRET_KEYS.search(key):
        return "[REDACTED]"
    if dataclasses.is_dataclass(value):
        return _sanitize(dataclasses.asdict(value), key)
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item, key) for item in value]
    if isinstance(value, str):
        redacted = _AWS_ACCESS_KEY_RE.sub("[REDACTED_AWS_ACCESS_KEY]", value)
        for env_key, env_value in os.environ.items():
            if _SECRET_KEYS.search(env_key) and env_value and len(env_value) >= 4:
                redacted = redacted.replace(env_value, "[REDACTED]")
        if len(redacted) > _MAX_FIELD_CHARS:
            return f"{redacted[:_MAX_FIELD_CHARS]}...[truncated {len(redacted) - _MAX_FIELD_CHARS} chars]"
        return redacted
    return value


def _state_summary(state: dict) -> dict:
    return {
        "current_step": state.get("current_step"),
        "project_path": state.get("project_path"),
        "github_url": state.get("github_url"),
        "target_environments": state.get("target_environments"),
        "preferred_provider": state.get("preferred_provider"),
        "dry_run": state.get("dry_run"),
        "has_project_analysis": state.get("project_analysis") is not None,
        "has_architecture_proposal": state.get("architecture_proposal") is not None,
        "has_deployment_result": state.get("deployment_result") is not None,
        "user_approved": state.get("user_approved"),
        "promoted_to_prod": state.get("promoted_to_prod"),
        "errors": state.get("errors", []),
        "last_message": (state.get("messages") or [{}])[-1],
    }


def _output_summary(output: dict) -> dict:
    return {
        "current_step": output.get("current_step"),
        "errors": output.get("errors", []),
        "messages_added": len(output.get("messages", [])),
        "project_analysis": output.get("project_analysis"),
        "architecture_proposal": output.get("architecture_proposal"),
        "deployment_result": output.get("deployment_result"),
        "terraform_config_keys": list((output.get("terraform_configs") or {}).keys()),
    }


def setup_otel(endpoint: str = "http://localhost:4317", service_name: str = "aetherdeploy") -> None:
    """Inicializa OpenTelemetry con exportador OTLP."""
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": service_name, "service.version": "0.1.0"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
    except ImportError:
        pass  # OpenTelemetry no instalado — silencioso


def traced_node(node_fn: Callable) -> Callable:
    """Decorador que envuelve un nodo con logging estructurado y span OTel."""
    async def wrapper(state: dict) -> dict:
        start = time.perf_counter()
        node_name = node_fn.__name__
        log_event(
            "agent.node.start",
            node=node_name,
            input=_state_summary(state),
            decision_trace=(
                "Inicio del nodo. Se registra el estado observable de entrada; "
                "no se registran cadenas de pensamiento internas."
            ),
        )
        try:
            from opentelemetry import trace
            tracer = trace.get_tracer("aetherdeploy.agent")
            with tracer.start_as_current_span(node_fn.__name__) as span:
                span.set_attribute("agent.step", state.get("current_step", ""))
                span.set_attribute("agent.project", state.get("project_path", ""))
                span.set_attribute("agent.provider", state.get("preferred_provider", ""))
                result = await node_fn(state)
                span.set_attribute("agent.next_step", result.get("current_step", ""))
        except ImportError:
            result = await node_fn(state)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            log_event(
                "agent.node.error",
                level="ERROR",
                node=node_name,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log_event(
            "agent.node.end",
            node=node_name,
            duration_ms=duration_ms,
            output=_output_summary(result),
            decision_trace=(
                f"Nodo completado. Siguiente paso observable: {result.get('current_step', '')}."
            ),
        )
        return result
    wrapper.__name__ = node_fn.__name__
    return wrapper


def get_tracer(name: str = "aetherdeploy") -> Any:
    """Retorna el tracer activo o un tracer noop si OTel no está configurado."""
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        return _NoopTracer()


class _NoopTracer:
    """Tracer no-operativo cuando OpenTelemetry no está disponible."""
    def start_as_current_span(self, name: str, **_):
        return _NoopSpan()


class _NoopSpan:
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def set_attribute(self, *_): pass

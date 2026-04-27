from __future__ import annotations

import re
from pathlib import Path

from ..models import LanguageAnalysis
from .base import LanguageAnalyzer

_FRAMEWORK_MARKERS = {
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "starlette": "Starlette",
    "tornado": "Tornado",
    "sanic": "Sanic",
    "litestar": "Litestar",
    "aiohttp": "aiohttp",
    "falcon": "Falcon",
}

_DEFAULT_PORTS = {
    "fastapi": 8000, "flask": 5000, "django": 8000,
    "starlette": 8000, "aiohttp": 8080,
}

# New signal dep sets
_QUEUE_DEPS = {
    "rq", "dramatiq", "huey", "celery",  # celery already → has-worker, but also queue
    "kafka-python", "confluent-kafka", "pika", "aio-pika",
    "aiormq", "kombu", "nats-py",
}
_TRACING_DEPS = {
    "opentelemetry-sdk", "opentelemetry-api",
    "opentelemetry-exporter-zipkin", "opentelemetry-exporter-jaeger",
    "opentelemetry-exporter-otlp", "jaeger-client",
    "ddtrace", "elastic-apm",
}
_METRICS_DEPS = {
    "prometheus-client", "statsd", "datadog",
    "opentelemetry-exporter-prometheus",
}
_PAYMENT_DEPS = {
    "stripe", "paypalrestsdk", "braintree",
    "adyen", "square",
}
_MIGRATION_DEPS = {
    "alembic",
    "django",       # django always has migrations
    "yoyo-migrations", "sqlalchemy-migrate",
}
_GATEWAY_DEPS = {
    "kong", "traefik",
    "fastapi-gateway", "starlette-gateway",
}
_DISCOVERY_DEPS = {
    "python-consul", "python-consul2",
    "py-eureka-client", "py-zookeeper",
    "kubernetes",
}
_DB_DEPS = {
    "sqlalchemy", "tortoise-orm", "django", "peewee",
    "pymongo", "motor", "databases",
    "psycopg2", "psycopg", "asyncpg",
    "pymysql", "aiomysql", "aiosqlite",
}
_CACHE_DEPS = {
    "redis", "aioredis", "hiredis",
    "pymemcache", "aiomcache",
}


class PythonAnalyzer(LanguageAnalyzer):
    """Analyzes Python projects."""

    def can_analyze(self, path: Path) -> bool:
        return self._file_exists(
            path, "pyproject.toml", "requirements.txt",
            "setup.py", "setup.cfg", "Pipfile",
        )

    def analyze(self, path: Path) -> LanguageAnalysis:
        deps, dev_deps, version = self._parse_dependencies(path)
        framework = self._detect_framework(deps)
        ports = self._detect_ports(path, framework)

        has_tests = (
            any((path / d).is_dir() for d in ("tests", "test"))
            or bool(list(path.glob("test_*.py")))
            or "pytest" in dev_deps
        )

        hints = self._build_hints(deps, framework)

        return LanguageAnalysis(
            language="Python",
            framework=framework,
            version=version,
            dependencies=list(deps),
            dev_dependencies=list(dev_deps),
            entry_points=self._detect_entry_points(path),
            exposed_ports=ports,
            has_dockerfile=self._file_exists(path, "Dockerfile", "dockerfile"),
            has_tests=has_tests,
            architecture_hints=hints,
        )

    def _build_hints(self, deps: set[str], framework: str | None) -> list[str]:
        hints: list[str] = []

        if "celery" in deps or "rq" in deps or "dramatiq" in deps or "huey" in deps:
            hints.append("has-worker")
        if deps & _DB_DEPS:
            hints.append("has-database")
        if deps & _CACHE_DEPS:
            hints.append("has-cache")

        # New signals
        if deps & _QUEUE_DEPS:
            hints.append("has-queue")
        if deps & _PAYMENT_DEPS:
            hints.append("has-payment")
        if deps & _TRACING_DEPS:
            hints.append("has-tracing")
        if deps & _METRICS_DEPS:
            hints.append("has-metrics")
        if deps & _MIGRATION_DEPS:
            hints.append("has-migrations")
        if deps & _GATEWAY_DEPS:
            hints.append("has-gateway")
        if deps & _DISCOVERY_DEPS:
            hints.append("has-service-discovery")

        if not hints:
            hints.append("api-only")

        return hints

    def _parse_dependencies(self, path: Path) -> tuple[set[str], set[str], str | None]:
        deps: set[str] = set()
        dev_deps: set[str] = set()
        version: str | None = None

        pyproject = path / "pyproject.toml"
        if pyproject.exists():
            text = self._read_text(pyproject)
            m = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
            if m:
                version_match = re.search(r"(\d+\.\d+)", m.group(1))
                version = version_match.group(1) if version_match else m.group(1)
            for match in re.finditer(
                r'"([a-zA-Z0-9_\-]+)(?:[>=<!\[,][^"]*)?"|\'([a-zA-Z0-9_\-]+)', text
            ):
                name = (match.group(1) or match.group(2) or "").lower()
                if name:
                    deps.add(name)

        req = path / "requirements.txt"
        if req.exists():
            for line in req.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    name = re.split(r"[>=<!\[,;]", line)[0].strip().lower()
                    if name:
                        deps.add(name)

        # requirements-dev.txt or requirements/dev.txt
        for dev_file in (path / "requirements-dev.txt", path / "requirements" / "dev.txt"):
            if dev_file.exists():
                for line in dev_file.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        name = re.split(r"[>=<!\[,;]", line)[0].strip().lower()
                        if name:
                            dev_deps.add(name)

        return deps, dev_deps, version

    def _detect_framework(self, deps: set[str]) -> str | None:
        for key, name in _FRAMEWORK_MARKERS.items():
            if key in deps:
                return name
        return None

    def _detect_ports(self, path: Path, framework: str | None) -> list[int]:
        ports: set[int] = set()
        dockerfile = path / "Dockerfile"
        if dockerfile.exists():
            for match in re.finditer(r"EXPOSE\s+(\d+)", dockerfile.read_text()):
                ports.add(int(match.group(1)))
        if not ports and framework:
            default = _DEFAULT_PORTS.get(framework.lower(), 8000)
            ports.add(default)
        return sorted(ports) or [8000]

    def _detect_entry_points(self, path: Path) -> list[str]:
        entries = []
        for name in ("main.py", "app.py", "server.py", "wsgi.py", "asgi.py", "manage.py"):
            if (path / name).exists():
                entries.append(name)
        return entries

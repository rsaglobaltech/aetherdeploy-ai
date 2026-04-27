from __future__ import annotations

import re
from pathlib import Path

from ..models import LanguageAnalysis
from .base import LanguageAnalyzer

_FRAMEWORK_MARKERS = {
    "github.com/gin-gonic/gin": "Gin",
    "github.com/labstack/echo": "Echo",
    "github.com/gofiber/fiber": "Fiber",
    "github.com/gorilla/mux": "Gorilla Mux",
    "github.com/go-chi/chi": "Chi",
    "google.golang.org/grpc": "gRPC",
    "github.com/grpc-ecosystem/grpc-gateway": "gRPC-Gateway",
}

# New signal dep sets
_DB_DEPS = {
    "gorm.io/gorm", "gorm.io/driver",
    "github.com/jmoiron/sqlx",
    "github.com/jackc/pgx",
    "github.com/lib/pq",
    "go.mongodb.org/mongo-driver",
    "github.com/go-sql-driver/mysql",
}
_CACHE_DEPS = {
    "github.com/go-redis/redis",
    "github.com/redis/go-redis",
    "github.com/bradfitz/gomemcache",
}
_QUEUE_DEPS = {
    "github.com/hibiken/asynq",
    "github.com/ThreeDotsLabs/watermill",
    "github.com/nats-io/nats.go",
    "github.com/confluentinc/confluent-kafka-go",
    "github.com/segmentio/kafka-go",
    "github.com/rabbitmq/amqp091-go",
    "github.com/streadway/amqp",
}
_TRACING_DEPS = {
    "go.opentelemetry.io/otel",
    "github.com/openzipkin/zipkin-go",
    "github.com/uber/jaeger-client-go",
    "go.elastic.co/apm",
    "gopkg.in/DataDog/dd-trace-go.v1",
}
_METRICS_DEPS = {
    "github.com/prometheus/client_golang",
    "github.com/DataDog/datadog-go",
    "go.opentelemetry.io/otel/exporters/prometheus",
}
_PAYMENT_DEPS = {
    "github.com/stripe/stripe-go",
    "github.com/braintree-go/braintree-go",
    "github.com/plutov/paypal",
}
_MIGRATION_DEPS = {
    "github.com/golang-migrate/migrate",
    "github.com/pressly/goose",
    "github.com/rubenv/sql-migrate",
}
_DISCOVERY_DEPS = {
    "github.com/hashicorp/consul",
    "go.etcd.io/etcd",
    "k8s.io/client-go",
}


class GoAnalyzer(LanguageAnalyzer):
    """Analyzes Go projects."""

    def can_analyze(self, path: Path) -> bool:
        return (path / "go.mod").exists()

    def analyze(self, path: Path) -> LanguageAnalysis:
        text = self._read_text(path / "go.mod")
        version = self._parse_go_version(text)
        deps = self._parse_deps(text)
        framework = self._detect_framework(deps)

        has_tests = bool(list(path.rglob("*_test.go")))

        hints = self._build_hints(deps, framework)

        return LanguageAnalysis(
            language="Go",
            framework=framework,
            version=version,
            dependencies=deps,
            dev_dependencies=[],
            entry_points=["main.go"] if (path / "main.go").exists() else ["cmd/"],
            exposed_ports=self._detect_ports(path),
            has_dockerfile=self._file_exists(path, "Dockerfile"),
            has_tests=has_tests,
            architecture_hints=hints,
        )

    def _build_hints(self, deps: list[str], framework: str | None) -> list[str]:
        hints: list[str] = ["api-only"]
        dep_set = set(deps)

        def _match(candidates: set[str]) -> bool:
            return any(any(c in d for c in candidates) for d in dep_set)

        if _match(_DB_DEPS):
            hints.append("has-database")
        if _match(_CACHE_DEPS):
            hints.append("has-cache")
        if _match(_QUEUE_DEPS):
            hints.append("has-queue")
        if _match(_TRACING_DEPS):
            hints.append("has-tracing")
        if _match(_METRICS_DEPS):
            hints.append("has-metrics")
        if _match(_PAYMENT_DEPS):
            hints.append("has-payment")
        if _match(_MIGRATION_DEPS):
            hints.append("has-migrations")
        if _match(_DISCOVERY_DEPS):
            hints.append("has-service-discovery")

        return hints

    def _detect_ports(self, path: Path) -> list[int]:
        ports: set[int] = set()
        dockerfile = path / "Dockerfile"
        if dockerfile.exists():
            for match in re.finditer(r"EXPOSE\s+(\d+)", dockerfile.read_text()):
                ports.add(int(match.group(1)))
        return sorted(ports) or [8080]

    def _parse_go_version(self, text: str) -> str | None:
        m = re.search(r"^go\s+(\d+\.\d+)", text, re.MULTILINE)
        return m.group(1) if m else None

    def _parse_deps(self, text: str) -> list[str]:
        return re.findall(r"^\s+([a-zA-Z0-9./\-]+)\s+v", text, re.MULTILINE)

    def _detect_framework(self, deps: list[str]) -> str | None:
        for marker, name in _FRAMEWORK_MARKERS.items():
            if any(marker in d for d in deps):
                return name
        return None

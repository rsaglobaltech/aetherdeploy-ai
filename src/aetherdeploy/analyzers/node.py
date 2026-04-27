from __future__ import annotations

import re
from pathlib import Path

from ..models import LanguageAnalysis
from .base import LanguageAnalyzer

_FRAMEWORK_MARKERS = {
    "next": "Next.js",
    "nuxt": "Nuxt.js",
    "remix": "Remix",
    "gatsby": "Gatsby",
    "nestjs": "NestJS",
    "@nestjs/core": "NestJS",
    "fastify": "Fastify",
    "express": "Express",
    "koa": "Koa",
    "hapi": "@hapi/hapi",
}

# New signal dep sets
_QUEUE_DEPS = {
    "bull", "bullmq", "agenda", "bee-queue",
    "kafkajs", "kafka-node", "amqplib", "rhea",
    "node-nats", "nats",
}
_TRACING_DEPS = {
    "@opentelemetry/sdk-node", "@opentelemetry/api",
    "zipkin", "zipkin-javascript-opentracing", "jaeger-client",
    "dd-trace", "elastic-apm-node",
}
_METRICS_DEPS = {
    "prom-client", "hot-shots", "datadog-metrics",
    "@opentelemetry/exporter-prometheus",
}
_PAYMENT_DEPS = {
    "stripe", "paypal-rest-sdk", "braintree",
    "@adyen/api-library", "square",
}
_MIGRATION_DEPS = {
    "typeorm", "prisma", "@prisma/client",
    "knex", "sequelize", "mikro-orm",
    "db-migrate", "flyway-npm",
}
_GATEWAY_DEPS = {
    "http-proxy-middleware", "express-http-proxy",
    "fastify-http-proxy", "express-gateway",
    "@nestjs/microservices",
}
_DISCOVERY_DEPS = {
    "consul", "node-consul",
    "eureka-js-client", "netflix-eureka-client",
    "kubernetes-client",
}
_SERVER_DEPS = {
    "express", "fastify", "koa", "@hapi/hapi",
    "next", "nuxt", "remix", "nestjs", "@nestjs/core",
    "restify", "polka", "hono",
}


class NodeAnalyzer(LanguageAnalyzer):
    """Analyzes Node.js / TypeScript projects."""

    def can_analyze(self, path: Path) -> bool:
        return (path / "package.json").exists()

    def analyze(self, path: Path) -> LanguageAnalysis:
        pkg = self._read_json(path / "package.json")
        deps = {**pkg.get("dependencies", {}), **pkg.get("peerDependencies", {})}
        dev_deps = pkg.get("devDependencies", {})
        all_deps = {**deps, **dev_deps}

        framework = self._detect_framework(deps)
        version = self._detect_node_version(path, pkg)
        ports = self._detect_ports(path, pkg)
        entry_points = self._detect_entry_points(pkg)

        has_ts = (
            "typescript" in dev_deps
            or bool(list(path.glob("*.ts")))
            or bool(list(path.glob("tsconfig*.json")))
        )
        has_tests = any(k in dev_deps for k in (
            "jest", "vitest", "mocha", "@testing-library/react",
            "@jest/core", "jasmine",
        ))

        hints = self._build_hints(deps, all_deps, framework, path)

        return LanguageAnalysis(
            language="TypeScript" if has_ts else "Node.js",
            framework=framework,
            version=version,
            dependencies=list(deps.keys()),
            dev_dependencies=list(dev_deps.keys()),
            entry_points=entry_points,
            exposed_ports=ports,
            has_dockerfile=self._file_exists(path, "Dockerfile", "dockerfile"),
            has_tests=has_tests,
            architecture_hints=hints,
        )

    def _build_hints(
        self,
        deps: dict,
        all_deps: dict,
        framework: str | None,
        path: Path,
    ) -> list[str]:
        hints: list[str] = []
        dep_keys = set(all_deps.keys())

        # Existing signals
        if "next" in deps or "gatsby" in deps or "nuxt" in deps or "remix" in deps:
            hints.append("fullstack")
        if dep_keys & {"bull", "bullmq", "agenda"}:
            hints.append("has-worker")
        if (path / "docker-compose.yml").exists() or (path / "docker-compose.yaml").exists():
            hints.append("multi-service")

        # Database (ORM → implicit DB)
        if dep_keys & {"pg", "mysql", "mysql2", "mongodb", "mongoose",
                       "typeorm", "prisma", "@prisma/client", "sequelize",
                       "knex", "better-sqlite3"}:
            hints.append("has-database")

        # Cache
        if dep_keys & {"redis", "ioredis", "memcached", "@redis/client"}:
            hints.append("has-cache")

        # New signals
        if dep_keys & _QUEUE_DEPS:
            hints.append("has-queue")
        if dep_keys & _PAYMENT_DEPS:
            hints.append("has-payment")
        if dep_keys & _TRACING_DEPS:
            hints.append("has-tracing")
        if dep_keys & _METRICS_DEPS:
            hints.append("has-metrics")
        if dep_keys & _MIGRATION_DEPS:
            hints.append("has-migrations")
        if dep_keys & _GATEWAY_DEPS:
            hints.append("has-gateway")
        if dep_keys & _DISCOVERY_DEPS:
            hints.append("has-service-discovery")

        # Frontend-only: no server framework at all
        if not dep_keys & _SERVER_DEPS and (
            (path / "index.html").exists()
            or (path / "public" / "index.html").exists()
            or (path / "dist").is_dir()
        ):
            hints.append("is-frontend-only")

        if not hints:
            hints.append("api-only")

        return hints

    def _detect_framework(self, deps: dict) -> str | None:
        for key, name in _FRAMEWORK_MARKERS.items():
            if key in deps:
                return name
        return None

    def _detect_node_version(self, path: Path, pkg: dict) -> str | None:
        nvmrc = path / ".nvmrc"
        if nvmrc.exists():
            return nvmrc.read_text().strip()
        engines = pkg.get("engines", {})
        if "node" in engines:
            match = re.search(r"(\d+)", engines["node"])
            return match.group(1) if match else engines["node"]
        return None

    def _detect_ports(self, path: Path, pkg: dict) -> list[int]:
        ports: set[int] = set()
        for script in pkg.get("scripts", {}).values():
            for match in re.finditer(r"PORT[=\s]+(\d{4,5})|--port[=\s]+(\d{4,5})", script, re.I):
                p = match.group(1) or match.group(2)
                if p:
                    ports.add(int(p))
        dockerfile = path / "Dockerfile"
        if dockerfile.exists():
            for match in re.finditer(r"EXPOSE\s+(\d+)", dockerfile.read_text()):
                ports.add(int(match.group(1)))
        return sorted(ports) or [3000]

    def _detect_entry_points(self, pkg: dict) -> list[str]:
        entries = []
        if "main" in pkg:
            entries.append(pkg["main"])
        scripts = pkg.get("scripts", {})
        if "start" in scripts:
            entries.append(f"npm start → {scripts['start']}")
        return entries

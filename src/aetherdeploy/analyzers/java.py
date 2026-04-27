from __future__ import annotations

import re
from pathlib import Path

from ..models import LanguageAnalysis
from .base import LanguageAnalyzer

# Existing dep sets
_DB_DEPS = {
    "postgresql", "spring-boot-starter-data-jpa", "h2",
    "mysql-connector-j", "mysql-connector-java", "spring-data-jpa", "hibernate-core",
}
_CACHE_DEPS = {
    "spring-boot-starter-data-redis", "lettuce-core", "jedis",
}

# New signal dep sets (language-agnostic signal names, Java-specific dep names)
_GATEWAY_DEPS = {
    "spring-cloud-starter-gateway", "spring-cloud-netflix-zuul",
    "spring-cloud-starter-netflix-zuul",
}
_DISCOVERY_DEPS = {
    "spring-cloud-starter-netflix-eureka-client",
    "spring-cloud-starter-netflix-eureka-server",
    "spring-cloud-starter-consul-discovery",
    "spring-cloud-starter-consul-config",
}
_TRACING_DEPS = {
    "spring-cloud-starter-zipkin", "spring-cloud-sleuth",
    "spring-cloud-starter-sleuth", "micrometer-tracing-bridge-otel",
    "opentelemetry-api",
}
_METRICS_DEPS = {
    "micrometer-registry-prometheus", "micrometer-registry-influx",
    "micrometer-registry-datadog", "spring-boot-actuator",
}
_PAYMENT_DEPS = {
    "stripe-java", "braintree", "paypal-rest-sdk", "paypal-core",
    "adyen-java-api-library",
}
_QUEUE_DEPS = {
    "spring-kafka", "spring-rabbit", "spring-amqp",
    "aws-java-sdk-sqs", "software.amazon.awssdk-sqs",
    "activemq-broker", "artemis-jms-client",
}
_MIGRATION_DEPS = {
    "flyway-core", "flyway-mysql", "flyway-database-postgresql",
    "liquibase-core",
}


class JavaAnalyzer(LanguageAnalyzer):
    """Analyzes Java / Kotlin projects (Maven or Gradle)."""

    def can_analyze(self, path: Path) -> bool:
        return self._file_exists(path, "pom.xml", "build.gradle", "build.gradle.kts")

    def analyze(self, path: Path) -> LanguageAnalysis:
        is_kotlin = self._file_exists(path, "build.gradle.kts") or bool(list(path.rglob("*.kt")))
        language = "Kotlin" if is_kotlin else "Java"

        pom = path / "pom.xml"
        gradle = path / "build.gradle" if not is_kotlin else path / "build.gradle.kts"

        deps: list[str] = []
        framework: str | None = None
        java_version: str | None = None
        is_multi_module = False

        if pom.exists():
            text = self._read_text(pom)
            deps = re.findall(r"<artifactId>([^<]+)</artifactId>", text)
            m = re.search(r"<java\.version>(\d+)</java\.version>", text)
            if m:
                java_version = m.group(1)
            framework = self._detect_framework_from_deps(deps)
            is_multi_module = "<modules>" in text

            # Multi-module: also collect deps from declared sub-modules
            if is_multi_module:
                deps = list(set(deps) | self._collect_submodule_deps(path, text))

        elif gradle.exists():
            text = self._read_text(gradle)
            deps = re.findall(r"[\"']([a-zA-Z0-9.\-]+:[a-zA-Z0-9.\-]+):[^\"']+[\"']", text)
            m = re.search(r"sourceCompatibility\s*=\s*[\"']?(\d+)[\"']?", text)
            if m:
                java_version = m.group(1)
            framework = self._detect_framework_from_deps([d.split(":")[-1] for d in deps])
            # Gradle multi-project: settings.gradle declares include statements
            if self._file_exists(path, "settings.gradle", "settings.gradle.kts"):
                settings_text = self._read_text(path / "settings.gradle")
                if "include" in settings_text and settings_text.count("include") > 1:
                    is_multi_module = True

        has_tests = (path / "src/test").is_dir() or bool(list(path.rglob("*Test.java")))

        hints = self._build_hints(deps, framework, is_multi_module)

        return LanguageAnalysis(
            language=language,
            framework=framework,
            version=java_version,
            dependencies=deps[:50],
            dev_dependencies=[],
            entry_points=["src/main/"],
            exposed_ports=self._detect_ports(path, deps),
            has_dockerfile=self._file_exists(path, "Dockerfile"),
            has_tests=has_tests,
            architecture_hints=hints,
        )

    def _build_hints(
        self, deps: list[str], framework: str | None, is_multi_module: bool
    ) -> list[str]:
        hints: list[str] = ["api-only"] if framework else ["monolith"]
        deps_lower = {d.lower() for d in deps}

        if deps_lower & _DB_DEPS:
            hints.append("has-database")
        if deps_lower & _CACHE_DEPS:
            hints.append("has-cache")
        if deps_lower & _GATEWAY_DEPS:
            hints.append("has-gateway")
        if deps_lower & _DISCOVERY_DEPS:
            hints.append("has-service-discovery")
        if deps_lower & _TRACING_DEPS:
            hints.append("has-tracing")
        if deps_lower & _METRICS_DEPS:
            hints.append("has-metrics")
        if deps_lower & _PAYMENT_DEPS:
            hints.append("has-payment")
        if deps_lower & _QUEUE_DEPS:
            hints.append("has-queue")
        if deps_lower & _MIGRATION_DEPS:
            hints.append("has-migrations")
        if is_multi_module:
            hints.append("is-multi-module")

        return hints

    def _collect_submodule_deps(self, root: Path, parent_pom_text: str) -> set[str]:
        """Reads <module> entries in a multi-module pom and collects their artifact IDs."""
        module_names = re.findall(r"<module>([^<]+)</module>", parent_pom_text)
        collected: set[str] = set()
        for module in module_names:
            sub_pom = root / module.strip() / "pom.xml"
            if sub_pom.exists():
                sub_text = self._read_text(sub_pom)
                sub_deps = re.findall(r"<artifactId>([^<]+)</artifactId>", sub_text)
                collected.update(sub_deps)
        return collected

    def _detect_ports(self, path: Path, deps: list[str]) -> list[int]:
        """Reads server.port from application.properties / application.yml."""
        ports: set[int] = set()
        for config_file in (
            "src/main/resources/application.properties",
            "src/main/resources/application.yml",
            "src/main/resources/application.yaml",
        ):
            config_path = path / config_file
            if not config_path.exists():
                continue
            text = self._read_text(config_path)
            # properties: server.port=8080 or server.port=${SERVER_PORT:8080}
            m = re.search(r"server\.port\s*[=:]\s*\$?\{?(?:SERVER_PORT[^}]*:)?(\d{4,5})", text)
            if m:
                ports.add(int(m.group(1)))
        return sorted(ports) or [8080]

    def _detect_framework_from_deps(self, deps: list[str]) -> str | None:
        lower = [d.lower() for d in deps]
        if any("spring-boot" in d for d in lower):
            return "Spring Boot"
        if any("micronaut" in d for d in lower):
            return "Micronaut"
        if any("quarkus" in d for d in lower):
            return "Quarkus"
        if any("vert.x" in d or "vertx" in d for d in lower):
            return "Vert.x"
        return None

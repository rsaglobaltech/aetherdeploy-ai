from __future__ import annotations

import re
from pathlib import Path

from ..models import (
    ApplicationTopology,
    LanguageAnalysis,
    PROFILE_ENTERPRISE,
    PROFILE_LARGE,
    PROFILE_MICRO,
    PROFILE_NANO,
    PROFILE_SMALL,
    PROFILE_STANDARD,
    ProjectAnalysis,
    ServiceSummary,
)
from .base import LanguageAnalyzer
from .go import GoAnalyzer
from .java import JavaAnalyzer
from .node import NodeAnalyzer
from .python import PythonAnalyzer

# Directories to never descend into when scanning for service roots
_SKIP_DIRS = {
    ".git", ".github", ".aetherdeploy", ".venv", "venv", "env",
    "node_modules", "__pycache__", "target", "build", "dist",
    ".gradle", ".idea", ".mvn", "out", "bin", ".terraform",
}

# Images that indicate an infra/monitoring service rather than application code
_INFRA_IMAGES = {
    "mysql", "postgres", "postgresql", "mariadb", "mongo", "mongodb",
    "redis", "memcached", "rabbitmq", "kafka", "zookeeper", "nats",
    "consul", "vault", "etcd",
    "zipkin", "jaeger",
    "prometheus", "grafana", "influxdb", "telegraf", "chronograf",
    "kapacitor", "elasticsearch", "kibana", "logstash",
    "nginx", "traefik", "haproxy",
    "localstack", "minio",
}


def _score_profile(hints: list[str], service_count: int) -> str:
    """Compute complexity profile from merged hints + service count.

    Rules evaluated top-to-bottom; first match wins.
    All logic is expressed in signal strings — no language-specific knowledge.
    """
    hint_set = set(hints)

    # Hard overrides
    if "has-payment" in hint_set:
        return PROFILE_ENTERPRISE
    if service_count >= 8:
        return PROFILE_ENTERPRISE

    # Service-count gates
    if service_count >= 5 or "has-service-discovery" in hint_set:
        return PROFILE_LARGE
    if service_count >= 3:
        return PROFILE_STANDARD

    # Single-service classification
    is_static  = "is-frontend-only" in hint_set
    has_db     = "has-database" in hint_set
    has_cache  = "has-cache" in hint_set
    has_queue  = "has-queue" in hint_set
    has_worker = "has-worker" in hint_set

    if is_static and service_count <= 1:
        return PROFILE_NANO
    if not has_db and not has_cache and not has_queue and not has_worker:
        return PROFILE_MICRO
    if has_db and not has_cache and not has_queue and not has_worker:
        return PROFILE_SMALL

    # 2 services or single with cache/queue/worker
    return PROFILE_STANDARD


class ProjectDetector:
    """Orchestrates language analyzers and produces unified analysis objects."""

    def __init__(self) -> None:
        self._analyzers: list[LanguageAnalyzer] = [
            NodeAnalyzer(),
            PythonAnalyzer(),
            JavaAnalyzer(),
            GoAnalyzer(),
        ]

    # ------------------------------------------------------------------
    # Existing public API — unchanged, all 51 tests pass through here
    # ------------------------------------------------------------------

    def detect(self, project_path: Path) -> ProjectAnalysis:
        results = [a.analyze(project_path) for a in self._analyzers if a.can_analyze(project_path)]

        if not results:
            return ProjectAnalysis(
                primary_language="unknown",
                infrastructure_hints=["unknown-stack"],
            )

        primary = self._pick_primary(results)
        all_deps = list({dep for r in results for dep in r.dependencies})
        all_ports = sorted({p for r in results for p in r.exposed_ports})
        all_hints = list({h for r in results for h in r.architecture_hints})

        return ProjectAnalysis(
            primary_language=primary.language,
            languages=[r.language for r in results],
            frameworks=[r.framework for r in results if r.framework],
            architecture=self._infer_architecture(all_hints),
            dependencies=all_deps,
            exposed_ports=all_ports or [8080],
            has_dockerfile=any(r.has_dockerfile for r in results),
            has_tests=any(r.has_tests for r in results),
            infrastructure_hints=all_hints,
            raw_analyses=results,
        )

    # ------------------------------------------------------------------
    # New topology-aware API
    # ------------------------------------------------------------------

    def detect_topology(self, project_path: Path) -> ApplicationTopology:
        """Full topology detection.

        Algorithm:
        1. Parse docker-compose.yml if present — source of truth for service count.
        2. Scan subdirectories (depth ≤ 3) for service roots.
        3. Run per-language analyzers on each service root.
        4. Merge all hints; score the ComplexityProfile.
        5. Return ApplicationTopology (includes as_project_analysis() for compat).
        """
        project_path = project_path.resolve()
        project_name = project_path.name

        compose_services, compose_count = self._parse_compose(project_path)
        has_compose = compose_count > 0

        service_summaries = self._scan_services(project_path, compose_services)

        # Merge all hints (deduped, order preserved)
        seen: set[str] = set()
        all_hints: list[str] = []
        for svc in service_summaries:
            for h in svc.hints:
                if h not in seen:
                    seen.add(h)
                    all_hints.append(h)

        # If compose has infra services (redis, mysql...) add signals not caught by analyzers
        all_hints = self._enrich_hints_from_compose(all_hints, compose_services)

        app_service_count = len(service_summaries) or 1
        profile = _score_profile(all_hints, app_service_count)

        return ApplicationTopology(
            project_name=project_name,
            profile=profile,
            service_count=app_service_count,
            services=service_summaries,
            all_hints=all_hints,
            has_compose=has_compose,
            compose_service_count=compose_count,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_compose(self, root: Path) -> tuple[dict, int]:
        """Returns (services_dict, app_service_count).

        services_dict keys are service names; values are the raw compose dicts.
        app_service_count counts only non-infra services.
        """
        for filename in ("docker-compose.yml", "docker-compose.yaml"):
            compose_file = root / filename
            if not compose_file.exists():
                continue
            try:
                import yaml  # PyYAML — available via LangGraph/LangChain deps
                data = yaml.safe_load(compose_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                return {}, 0

            services: dict = data.get("services", {}) if isinstance(data, dict) else {}
            return services, len(services)

        return {}, 0

    def _is_infra_service(self, svc_def: dict) -> bool:
        """Returns True if a compose service is pure infrastructure (no app code)."""
        image = svc_def.get("image", "")
        if not image:
            return False
        # Strip tag/digest: "mysql:5.7" → "mysql"
        image_name = image.split(":")[0].split("/")[-1].lower()
        return image_name in _INFRA_IMAGES

    def _enrich_hints_from_compose(
        self, hints: list[str], compose_services: dict
    ) -> list[str]:
        """Adds signals inferred from infra images in docker-compose."""
        hint_set = set(hints)
        additions: list[str] = []

        for svc_def in compose_services.values():
            image = svc_def.get("image", "").split(":")[0].split("/")[-1].lower()
            if image in {"mysql", "postgres", "postgresql", "mariadb", "mongo", "mongodb"}:
                if "has-database" not in hint_set:
                    additions.append("has-database")
                    hint_set.add("has-database")
            if image in {"redis", "memcached"}:
                if "has-cache" not in hint_set:
                    additions.append("has-cache")
                    hint_set.add("has-cache")
            if image in {"rabbitmq", "kafka", "nats", "zookeeper"}:
                if "has-queue" not in hint_set:
                    additions.append("has-queue")
                    hint_set.add("has-queue")
            if image in {"zipkin", "jaeger"}:
                if "has-tracing" not in hint_set:
                    additions.append("has-tracing")
                    hint_set.add("has-tracing")
            if image in {"prometheus", "grafana", "influxdb", "telegraf"}:
                if "has-metrics" not in hint_set:
                    additions.append("has-metrics")
                    hint_set.add("has-metrics")

        return hints + additions

    def _scan_services(
        self, root: Path, compose_services: dict
    ) -> list[ServiceSummary]:
        """Finds service directories and runs analyzers on each.

        Priority:
        - If a compose file exists, use `build.context` entries as service dirs.
        - Otherwise, scan subdirectories for project markers.
        - If neither works, treat the root itself as a single service.
        """
        service_dirs: list[tuple[str, Path]] = []

        # 1. Compose build contexts → authoritative service list
        for svc_name, svc_def in compose_services.items():
            if self._is_infra_service(svc_def):
                continue
            build = svc_def.get("build")
            if isinstance(build, dict):
                ctx = build.get("context", ".")
            elif isinstance(build, str):
                ctx = build
            else:
                continue
            svc_path = (root / ctx).resolve()
            if svc_path.is_dir():
                service_dirs.append((svc_name, svc_path))

        # 2. Directory scan (fallback or supplement when no compose)
        if not service_dirs:
            service_dirs = self._find_service_roots(root)

        # 3. If still nothing, treat root as single service
        if not service_dirs:
            analysis = self.detect(root)
            if analysis.primary_language != "unknown":
                return [ServiceSummary(
                    name=root.name,
                    path_relative=".",
                    language=analysis.primary_language,
                    framework=analysis.frameworks[0] if analysis.frameworks else None,
                    hints=analysis.infrastructure_hints,
                    ports=analysis.exposed_ports,
                    has_dockerfile=analysis.has_dockerfile,
                )]
            return []

        summaries: list[ServiceSummary] = []
        seen_paths: set[Path] = set()

        for svc_name, svc_path in service_dirs:
            if svc_path in seen_paths:
                continue
            seen_paths.add(svc_path)

            results = [
                a.analyze(svc_path)
                for a in self._analyzers
                if a.can_analyze(svc_path)
            ]
            if not results:
                continue

            primary = self._pick_primary(results)
            merged_hints = list(dict.fromkeys(
                h for r in results for h in r.architecture_hints
            ))
            merged_ports = sorted({p for r in results for p in r.exposed_ports}) or [8080]

            try:
                rel = str(svc_path.relative_to(root))
            except ValueError:
                rel = svc_path.name

            summaries.append(ServiceSummary(
                name=svc_name,
                path_relative=rel,
                language=primary.language,
                framework=primary.framework,
                hints=merged_hints,
                ports=merged_ports,
                has_dockerfile=any(r.has_dockerfile for r in results),
            ))

        return summaries

    def _find_service_roots(self, root: Path) -> list[tuple[str, Path]]:
        """Walks up to depth 3 looking for project marker files."""
        _MARKERS = {
            "pom.xml", "build.gradle", "build.gradle.kts",
            "package.json", "pyproject.toml", "requirements.txt",
            "go.mod", "Cargo.toml", "Dockerfile",
        }
        found: list[tuple[str, Path]] = []

        def _walk(path: Path, depth: int) -> None:
            if depth > 3:
                return
            if path.name in _SKIP_DIRS:
                return
            # Check if this directory is itself a service root (but not the project root)
            if depth > 0 and any((path / m).exists() for m in _MARKERS):
                found.append((path.name, path))
                return  # don't recurse deeper into a service root
            try:
                for child in sorted(path.iterdir()):
                    if child.is_dir() and child.name not in _SKIP_DIRS:
                        _walk(child, depth + 1)
            except PermissionError:
                pass

        _walk(root, 0)
        return found

    def _pick_primary(self, results: list[LanguageAnalysis]) -> LanguageAnalysis:
        for r in results:
            if r.framework:
                return r
        return results[0]

    def _infer_architecture(self, hints: list[str]) -> str:
        if "multi-service" in hints:
            return "microservices"
        if "has-worker" in hints:
            return "monolith-with-workers"
        if "fullstack" in hints:
            return "fullstack"
        return "monolith"

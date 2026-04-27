"""LLM-based second pass that enriches architecture_hints after static analysis.

Design:
- Static pass always runs first (sync, fast, no LLM dependency).
- This module is the async second pass: gated, bounded, graceful fallback.
- Uses the existing LLMBackend.complete_json() — no new LLM infrastructure needed.
- Context is always capped to fit small models (Ollama/Gemma3: 4096 token window).
- Never crashes the pipeline: any exception returns the original hints unchanged.
"""
from __future__ import annotations

import json as _json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_config
from ..llm.base import LLMBackendFactory
from ..observability import log_event

# Nominal character budget for project context.
# Conservative: ~2 000 tokens × 4 chars/token = 8 000 chars.
# Adapted at runtime via llm_context_window config.
_DEFAULT_BUDGET = 8_000

# Valid signal strings — the LLM must only return values from this set
_VALID_HINTS = {
    "api-only", "has-database", "has-cache", "has-worker", "fullstack",
    "multi-service", "has-queue", "has-gateway", "has-service-discovery",
    "has-tracing", "has-metrics", "has-payment", "is-frontend-only",
    "is-multi-module", "has-migrations",
}

ANALYSIS_ENRICHMENT_SYSTEM = """\
You are AetherDeploy, a cloud deployment agent analysing a software project.
You are given static analysis results (signals already detected) and key project
file snippets. Your task: identify any signals the static analysis may have missed,
and remove any that are clearly wrong.

Signal vocabulary — use ONLY these exact strings:
  api-only, has-database, has-cache, has-worker, fullstack, multi-service,
  has-queue, has-gateway, has-service-discovery, has-tracing, has-metrics,
  has-payment, is-frontend-only, is-multi-module, has-migrations

Clues to look for in file contents:
  - Environment variables like STRIPE_SECRET_KEY, DATABASE_URL, REDIS_URL,
    KAFKA_BROKERS, ZIPKIN_HOST, INFLUXDB_URL → signals
  - docker-compose images: mysql/postgres → has-database, redis → has-cache,
    rabbitmq/kafka → has-queue, zipkin/jaeger → has-tracing
  - README mentions payment, microservices, service mesh, event bus, etc.

Return ONLY valid JSON (no markdown, no explanation outside JSON):
{
  "additional_hints": [...],
  "remove_hints":    [...],
  "confidence":      0.0,
  "reasoning":       "one sentence"
}
"""


@dataclass
class EnrichmentResult:
    hints: list[str]
    confidence: float
    llm_used: bool
    reasoning: str = ""


class LLMAnalysisEnricher:
    """Enriches architecture_hints using the configured LLM backend."""

    async def enrich(
        self,
        static_hints: list[str],
        service_count: int,
        project_path: Path,
    ) -> EnrichmentResult:
        """Run the LLM second pass.

        Returns the original hints unchanged on any failure — never raises.
        """
        if not self._should_enrich(static_hints, service_count, project_path):
            log_event(
                "analysis.llm_enricher.skipped",
                reason="gating: rich hints, single service, no compose",
                hints=static_hints,
            )
            return EnrichmentResult(hints=static_hints, confidence=1.0, llm_used=False)

        context = _build_context(project_path, static_hints)
        config = get_config()

        try:
            backend = LLMBackendFactory.create(
                config.llm_backend,
                model=config.llm_model,
                base_url=config.llm_base_url,
                api_key=config.llm_api_key,
            )
        except Exception as exc:
            log_event(
                "analysis.llm_enricher.unavailable",
                level="WARNING",
                reason=str(exc),
            )
            return EnrichmentResult(hints=static_hints, confidence=0.7, llm_used=False)

        messages = [{"role": "user", "content": context}]
        try:
            data = await backend.complete_json(messages, system=ANALYSIS_ENRICHMENT_SYSTEM)
        except Exception as exc:
            log_event("analysis.llm_enricher.error", level="WARNING", error=str(exc))
            return EnrichmentResult(hints=static_hints, confidence=0.7, llm_used=False)
        finally:
            close = getattr(backend, "aclose", None)
            if close:
                try:
                    await close()
                except Exception:
                    pass

        # Validate and apply LLM response
        additional = [
            h for h in data.get("additional_hints", [])
            if isinstance(h, str) and h in _VALID_HINTS and h not in static_hints
        ]
        remove = {
            h for h in data.get("remove_hints", [])
            if isinstance(h, str) and h in _VALID_HINTS
        }
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.8))))
        reasoning = str(data.get("reasoning", ""))[:300]

        merged = [h for h in static_hints if h not in remove] + additional

        log_event(
            "analysis.llm_enricher.done",
            added=additional,
            removed=list(remove),
            confidence=confidence,
            reasoning=reasoning,
        )
        return EnrichmentResult(
            hints=merged,
            confidence=confidence,
            llm_used=True,
            reasoning=reasoning,
        )

    def _should_enrich(
        self, hints: list[str], service_count: int, project_path: Path
    ) -> bool:
        """Gate: skip LLM for small projects with rich static hints."""
        has_compose = (
            (project_path / "docker-compose.yml").exists()
            or (project_path / "docker-compose.yaml").exists()
        )
        hint_set = set(hints)
        sparse = hint_set <= {"api-only"}  # only the default — static found nothing
        return (
            service_count >= 2
            or has_compose
            or sparse
            or "multi-service" in hint_set
        )


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _build_context(project_path: Path, static_hints: list[str]) -> str:
    """Builds a compact project context within the configured character budget."""
    config = get_config()
    # Conservative: leave half the window for system prompt + response
    budget = min(_DEFAULT_BUDGET, getattr(config, "llm_context_window", 4096) * 2)

    header = (
        f"Static analysis detected these signals: "
        f"{', '.join(static_hints) if static_hints else 'none'}\n\n"
        "Project file snippets follow. Identify any missed or incorrect signals.\n\n"
    )
    parts: list[str] = [header]
    budget -= len(header)

    # Priority order — most signal-rich files first
    candidates = [
        ("docker-compose.yml",           80),
        ("docker-compose.yaml",          80),
        (".env.example",                 60),
        (".env.sample",                  60),
        (".env",                         40),
        ("application.yml",              50),
        ("application.properties",       50),
        ("requirements.txt",             60),
        ("package.json",                 40),
        ("pom.xml",                      30),
        ("go.mod",                       40),
        ("README.md",                    30),
        ("Dockerfile",                   25),
        ("docker-compose.override.yml",  40),
    ]

    for filename, max_lines in candidates:
        if budget <= 200:
            break

        candidate = project_path / filename
        if not candidate.exists():
            # One level deep (multi-module layouts)
            matches = sorted(project_path.glob(f"*/{filename}"))[:3]
            if not matches:
                continue
            candidate = matches[0]

        snippet = _read_snippet(candidate, filename, max_lines)
        if not snippet:
            continue

        block = f"=== {filename} ===\n{snippet}\n\n"
        if len(block) > budget:
            block = block[:budget] + "...[truncated]\n\n"
        parts.append(block)
        budget -= len(block)

    return "".join(parts)


def _read_snippet(path: Path, filename: str, max_lines: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    # For package.json extract only the dep keys (compact, most signal-dense)
    if filename == "package.json":
        try:
            data = _json.loads(text)
            all_deps = {
                **data.get("dependencies", {}),
                **data.get("devDependencies", {}),
            }
            if all_deps:
                return "dependencies: " + ", ".join(sorted(all_deps.keys())[:80])
        except Exception:
            pass

    # For pom.xml extract artifact IDs (more readable than full XML)
    if filename == "pom.xml":
        ids = re.findall(r"<artifactId>([^<]+)</artifactId>", text)
        if ids:
            return "artifactIds: " + ", ".join(list(dict.fromkeys(ids))[:60])

    return "\n".join(text.splitlines()[:max_lines])

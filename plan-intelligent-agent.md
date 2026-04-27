# AetherDeploy — Proactive Intelligence Plan

> **Core principle**: understand the project first, then propose the minimum viable
> infrastructure that fits it. A single Spring Boot CRUD API is not a distributed platform.
>
> BookStoreApp (distributed, 8+ services) is the ceiling.
> `sample_spring_project` (1 service + PostgreSQL) is the floor.
> Every project lands at the right level automatically, regardless of language.

---

## Progress

| Step | Status | Notes |
|---|---|---|
| **1** Extend `JavaAnalyzer` | ✅ done | Multi-module scan, 9 new signals, port from application.yml |
| **2** Extend `NodeAnalyzer` | ✅ done | 7 new signals, DB detection, frontend-only detection |
| **3** Extend `PythonAnalyzer` | ✅ done | 7 new signals, requirements-dev.txt |
| **4** Extend `GoAnalyzer` | ✅ done | 8 new signals, port from Dockerfile |
| **5** New dataclasses `models.py` | ✅ done | `ApplicationTopology`, `ServiceSummary`, `ProposalMetadata`, `PROFILE_*` constants |
| **6** `detect_topology()` in `detector.py` | ✅ done | Compose parser, service scanner, profile scorer, hint enrichment from infra images |
| **7** `llm_enricher.py` | ✅ done | `LLMAnalysisEnricher` + gating + context builder + 22 tests |
| **8** Register types in `graph.py` | ✅ done | `ApplicationTopology`, `ProposalMetadata`, `ServiceSummary` added to `_SERDE` |
| **9** `application_topology` in `AetherState` | ✅ done | Optional field added to `AetherState` TypedDict |
| **10** Update `analysis_node` | ✅ done | Uses `detect_topology()` + `LLMAnalysisEnricher`, emits `analysis_complete` event with profile |
| **11** `_propose_for_profile` AWS | ✅ done | 5 profile methods: nano/micro/small/standard/large+enterprise |
| **12** GCP + Azure profile dispatch | ✅ done | Same pattern as AWS in both GCP and Azure providers |
| **13** `proposal_node` passes topology | ✅ done | `topology` passed to `recommend_architecture()` and `_enrich_with_llm()` |
| **14** `small/main.tf.j2` | ✅ done | App Runner + Aurora Serverless v2, VPC Connector, Secrets Manager |
| **15** `micro/` + `nano/` templates | ✅ done | micro: App Runner only; nano: S3 + CloudFront OAC |
| **16** Generator routing by profile | ✅ done | `_resolve_template()` routes via `_PROFILE_TO_TIER`, fallback to legacy `main.tf.j2` |
| **17** `analyze` CLI command | ✅ done | `aetherdeploy analyze [--output json] [--llm]` — no credentials required |
| **18** Topology-aware LLM prompt | ✅ done | `architecture_review_user_prompt` extended with `profile`, `service_count`, `all_hints` optional params; `TOPOLOGY_REASONING_SYSTEM` added |
| **19** `large/enterprise` multi-file templates | ✅ done | `tiers/large/main.tf.j2`: ECS Fargate + Service Connect + ALB + optional Aurora/Redis/WAF |

---

## Audit: What Is Wrong in the Existing Code

| Location | Problem |
|---|---|
| `analyzers/detector.py` | Reads only root-level markers — misses multi-module projects |
| `analyzers/java.py` | Reads only root `pom.xml`, not `<modules>` sub-directories |
| `providers/aws/provider.py` | Always recommends the same shape: ECS + VPC + optional DB |
| `agent/nodes/proposal.py` | LLM annotates a pre-built proposal; never sees the code |
| `agent/nodes/confirmation.py` | HIL is approve/reject only; no natural-language modification |
| `cli.py` | No command to show what the agent understood before deploying |
| `terraform/templates/aws/main.tf.j2` | One template for all project sizes — always generates VPC + NAT + ALB |

---

## Architecture Contract: What Cannot Break

These are invariants. Every phase must preserve them.

1. **All 51 existing tests pass.** `ProjectDetector.detect()`, `LanguageAnalyzer` ABC,
   `ProjectAnalysis`, `ArchitectureProposal`, `AWSProvider.recommend_architecture(analysis, envs)`
   — all unchanged in signature and behavior.
2. **`AetherState` fields are additive only.** New optional fields; no field renamed or removed.
3. **`JsonPlusSerializer` registration.** Every new dataclass stored in `AetherState` must
   be added to `_SERDE` in `graph.py`.
4. **All three providers (AWS, GCP, Azure) must be updated together.** New behavior in AWS
   without GCP/Azure equivalents is unacceptable.
5. **Language-agnostic signals.** No analyzer-specific class is called from the
   detector or the topology layer. The detector speaks only in normalized signal strings.

---

## The Core Abstraction: Normalized Service Signals

All four existing analyzers already produce `architecture_hints: list[str]`.
The signal vocabulary is extended — not replaced — to carry more information.

### Extended signal vocabulary

Each `LanguageAnalyzer.analyze()` may return any of these strings in `architecture_hints`.
The existing strings are preserved exactly.

```python
# Already exist — DO NOT RENAME
"api-only"       # stateless single service
"has-database"   # any relational/document DB dependency
"has-cache"      # Redis, Memcached, etc.
"has-worker"     # background job processor (Celery, Bull, Sidekiq...)
"fullstack"      # server-side rendering (Next.js, Nuxt, Remix...)
"multi-service"  # docker-compose with multiple services detected

# New signals — language-agnostic, added by any analyzer that detects them
"has-queue"         # Kafka, RabbitMQ, SQS, RQ, Dramatiq
"has-gateway"       # API gateway in the project (Zuul, Kong, traefik, NGINX as gateway)
"has-service-discovery"  # Eureka, Consul, etcd client detected
"has-tracing"       # Zipkin, Jaeger, OpenTelemetry exporter
"has-metrics"       # Prometheus, StatsD, Datadog agent
"has-payment"       # Stripe, PayPal, Braintree, Adyen SDK
"is-frontend-only"  # No server-side code, only static assets
"is-multi-module"   # Maven multi-module, Nx monorepo, Go workspace, etc.
"has-migrations"    # Flyway, Alembic, Django migrations, golang-migrate
```

### How each analyzer emits new signals

**`analyzers/java.py`** — extended, not replaced:

```python
# Spring Cloud pattern detection (Java-specific, inside JavaAnalyzer.analyze)
_SC_GATEWAY_DEPS   = {"spring-cloud-starter-gateway", "spring-cloud-netflix-zuul"}
_SC_DISCOVERY_DEPS = {"spring-cloud-starter-netflix-eureka-client",
                      "spring-cloud-starter-consul-discovery"}
_SC_TRACING_DEPS   = {"spring-cloud-starter-zipkin", "spring-cloud-sleuth"}
_SC_FEIGN_DEPS     = {"spring-cloud-starter-openfeign"}
_PAYMENT_DEPS      = {"stripe-java", "braintree", "paypal-rest-sdk"}
_MIGRATION_DEPS    = {"flyway-core", "liquibase-core"}
_MULTI_MODULE_TAG  = "<modules>"  # present in parent pom.xml
```

**`analyzers/node.py`** — extended:

```python
_GATEWAY_DEPS   = {"@nestjs/platform-express", "http-proxy-middleware", "express-gateway"}
_QUEUE_DEPS     = {"bull", "bullmq", "agenda", "bee-queue", "kafkajs", "amqplib"}
_TRACING_DEPS   = {"@opentelemetry/sdk-node", "zipkin", "jaeger-client"}
_PAYMENT_DEPS   = {"stripe", "paypal-rest-sdk", "braintree"}
_MIGRATION_DEPS = {"typeorm", "prisma", "knex", "sequelize"}  # ORM = implicit migration
```

**`analyzers/python.py`** — extended:

```python
_QUEUE_DEPS     = {"rq", "dramatiq", "huey", "kafka-python", "pika"}
_TRACING_DEPS   = {"opentelemetry-sdk", "opentelemetry-exporter-zipkin", "jaeger-client"}
_PAYMENT_DEPS   = {"stripe", "paypalrestsdk", "braintree"}
_MIGRATION_DEPS = {"alembic", "django"}  # django always has migrations
_GATEWAY_DEPS   = {"kong", "traefik"}    # rarely in Python, but possible
```

**`analyzers/go.py`** — extended:

```python
_QUEUE_DEPS    = {"github.com/hibiken/asynq", "github.com/ThreeDotsLabs/watermill"}
_TRACING_DEPS  = {"go.opentelemetry.io/otel"}
_PAYMENT_DEPS  = {"github.com/stripe/stripe-go"}
```

The detector and topology layer **never** call a language-specific class directly.
They read the `architecture_hints` list that analyzers produce.

---

## Phase A6 — LLM-Augmented Analysis (Two-Pass Architecture)

Static regex over `pom.xml`, `package.json`, and `go.mod` misses real signals:
- `STRIPE_SECRET_KEY` in `.env.example` — payment, no SDK in deps
- `DATABASE_URL` environment variable without an ORM dep
- A `docker-compose.yml` dependency on Redis that isn't in any source file's imports
- A `README.md` that says "this is a microservices platform"
- Custom HTTP clients calling external APIs not detectable from library names

The static pass is always correct and fast. The LLM pass catches what static analysis
cannot infer from dependency names alone.

### Two-pass design

```
static pass (sync, fast, no LLM, always runs)
      │
      │ architecture_hints (partial — what static analysis can see)
      ▼
LLM pass (async, gated, uses existing LLMBackend)
      │
      │ architecture_hints (enriched — static + what LLM sees in files)
      ▼
profile scoring (reads final merged hints)
      │
      ▼
ApplicationTopology (final, high-confidence)
```

**Gating rule**: LLM enrichment runs when ANY of:
- `service_count >= 2` (multi-service — higher chance of missed signals)
- `profile >= "standard"` based on static hints alone
- static hints contains only `["api-only"]` with no other signals (suspiciously sparse)
- a `docker-compose.yml` exists (compose often reveals services not in source deps)

For a simple NANO/MICRO project with rich static hints, LLM enrichment is skipped.
This keeps the `analyze` command fast for simple projects.

### New file: `src/aetherdeploy/analyzers/llm_enricher.py`

```python
"""LLM-based second pass that enriches architecture_hints after static analysis."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..config import get_config
from ..llm.base import LLMBackendFactory
from ..observability import log_event

# Max characters of project context sent to the LLM.
# ~2 000 chars ≈ ~500 tokens — fits within any Ollama context window.
_CONTEXT_BUDGET = 8_000   # chars; ~2 000 tokens


ANALYSIS_ENRICHMENT_SYSTEM = """\
You are AetherDeploy, a cloud deployment agent analysing a software project.
You are given the results of static code analysis (already-detected signals) and
key project file snippets. Identify any signals the static analysis may have missed.

Signal vocabulary (use ONLY these values):
  has-database, has-cache, has-queue, has-worker, has-gateway,
  has-service-discovery, has-tracing, has-metrics, has-payment,
  is-frontend-only, is-multi-module, has-migrations,
  fullstack, multi-service, api-only

Return ONLY valid JSON:
{
  "additional_hints": [...],   // signals to add (not already in static_hints)
  "remove_hints":    [...],    // signals to remove (static analysis got wrong)
  "confidence":      0.0-1.0,  // how confident you are in the full picture
  "reasoning":       "..."     // one sentence: most important finding
}
"""


class LLMAnalysisEnricher:
    """Enriches architecture_hints using the configured LLM backend.

    Designed for fast, bounded calls: context is always capped, prompt is short,
    and the enricher falls back silently if the LLM is unavailable.
    """

    async def enrich(
        self,
        static_hints: list[str],
        service_count: int,
        project_path: Path,
    ) -> EnrichmentResult:
        """Run the LLM second pass. Returns the original hints unchanged on failure."""
        if not self._should_enrich(static_hints, service_count, project_path):
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
            log_event("analysis.llm_enricher.unavailable", level="WARNING",
                      reason=str(exc), static_hints=static_hints)
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

        additional = [h for h in data.get("additional_hints", []) if isinstance(h, str)]
        remove     = set(h for h in data.get("remove_hints", []) if isinstance(h, str))
        confidence = float(data.get("confidence", 0.8))
        reasoning  = data.get("reasoning", "")

        merged = [h for h in static_hints if h not in remove]
        for h in additional:
            if h not in merged:
                merged.append(h)

        log_event(
            "analysis.llm_enricher.done",
            added=additional, removed=list(remove),
            confidence=confidence, reasoning=reasoning,
        )
        return EnrichmentResult(hints=merged, confidence=confidence, llm_used=True,
                                reasoning=reasoning)

    def _should_enrich(
        self, hints: list[str], service_count: int, project_path: Path
    ) -> bool:
        has_compose = (
            (project_path / "docker-compose.yml").exists()
            or (project_path / "docker-compose.yaml").exists()
        )
        hint_set = set(hints)
        sparse = hint_set <= {"api-only"}   # only default hint, nothing found
        return (
            service_count >= 2
            or has_compose
            or sparse
            or "multi-service" in hint_set
        )


from dataclasses import dataclass, field

@dataclass
class EnrichmentResult:
    hints: list[str]
    confidence: float
    llm_used: bool
    reasoning: str = ""
```

### Context builder

```python
def _build_context(project_path: Path, static_hints: list[str]) -> str:
    """Builds a compact project context within _CONTEXT_BUDGET characters."""
    parts: list[str] = [
        f"Static analysis detected these signals: {', '.join(static_hints) or 'none'}",
        "",
        "Project file snippets follow. Identify any missed signals.",
        "",
    ]

    # Priority order — most signal-rich files first
    candidates = [
        ("docker-compose.yml",            80),
        ("docker-compose.yaml",           80),
        (".env.example",                  60),
        (".env.sample",                   60),
        ("application.yml",               50),
        ("application.properties",        50),
        ("requirements.txt",              60),
        ("package.json",                  40),   # only deps section extracted
        ("pom.xml",                       30),   # only deps section extracted
        ("go.mod",                        40),
        ("README.md",                     30),
        ("Dockerfile",                    25),
        ("docker-compose.override.yml",   40),
    ]

    budget = _CONTEXT_BUDGET - len("\n".join(parts))

    for filename, max_lines in candidates:
        path = project_path / filename
        if not path.exists():
            # Search one level deep (covers multi-module layouts)
            matches = list(project_path.glob(f"*/{filename}"))
            if matches:
                path = matches[0]
            else:
                continue

        text = _read_file_snippet(path, filename, max_lines)
        if len(text) > budget:
            text = text[:budget] + "\n...[truncated]"
        parts.append(f"=== {filename} ===")
        parts.append(text)
        budget -= len(text)
        if budget <= 200:
            break

    return "\n".join(parts)


def _read_file_snippet(path: Path, filename: str, max_lines: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    # For package.json and pom.xml, extract only the dependencies section
    if filename == "package.json":
        import json as _json
        try:
            data = _json.loads(text)
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            return "dependencies: " + ", ".join(sorted(deps.keys())[:80])
        except Exception:
            pass

    if filename == "pom.xml":
        import re
        # Extract artifact IDs from <artifactId> tags (proxy for dep names)
        ids = re.findall(r"<artifactId>([^<]+)</artifactId>", text)
        return "artifactIds: " + ", ".join(ids[:60])

    lines = text.splitlines()[:max_lines]
    return "\n".join(lines)
```

### Integration into `analysis_node`

**File**: `src/aetherdeploy/agent/nodes/analysis.py`

```python
async def analysis_node(state: AetherState) -> dict:
    emit = _emitter_var.get()
    project_path = state.get("project_path") or "."
    path = Path(project_path)

    emit({"type": "progress", "step": "analysis_scan", "percentage": 18})
    emit({"type": "message", "role": "assistant", "content": "Scanning project files..."})

    # Pass 1: static analysis (sync, fast)
    topology = _detector.detect_topology(path)

    # Pass 2: LLM enrichment (async, gated)
    emit({"type": "progress", "step": "analysis_llm", "percentage": 25})
    enricher = LLMAnalysisEnricher()
    enrichment = await enricher.enrich(
        static_hints=topology.all_hints,
        service_count=topology.service_count,
        project_path=path,
    )

    if enrichment.llm_used:
        emit({
            "type": "message",
            "role": "assistant",
            "content": f"LLM analysis: {enrichment.reasoning}" if enrichment.reasoning
                       else "LLM analysis complete.",
        })
        # Update topology with enriched hints
        topology = _rebuild_topology_with_hints(topology, enrichment.hints)

    emit({"type": "progress", "step": "analysis_detect_stack", "percentage": 30})
    analysis = topology.as_project_analysis()

    emit({
        "type": "analysis_complete",
        "profile": topology.profile,
        "service_count": topology.service_count,
        "services": [...],
        "all_hints": topology.all_hints,
        "llm_used": enrichment.llm_used,
        "confidence": enrichment.confidence,
    })

    return {
        "project_analysis": analysis,
        "application_topology": topology,
        "current_step": "analysis_done",
        "messages": [...],
    }
```

`_rebuild_topology_with_hints(topology, new_hints)` creates a new `ApplicationTopology`
with `all_hints` replaced, and re-runs `ProfileScorer().score()` so the profile reflects
the enriched signals. It is a pure function — no side effects.

### Token budget vs LLM context window

**File**: `src/aetherdeploy/config.py` — add:

```python
llm_context_window: int = 4096   # tokens; override for models with larger context
```

The `_CONTEXT_BUDGET` in `llm_enricher.py` adapts:

```python
# At runtime, before building context:
config = get_config()
# Rough: budget_chars = min(8000, config.llm_context_window * 3)
# 3 chars/token conservative estimate; leaves room for system prompt + response
_budget = min(_CONTEXT_BUDGET, config.llm_context_window * 3 // 2)
```

For Ollama/Gemma3 (4096 token window): budget ≈ 6000 chars → compact snippets only.
For Anthropic Claude (200K window): budget ≈ 8000 chars → more file content shown.

### What the LLM sees for each fixture project

**`sample_spring_project`** (static hints already complete — LLM skipped for SMALL):
```
Static analysis detected: has-database, has-migrations, api-only
[LLM not invoked — single service, no compose, rich hints]
```

**`BookStoreApp`** (compose present, 8+ services — LLM always runs):
```
Static analysis detected: has-database, has-gateway, has-service-discovery,
  has-tracing, has-metrics, has-migrations, is-multi-module, api-only

=== docker-compose.yml ===
version: "3.4"
services:
  bookstore-mysql-db: ...
  bookstore-consul-discovery: ...
  bookstore-zuul-api-gateway-server: ...
  bookstore-account-service: ...
  ...

=== bookstore-payment-service/pom.xml ===
artifactIds: stripe-java, spring-boot-starter-web, ...

=== .env.example ===  (if exists)
STRIPE_API_KEY=...
```

LLM response:
```json
{
  "additional_hints": ["has-payment"],
  "remove_hints": [],
  "confidence": 0.95,
  "reasoning": "stripe-java dependency in payment service confirms payment processing"
}
```

The `has-payment` signal upgrades the profile to ENTERPRISE and triggers WAF + isolated
subnet in the infrastructure proposal.

### What this enables that pure static analysis cannot

| Scenario | Static result | LLM-enriched result |
|---|---|---|
| Stripe env var in `.env.example`, no SDK in deps | `api-only` | `has-payment` |
| `docker-compose.yml` has Redis, but no dep in source | `api-only` | `has-cache` |
| Go service calls external OAuth2 without a library | `api-only` | `has-service-discovery` (inferred) |
| README says "Kafka-backed event bus" | nothing | `has-queue` |
| Multi-module Maven with `<modules>` in root pom | single service | `is-multi-module` |
| `.env.example` has `INFLUXDB_URL=` | nothing | `has-metrics` |

---

## Complexity Profile — The Central Decision

```python
class ComplexityProfile(str, Enum):
    NANO       = "nano"        # static site, no backend
    MICRO      = "micro"       # single stateless service, no DB
    SMALL      = "small"       # single service + DB (+ optional migrations)
    STANDARD   = "standard"    # 2–4 services OR single service with cache/queue/worker
    LARGE      = "large"       # 5–8 services with service discovery and/or tracing
    ENTERPRISE = "enterprise"  # 8+ services, payment processing, or compliance signals
```

### Scoring algorithm

Computed from `architecture_hints` (existing strings + new ones above).
No Java/Python/Node-specific knowledge here — only signal strings.

```python
def score(hints: list[str], service_count: int) -> ComplexityProfile:
    hint_set = set(hints)

    # Hard overrides — always ENTERPRISE regardless of service count
    if "has-payment" in hint_set:
        return ENTERPRISE
    if service_count >= 8:
        return ENTERPRISE

    # Service-count gates
    if service_count >= 5 or "has-service-discovery" in hint_set:
        return LARGE
    if service_count >= 3:
        return STANDARD

    # Single-service classification
    has_db    = "has-database" in hint_set
    has_cache = "has-cache" in hint_set
    has_queue = "has-queue" in hint_set
    has_worker = "has-worker" in hint_set
    is_static = "is-frontend-only" in hint_set

    if is_static and service_count <= 1:
        return NANO
    if not has_db and not has_cache and not has_queue and not has_worker:
        return MICRO
    if has_db and not has_cache and not has_queue and not has_worker:
        return SMALL
    return STANDARD

    # 2 services
    return STANDARD
```

### Profile → infrastructure tier (all three providers)

| Profile | AWS | GCP | Azure |
|---|---|---|---|
| NANO | S3 + CloudFront | Cloud Storage + Cloud CDN | Blob Storage + CDN |
| MICRO | App Runner | Cloud Run | Container Apps |
| SMALL | App Runner + RDS Serverless v2 | Cloud Run + Cloud SQL (serverless) | Container Apps + Flexible Server |
| STANDARD | ECS Fargate + RDS + ALB + VPC | Cloud Run + Cloud SQL + Load Balancer | Container Apps + Flexible Server + App Gateway |
| LARGE | ECS Fargate multi-service + Service Connect | GKE Autopilot | AKS + Container Apps |
| ENTERPRISE | ECS Fargate multi-service + WAF + isolated subnets | GKE Autopilot + Armor | AKS + WAF + Private Endpoints |

---

## Phase A — Extended Language Analyzers

**Goal**: enrich `architecture_hints` with the new signal vocabulary.
No new files. No new classes called from outside. Pure extension of existing analyzers.

### A1: Extend `JavaAnalyzer`

**File**: `src/aetherdeploy/analyzers/java.py`

In `analyze()`, after the existing hint detection, add:

```python
# Spring Cloud signals (all inside JavaAnalyzer — not a separate class)
text_lower = set(deps)  # deps are already extracted artifact IDs
if text_lower & {"spring-cloud-starter-gateway", "spring-cloud-netflix-zuul"}:
    hints.append("has-gateway")
if text_lower & {"spring-cloud-starter-netflix-eureka-client",
                  "spring-cloud-starter-consul-discovery"}:
    hints.append("has-service-discovery")
if text_lower & {"spring-cloud-starter-zipkin", "spring-cloud-sleuth"}:
    hints.append("has-tracing")
if text_lower & {"stripe-java", "braintree", "paypal-rest-sdk"}:
    hints.append("has-payment")
if text_lower & {"flyway-core", "liquibase-core"}:
    hints.append("has-migrations")

# Multi-module Maven detection
if pom.exists() and "<modules>" in self._read_text(pom):
    hints.append("is-multi-module")
```

Also add multi-module scanning: when `is-multi-module` is detected, walk the declared
`<module>` entries and analyze each sub-directory, collecting additional `LanguageAnalysis`
objects. The primary `LanguageAnalysis` returned is the parent's, with `dependencies`
and `architecture_hints` merged from all sub-modules.

### A2: Extend `NodeAnalyzer`

**File**: `src/aetherdeploy/analyzers/node.py`

```python
if any(k in deps for k in ("bull", "bullmq", "agenda", "bee-queue", "kafkajs", "amqplib")):
    hints.append("has-queue")
if any(k in deps for k in ("stripe", "paypal-rest-sdk", "braintree")):
    hints.append("has-payment")
if any(k in deps for k in ("@opentelemetry/sdk-node", "zipkin", "jaeger-client")):
    hints.append("has-tracing")
if "next" in deps or "nuxt" in deps or "remix" in deps:
    # Fullstack already set; additionally check if it is ONLY frontend
    pass  # "is-frontend-only" only if NO server deps at all
if not any(k in deps for k in ("express", "fastify", "koa", "nestjs", "@hapi/hapi",
                                "next", "nuxt", "remix")):
    if (tmp_path / "index.html").exists() or (tmp_path / "public").is_dir():
        hints.append("is-frontend-only")
```

### A3: Extend `PythonAnalyzer`

**File**: `src/aetherdeploy/analyzers/python.py`

```python
if any(k in deps for k in ("rq", "dramatiq", "huey", "kafka-python", "pika", "kombu")):
    hints.append("has-queue")
if any(k in deps for k in ("stripe", "paypalrestsdk", "braintree")):
    hints.append("has-payment")
if any(k in deps for k in ("opentelemetry-sdk", "opentelemetry-exporter-zipkin")):
    hints.append("has-tracing")
if "alembic" in deps or "django" in deps:
    hints.append("has-migrations")
```

### A4: Extend `GoAnalyzer`

**File**: `src/aetherdeploy/analyzers/go.py`

```python
if any("hibiken/asynq" in d or "watermill" in d for d in deps):
    hints.append("has-queue")
if any("stripe-go" in d for d in deps):
    hints.append("has-payment")
if any("go.opentelemetry.io/otel" in d for d in deps):
    hints.append("has-tracing")
```

### A5: Multi-service scanner in `ProjectDetector`

**File**: `src/aetherdeploy/analyzers/detector.py`

Add a new method alongside the existing `detect()`:

```python
def detect_topology(self, project_path: Path) -> "ApplicationTopology":
    """
    Produces a full ApplicationTopology by:
    1. Parsing docker-compose.yml if present (source of truth for service count)
    2. Scanning subdirectories for service roots (depth ≤ 3)
    3. Running per-language analyzers on each service root
    4. Scoring the ComplexityProfile from merged hints + service count
    5. Building a ProjectAnalysis (backwards compat) from the merged results
    """
```

The existing `detect(path)` is NOT modified. It remains the entry point for the
single-service fast path and for all existing tests.

`detect_topology()` calls `detect()` as its inner engine for each service root,
then merges the results. It does NOT call any language-specific class directly.

---

## Phase B — New Models (additive only)

**File**: `src/aetherdeploy/models.py`

Add new dataclasses **below** the existing ones. Never modify existing dataclasses in a
breaking way (existing fields: add only `field(default_factory=...)` defaults).

```python
class ComplexityProfile(str, Enum):
    NANO = "nano"
    MICRO = "micro"
    SMALL = "small"
    STANDARD = "standard"
    LARGE = "large"
    ENTERPRISE = "enterprise"


@dataclass
class ServiceSummary:
    """Lightweight per-service record inside ApplicationTopology."""
    name: str
    path_relative: str      # relative to project root — never absolute (serializable)
    language: str
    framework: str | None
    hints: list[str]        # architecture_hints from that service's analyzer
    ports: list[int]
    has_dockerfile: bool


@dataclass
class ApplicationTopology:
    """
    Full project topology. Produced by ProjectDetector.detect_topology().
    Stored in AetherState.application_topology (new optional field).
    The existing AetherState.project_analysis remains populated for all
    existing code paths that read it.
    """
    project_name: str
    profile: str                       # ComplexityProfile value (string for serialization)
    service_count: int
    services: list[ServiceSummary]
    all_hints: list[str]               # union of hints from all services
    has_compose: bool
    compose_service_count: int         # 0 if no compose file


@dataclass
class ProposalMetadata:
    """Attached to ArchitectureProposal when topology is available."""
    profile: str                       # ComplexityProfile value
    confidence: float                  # 0.0–1.0
    deploy_time_estimate: str          # "3–5 min" | "12–18 min"
    cost_low_usd: int
    cost_high_usd: int
    unknowns: list[str] = field(default_factory=list)
```

**IMPORTANT**: `ApplicationTopology` and `ProposalMetadata` use `str` for enum fields,
not `ComplexityProfile`, because LangGraph's `JsonPlusSerializer` handles `str` natively
but custom Enum types require explicit registration (which is fragile across versions).

### B2: Extend `ArchitectureProposal` (backwards compatible)

Add two new optional fields to the existing dataclass with safe defaults:

```python
@dataclass
class ArchitectureProposal:
    # --- existing fields, unchanged ---
    provider: str
    region: str
    services: list[ServiceRecommendation] = field(default_factory=list)
    total_estimated_cost: str = "~$0/mes"
    security_notes: list[str] = field(default_factory=list)
    scalability_notes: list[str] = field(default_factory=list)
    environments: dict[str, EnvironmentConfig] = field(default_factory=dict)
    # --- new optional fields ---
    metadata: ProposalMetadata | None = None          # None when topology unavailable
    cloud_mappings: dict[str, str] = field(default_factory=dict)  # hint → cloud service name
```

Because `ProposalMetadata` is added with `None` default, all existing code that constructs
`ArchitectureProposal(provider=..., region=...)` continues to work without changes.

### B3: Register new types in `graph.py`

**File**: `src/aetherdeploy/agent/graph.py`

```python
from ..models import (
    ApplicationTopology,   # NEW
    ArchitectureProposal,
    DeploymentResult,
    EnvironmentConfig,
    LanguageAnalysis,
    ProjectAnalysis,
    ProposalMetadata,      # NEW
    ServiceRecommendation,
    ServiceSummary,        # NEW
)

_SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=[
        ApplicationTopology,   # NEW
        ArchitectureProposal,
        DeploymentResult,
        EnvironmentConfig,
        LanguageAnalysis,
        ProjectAnalysis,
        ProposalMetadata,      # NEW
        ServiceRecommendation,
        ServiceSummary,        # NEW
    ],
)
```

### B4: Extend `AetherState`

**File**: `src/aetherdeploy/agent/state.py`

```python
class AetherState(TypedDict, total=False):
    # --- all existing fields, unchanged ---
    user_message: str
    project_path: str | None
    # ... (all existing fields) ...

    # NEW — optional; None when single-service fast path is used
    application_topology: ApplicationTopology | None
```

This is additive. `total=False` means all fields are optional already,
so adding a new field does not break any existing code.

---

## Phase C — Profile-Gated Analysis Node

**Goal**: the analysis node produces both `ProjectAnalysis` (backwards compat) AND
`ApplicationTopology` (new). No other node changes in this phase.

### C1: Update `analysis_node`

**File**: `src/aetherdeploy/agent/nodes/analysis.py`

```python
async def analysis_node(state: AetherState) -> dict:
    emit = _emitter_var.get()
    project_path = state.get("project_path") or "."

    emit({"type": "progress", "step": "analysis_scan", "percentage": 18})
    emit({"type": "message", "role": "assistant", "content": "Scanning project files..."})

    path = Path(project_path)
    topology = _detector.detect_topology(path)      # NEW — full topology
    analysis = topology.as_project_analysis()        # derives ProjectAnalysis from topology

    emit({"type": "progress", "step": "analysis_detect_stack", "percentage": 30})

    framework_str = f" + {', '.join(analysis.frameworks)}" if analysis.frameworks else ""
    summary = f"{analysis.primary_language}{framework_str} ({analysis.architecture})"

    # Emit the new analysis_complete event for the frontend
    emit({
        "type": "analysis_complete",
        "profile": topology.profile,
        "service_count": topology.service_count,
        "services": [
            {"name": s.name, "language": s.language, "framework": s.framework,
             "hints": s.hints, "ports": s.ports}
            for s in topology.services
        ],
        "all_hints": topology.all_hints,
        "has_compose": topology.has_compose,
    })

    return {
        "project_analysis": analysis,           # backwards compat — unchanged field
        "application_topology": topology,        # new field
        "current_step": "analysis_done",
        "messages": [
            *state.get("messages", []),
            {"role": "assistant", "content": f"Stack detected: **{summary}** (profile: {topology.profile})"},
        ],
    }
```

Note: `topology.as_project_analysis()` is a **method**, not a property. It returns a
fresh `ProjectAnalysis` derived from the topology so it can be serialized independently.
Both `project_analysis` and `application_topology` are stored in state; they do not
reference each other in memory after this point.

### C2: `ApplicationTopology.as_project_analysis()` method

**File**: `src/aetherdeploy/models.py`

```python
def as_project_analysis(self) -> ProjectAnalysis:
    """Derives a backwards-compatible ProjectAnalysis from this topology."""
    primary_lang = self.services[0].language if self.services else "unknown"
    frameworks = list({s.framework for s in self.services if s.framework})
    all_hints = list(set(self.all_hints))
    ports = sorted({p for s in self.services for p in s.ports}) or [8080]

    arch = "microservices" if self.service_count >= 5 else \
           "monolith-with-workers" if "has-worker" in all_hints else \
           "fullstack" if "fullstack" in all_hints else "monolith"

    return ProjectAnalysis(
        primary_language=primary_lang,
        languages=list({s.language for s in self.services}),
        frameworks=frameworks,
        architecture=arch,
        dependencies=all_hints,           # hints as proxy for deps (what providers read)
        exposed_ports=ports,
        has_dockerfile=any(s.has_dockerfile for s in self.services),
        has_tests=False,                  # not tracked in ServiceSummary (not needed by providers)
        infrastructure_hints=all_hints,   # what AWSProvider._select_compute() reads
    )
```

---

## Phase D — Profile-Aware Provider Proposals

### D1: `AWSProvider.recommend_architecture` — backwards compatible extension

**File**: `src/aetherdeploy/providers/aws/provider.py`

```python
def recommend_architecture(
    self,
    analysis: ProjectAnalysis,
    environments: list[str],
    topology: ApplicationTopology | None = None,   # NEW — optional, default None
) -> ArchitectureProposal:
    # Use topology profile if available; otherwise fall back to current behaviour
    if topology is not None:
        return self._propose_for_profile(topology, environments)
    # --- existing code path, 100% unchanged ---
    return self._propose_legacy(analysis, environments)

def _propose_legacy(self, analysis, environments):
    # Renamed from the current body of recommend_architecture — no changes
    ...

def _propose_for_profile(self, topology, environments):
    profile = topology.profile
    if profile == "nano":
        return self._propose_static_site(topology, environments)
    if profile == "micro":
        return self._propose_app_runner_stateless(topology, environments)
    if profile == "small":
        return self._propose_app_runner_with_db(topology, environments)
    if profile == "standard":
        return self._propose_ecs_standard(topology, environments)
    if profile in ("large", "enterprise"):
        return self._propose_ecs_multi_service(topology, environments)
    return self._propose_legacy(topology.as_project_analysis(), environments)
```

All 7 existing tests call `recommend_architecture(analysis, environments)` — `topology`
defaults to `None` — so they all hit the unchanged `_propose_legacy` path. Zero regressions.

### D2: GCP and Azure equivalents

**File**: `src/aetherdeploy/providers/gcp/provider.py`  
**File**: `src/aetherdeploy/providers/azure/provider.py`

Same pattern: add `topology: ApplicationTopology | None = None` parameter,
dispatch to profile-specific methods, keep the existing body as `_propose_legacy`.

GCP profile dispatch:
- `nano` → Cloud Storage + Cloud CDN
- `micro` → Cloud Run (no SQL)
- `small` → Cloud Run + Cloud SQL Serverless
- `standard` → Cloud Run + Cloud SQL + Load Balancer
- `large/enterprise` → GKE Autopilot

Azure profile dispatch:
- `nano` → Azure Blob Storage + CDN
- `micro` → Container Apps (no DB)
- `small` → Container Apps + Flexible Server
- `standard` → Container Apps + Flexible Server + App Gateway
- `large/enterprise` → AKS + WAF

### D3: `proposal_node` passes topology to provider

**File**: `src/aetherdeploy/agent/nodes/proposal.py`

```python
async def proposal_node(state: AetherState) -> dict:
    topology = state.get("application_topology")     # may be None
    analysis = state.get("project_analysis")

    provider = get_provider(state.get("preferred_provider") or "aws")
    # topology is passed if available; provider falls back to legacy if None
    proposal = provider.recommend_architecture(analysis, envs, topology=topology)
    ...
```

The LLM enrichment (`_enrich_with_llm`) now receives topology too, so it can give a
topology-aware rationale. The `architecture_review_user_prompt()` is extended with
optional topology fields — all with defaults so existing call sites still compile.

---

## Phase E — Composable Terraform Templates

### E1: Template layout (tiered)

```
src/aetherdeploy/terraform/templates/aws/
├── tiers/
│   ├── nano/    main.tf.j2    — S3 + CloudFront
│   ├── micro/   main.tf.j2    — App Runner only
│   ├── small/   main.tf.j2    — App Runner + RDS Serverless v2
│   └── standard/main.tf.j2   — ECS + RDS + ALB + VPC (current template, moved here)
├── partials/                  — used by large/enterprise (included via Jinja2 include)
│   ├── ecs_service.tf.j2      — one ECS task+service, rendered per service
│   ├── ecr_repo.tf.j2
│   ├── alb_rule.tf.j2
│   ├── security_group.tf.j2
│   ├── iam_task_role.tf.j2
│   └── ...
└── main.tf.j2                 — KEPT AS-IS for backwards compat (standard profile)
```

The existing `main.tf.j2` is moved to `tiers/standard/main.tf.j2` and symlinked/aliased
as `main.tf.j2` to avoid breaking any code that currently references it. The generator
is updated to use `tiers/{profile}/main.tf.j2` when a profile is known.

### E2: Generator routing

**File**: `src/aetherdeploy/terraform/generator.py`

```python
def generate(self, proposal, project_name, environment="prod", backend_config=None):
    profile = proposal.metadata.profile if proposal.metadata else "standard"
    tier = _profile_to_tier(profile)   # "nano"|"micro"|"small"|"standard"|"large_enterprise"

    if tier != "large_enterprise":
        template_name = f"{proposal.provider}/tiers/{tier}/main.tf.j2"
        try:
            template = self._env.get_template(template_name)
        except jinja2.TemplateNotFound:
            # Graceful fallback: use standard template for unknown tiers
            template = self._env.get_template(f"{proposal.provider}/main.tf.j2")
        return {"main.tf": template.render(**ctx), ".terraform-version": "1.9.0\n"}

    return self._generate_large_enterprise(proposal, project_name, environment, backend_config)
```

The fallback to `main.tf.j2` (existing template) ensures that if a tier template doesn't
exist yet, the generator still works rather than crashing.

### E3: SMALL template for `sample_spring_project`

**New file**: `src/aetherdeploy/terraform/templates/aws/tiers/small/main.tf.j2`

```hcl
# ============================================================
# Generated by AetherDeploy — {{ generated_at }}
# Project: {{ project_name }} | Profile: small | Env: {{ environment }}
# Compute: AWS App Runner | Database: RDS Aurora Serverless v2
# ============================================================

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.0" }
    random = { source = "hashicorp/random", version = "~> 3.0" }
  }
  {% if backend %}
  backend "s3" {
    bucket         = "{{ backend.bucket }}"
    key            = "{{ backend.key }}"
    region         = "{{ backend.region }}"
    encrypt        = true
    dynamodb_table = "{{ backend.dynamodb_table }}"
  }
  {% endif %}
}

provider "aws" {
  region = var.aws_region
  default_tags { tags = { Project = var.project_name, Environment = var.environment } }
}

variable "aws_region"      { default = "{{ proposal.region }}" }
variable "project_name"    { default = "{{ project_name }}" }
variable "environment"     { default = "{{ environment }}" }
variable "container_image" { default = "{{ project_name }}:latest" }
variable "container_port"  { type = number; default = {{ exposed_port }} }

# App Runner — no VPC, NAT, or ALB needed
resource "aws_apprunner_service" "app" {
  service_name = "${var.project_name}-${var.environment}"
  source_configuration {
    image_repository {
      image_identifier      = var.container_image
      image_repository_type = "ECR"
      image_configuration {
        port = tostring(var.container_port)
        {% if has_service("database") %}
        runtime_environment_secrets = {
          DB_HOST     = aws_secretsmanager_secret_version.db_host.arn
          DB_NAME     = aws_secretsmanager_secret_version.db_name.arn
          DB_USER     = aws_secretsmanager_secret_version.db_user.arn
          DB_PASSWORD = aws_secretsmanager_secret_version.db_password.arn
        }
        {% endif %}
      }
    }
    auto_deployments_enabled = false
  }
  instance_configuration {
    cpu    = "0.25 vCPU"
    memory = "0.5 GB"
  }
}

{% if has_service("database") %}
resource "random_password" "db" { length = 32; special = false }

resource "aws_rds_cluster" "main" {
  cluster_identifier   = "${var.project_name}-${var.environment}"
  engine               = "aurora-postgresql"
  engine_mode          = "provisioned"
  engine_version       = "15.4"
  database_name        = replace(var.project_name, "-", "_")
  master_username      = "app"
  master_password      = random_password.db.result
  skip_final_snapshot  = var.environment != "prod"
  serverlessv2_scaling_configuration {
    min_capacity = 0.5
    max_capacity = var.environment == "prod" ? 8 : 2
  }
}

resource "aws_rds_cluster_instance" "main" {
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version
}

# Secrets Manager — one secret per DB connection parameter
resource "aws_secretsmanager_secret" "db_host" {
  name = "${var.project_name}/${var.environment}/db-host"
}
resource "aws_secretsmanager_secret_version" "db_host" {
  secret_id     = aws_secretsmanager_secret.db_host.id
  secret_string = aws_rds_cluster.main.endpoint
}
# (db_name, db_user, db_password follow the same pattern)
{% endif %}

output "app_url" {
  value = "https://${aws_apprunner_service.app.service_url}"
}
{% if has_service("database") %}
output "db_endpoint" {
  value     = aws_rds_cluster.main.endpoint
  sensitive = true
}
{% endif %}
```

---

## Phase F — `analyze` CLI Command

**Goal**: pure read-only command. No graph, no LangGraph, no credentials, no Terraform.
Shows the user what the agent understands about the project.

### F1: Add `analyze` to CLI

**File**: `src/aetherdeploy/cli.py`

```python
@app.command()
def analyze(
    project: str = typer.Option(".", "--project", "-p", help="Path to the project"),
    output: str = typer.Option("text", "--output", "-o", help="Output format: text | json"),
    llm: bool = typer.Option(False, "--llm", help="Run LLM enrichment pass (requires LLM backend)"),
):
    """Analyse a project and show what AetherDeploy understands. No deployment, no credentials."""
    asyncio.run(_analyze_async(project, output, llm))


async def _analyze_async(project: str, output: str, use_llm: bool) -> None:
    from .analyzers.detector import ProjectDetector
    from .analyzers.llm_enricher import LLMAnalysisEnricher
    import json as _json

    path = Path(project).resolve()
    topology = ProjectDetector().detect_topology(path)

    # LLM enrichment: always run if --llm flag set; also run if gating triggers it
    if use_llm or LLMAnalysisEnricher()._should_enrich(
        topology.all_hints, topology.service_count, path
    ):
        enrichment = await LLMAnalysisEnricher().enrich(
            topology.all_hints, topology.service_count, path
        )
        if enrichment.llm_used:
            topology = _rebuild_topology_with_hints(topology, enrichment.hints)
            topology._llm_reasoning = enrichment.reasoning  # transient, for display only

    if output == "json":
        data = {
            "project_name": topology.project_name,
            "profile": topology.profile,
            "service_count": topology.service_count,
            "services": [
                {"name": s.name, "language": s.language, "framework": s.framework,
                 "hints": s.hints, "ports": s.ports}
                for s in topology.services
            ],
            "all_hints": topology.all_hints,
            "has_compose": topology.has_compose,
        }
        console.print(_json.dumps(data, indent=2))
        return

    # Text output
    console.print(f"\n[bold]Project Analysis — {topology.project_name}[/bold]")
    console.rule()
    console.print(f"Profile      [cyan]{topology.profile.upper()}[/cyan]")
    console.print(f"Services     {topology.service_count}")
    for svc in topology.services:
        lang = f"{svc.language}" + (f" ({svc.framework})" if svc.framework else "")
        ports_str = ", ".join(str(p) for p in svc.ports)
        console.print(f"  ├─ {svc.name:<40} {lang:<30} ports: {ports_str}")

    notable = [h for h in topology.all_hints
               if h not in ("api-only",) and not h.startswith("api")]
    if notable:
        console.print(f"Signals      {', '.join(notable)}")

    console.print()
    provider_name = get_config().default_provider.upper()
    console.print(
        f"Run [bold]aetherdeploy deploy[/bold] to propose {provider_name} infrastructure, "
        "or [bold]aetherdeploy deploy --dry-run[/bold] to preview Terraform."
    )
```

### F2: Expected output for each fixture

**`sample_spring_project`**:
```
Profile      SMALL
Services     1
  ├─ sample_spring_project    Java 21 (Spring Boot)    ports: 8080
Signals      has-database, has-migrations
```

**`BookStoreApp-Distributed-Application`**:
```
Profile      ENTERPRISE
Services     8
  ├─ bookstore-api-gateway-service    Java (Spring Boot)    ports: 8765
  ├─ bookstore-account-service        Java (Spring Boot)    ports: 4001
  ├─ bookstore-billing-service        Java (Spring Boot)    ports: 5001
  ├─ bookstore-catalog-service        Java (Spring Boot)    ports: 6001
  ├─ bookstore-order-service          Java (Spring Boot)    ports: 7001
  ├─ bookstore-payment-service        Java (Spring Boot)    ports: 8001
  ├─ bookstore-frontend-react-app     Node.js (React)       ports: 3000
  └─ ...
Signals      has-database, has-gateway, has-service-discovery, has-tracing,
             has-metrics, has-payment, has-migrations, is-multi-module
```

**`sample_node_project`**:
```
Profile      SMALL  (or MICRO if no DB dep detected)
Services     1
  ├─ sample_node_project    Node.js (Express)    ports: 3000
```

---

## Phase G — LLM as Reasoning Validator

### G1: Topology-aware prompt

**File**: `src/aetherdeploy/agent/prompts.py`

```python
TOPOLOGY_REASONING_SYSTEM = """\
You are AetherDeploy, a pragmatic cloud architect.

RULE: match infrastructure complexity to application complexity.
A single CRUD API must never get a VPC + NAT + Service Connect.
A distributed system with service discovery needs full networking.

You are given an ApplicationTopology (profile + service signals) and a proposed
infrastructure tier. Validate the tier and suggest adjustments ONLY if clearly wrong.

You may suggest ±1 tier level. You may NOT override a tier based on speculation.

If "has-payment" is in signals, the ENTERPRISE tier is correct — do not downgrade it.

Return JSON: {
  "tier_correct": bool,
  "tier_adjustment": "upgrade" | "downgrade" | null,
  "adjustment_reason": str | null,
  "security_concerns": list[str],
  "cost_optimizations": list[str],
  "unknowns": list[str]
}
"""

def topology_reasoning_user_prompt(topology, proposal) -> str:
    return (
        f"Profile detected: {topology.profile}\n"
        f"Service count: {topology.service_count}\n"
        f"Signals: {', '.join(topology.all_hints)}\n"
        f"Proposed tier: {proposal.metadata.profile if proposal.metadata else 'unknown'}\n"
        f"Provider: {proposal.provider}, region: {proposal.region}\n"
        f"Services proposed: {', '.join(s.service_name for s in proposal.services)}\n"
        "Validate the tier and return JSON."
    )
```

### G2: Extend `_enrich_with_llm` to use topology

**File**: `src/aetherdeploy/agent/nodes/proposal.py`

```python
async def _enrich_with_llm(proposal, state, emit):
    topology = state.get("application_topology")
    if topology:
        # Use topology-aware prompt when available
        system = TOPOLOGY_REASONING_SYSTEM
        user_msg = topology_reasoning_user_prompt(topology, proposal)
    else:
        # Existing behaviour — no topology available
        system = ARCHITECTURE_REVIEW_SYSTEM
        user_msg = architecture_review_user_prompt(...)
    ...
```

### G3: Confirmation node — NL modification via LLM

**File**: `src/aetherdeploy/agent/nodes/confirmation.py`

When `user_modifications` is non-empty:
- Currently returns `"needs_revision"` with a simple echo message.
- Extended: passes the modification text + current proposal to the LLM.
- LLM returns `{explanation, security_concerns, cost_optimizations}`.
- The returned message tells the user what the agent understood from the request.
- The `proposal_node` (re-run after `needs_revision`) picks up the modification from
  `user_modifications` in state and passes it to the provider's proposal logic.

---

## Phase H — Testing Strategy

### H0: LLM enricher tests

**New file**: `tests/unit/test_llm_enricher.py`

The enricher is tested with a mock LLM backend so tests run without a live model.

```python
import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from aetherdeploy.analyzers.llm_enricher import LLMAnalysisEnricher, EnrichmentResult


class MockBackend:
    async def complete_json(self, messages, system=""):
        return {
            "additional_hints": ["has-payment"],
            "remove_hints": [],
            "confidence": 0.95,
            "reasoning": "stripe-java found in pom.xml",
        }


@pytest.mark.asyncio
async def test_enricher_adds_missed_signal(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  db:\n    image: mysql\n")
    with patch("aetherdeploy.analyzers.llm_enricher.LLMBackendFactory.create",
               return_value=MockBackend()):
        enricher = LLMAnalysisEnricher()
        result = await enricher.enrich(["api-only"], service_count=1, project_path=tmp_path)
    assert "has-payment" in result.hints
    assert result.llm_used is True
    assert result.confidence == 0.95


@pytest.mark.asyncio
async def test_enricher_skipped_for_small_project(tmp_path):
    # No compose, no multi-service, rich static hints → skip LLM
    (tmp_path / "pom.xml").write_text("<project/>")
    hints = ["has-database", "has-migrations", "api-only"]
    enricher = LLMAnalysisEnricher()
    # _should_enrich returns False — no LLM call made
    result = await enricher.enrich(hints, service_count=1, project_path=tmp_path)
    assert result.llm_used is False
    assert result.hints == hints


@pytest.mark.asyncio
async def test_enricher_fallback_on_llm_error(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  web:\n    build: .\n")
    with patch("aetherdeploy.analyzers.llm_enricher.LLMBackendFactory.create",
               side_effect=Exception("LLM offline")):
        enricher = LLMAnalysisEnricher()
        result = await enricher.enrich(["api-only"], service_count=2, project_path=tmp_path)
    # Must return original hints unchanged — never crash
    assert result.hints == ["api-only"]
    assert result.llm_used is False


@pytest.mark.asyncio
async def test_enricher_remove_wrong_hint(tmp_path):
    class FixBackend:
        async def complete_json(self, messages, system=""):
            return {"additional_hints": [], "remove_hints": ["api-only"], "confidence": 0.8}
    (tmp_path / "docker-compose.yml").write_text("services:\n  db:\n    image: postgres\n")
    with patch("aetherdeploy.analyzers.llm_enricher.LLMBackendFactory.create",
               return_value=FixBackend()):
        enricher = LLMAnalysisEnricher()
        result = await enricher.enrich(["api-only", "has-database"], 1, tmp_path)
    assert "api-only" not in result.hints
    assert "has-database" in result.hints


@pytest.mark.asyncio
async def test_context_builder_caps_budget(tmp_path):
    # Write a very large docker-compose to test budget enforcement
    large_compose = "services:\n" + "  svc{}:\n    image: nginx\n".join(str(i) for i in range(200))
    (tmp_path / "docker-compose.yml").write_text(large_compose)
    from aetherdeploy.analyzers.llm_enricher import _build_context
    ctx = _build_context(tmp_path, ["api-only"])
    # Must never exceed budget
    assert len(ctx) <= 8_500   # small tolerance over nominal budget


def test_gating_compose_triggers_llm(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  db:\n    image: mysql\n")
    enricher = LLMAnalysisEnricher()
    assert enricher._should_enrich(["has-database"], service_count=1, project_path=tmp_path)


def test_gating_sparse_hints_triggers_llm(tmp_path):
    enricher = LLMAnalysisEnricher()
    assert enricher._should_enrich(["api-only"], service_count=1, project_path=tmp_path)


def test_gating_rich_single_service_skips_llm(tmp_path):
    enricher = LLMAnalysisEnricher()
    hints = ["has-database", "has-migrations", "has-cache"]
    assert not enricher._should_enrich(hints, service_count=1, project_path=tmp_path)
```

### H1: Signal extraction tests (per language)

**File**: `tests/unit/test_analyzers.py` — extended

```python
def test_java_detects_spring_cloud_gateway(tmp_path):
    pom = _pom_with_deps(["spring-cloud-starter-gateway"])
    (tmp_path / "pom.xml").write_text(pom)
    result = JavaAnalyzer().analyze(tmp_path)
    assert "has-gateway" in result.architecture_hints

def test_java_detects_stripe(tmp_path):
    pom = _pom_with_deps(["stripe-java"])
    (tmp_path / "pom.xml").write_text(pom)
    result = JavaAnalyzer().analyze(tmp_path)
    assert "has-payment" in result.architecture_hints

def test_node_detects_bull_queue(tmp_path):
    pkg = {"dependencies": {"bull": "^4.0.0", "express": "^4.0.0"}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    result = NodeAnalyzer().analyze(tmp_path)
    assert "has-queue" in result.architecture_hints

def test_python_detects_stripe(tmp_path):
    (tmp_path / "requirements.txt").write_text("stripe>=7.0\nfastapi\n")
    result = PythonAnalyzer().analyze(tmp_path)
    assert "has-payment" in result.architecture_hints
```

### H2: Profile scoring tests

**New file**: `tests/unit/test_profile_scorer.py`

```python
def test_sample_spring_is_small():
    hints = ["has-database", "has-migrations", "api-only"]
    assert score(hints, service_count=1) == "small"

def test_payment_always_enterprise():
    hints = ["has-database", "has-payment"]
    assert score(hints, service_count=1) == "enterprise"

def test_stateless_api_is_micro():
    hints = ["api-only"]
    assert score(hints, service_count=1) == "micro"

def test_multi_service_is_large():
    hints = ["has-database", "has-service-discovery"]
    assert score(hints, service_count=6) == "large"

def test_bookstore_topology_scores_enterprise():
    hints = ["has-database", "has-gateway", "has-service-discovery",
             "has-tracing", "has-payment", "is-multi-module"]
    assert score(hints, service_count=8) == "enterprise"
```

### H3: Backwards compat — all 51 existing tests must still pass

```python
# These must not change — verified by running pytest with no modifications to
# the existing test files

def test_aws_api_only_selects_ecs():
    # topology=None → hits _propose_legacy → same result as before
    proposal = AWSProvider().recommend_architecture(_node_analysis(), ["prod"])
    compute = next(s for s in proposal.services if s.purpose == "compute")
    assert "ECS Fargate" in compute.service_name

def test_aws_proposal_always_has_networking():
    # unchanged
    ...
```

### H4: `analyze` CLI command tests

**New file**: `tests/integration/test_cli_analyze.py`

```python
def test_analyze_sample_spring_json_output():
    result = runner.invoke(app, ["analyze",
        "--project", str(FIXTURES / "sample_spring_project"),
        "--output", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["profile"] == "small"
    assert data["service_count"] == 1
    assert "has-database" in data["all_hints"]

def test_analyze_does_not_write_files(tmp_path):
    shutil.copytree(FIXTURES / "sample_spring_project", tmp_path / "sp")
    runner.invoke(app, ["analyze", "--project", str(tmp_path / "sp")])
    assert not (tmp_path / "sp" / ".aetherdeploy").exists()

def test_analyze_bookstore_is_enterprise():
    result = runner.invoke(app, ["analyze",
        "--project", str(FIXTURES / "BookStoreApp-Distributed-Application"),
        "--output", "json"])
    data = json.loads(result.output)
    assert data["profile"] == "enterprise"
    assert data["service_count"] >= 7
```

---

## Implementation Sequence

Each item is independently mergeable and tested before the next starts.

| Step | What changes | New files | Tests | Safe to ship alone |
|---|---|---|---|---|
| **1** | Extend `JavaAnalyzer` with new signals | — | extend `test_analyzers.py` | ✓ |
| **2** | Extend `NodeAnalyzer` with new signals | — | extend `test_analyzers.py` | ✓ |
| **3** | Extend `PythonAnalyzer` with new signals | — | extend `test_analyzers.py` | ✓ |
| **4** | Extend `GoAnalyzer` with new signals | — | extend `test_analyzers.py` | ✓ |
| **5** | Add new dataclasses to `models.py` | — | no tests yet | ✓ (additive) |
| **6** | `detect_topology()` in `detector.py` | — | `test_profile_scorer.py` | ✓ |
| **7** | `llm_enricher.py` — LLMAnalysisEnricher | `analyzers/llm_enricher.py` | `test_llm_enricher.py` | ✓ |
| **8** | Register types in `graph.py` | — | existing tests still pass | ✓ |
| **9** | Add `application_topology` to `AetherState` | — | existing tests still pass | ✓ |
| **10** | Update `analysis_node` — static + LLM pass | — | existing graph tests pass | ✓ |
| **11** | Add `_propose_for_profile` to `AWSProvider` | — | `test_proposal_tiers.py` | ✓ |
| **12** | Add equivalent to `GCPProvider`, `AzureProvider` | — | extend `test_providers.py` | ✓ |
| **13** | Update `proposal_node` to pass topology | — | existing graph tests pass | ✓ |
| **14** | `small/main.tf.j2` template | `tiers/small/main.tf.j2` | generation test | ✓ |
| **15** | `micro/main.tf.j2`, `nano/main.tf.j2` | — | generation test | ✓ |
| **16** | Generator routing by profile | — | all existing gen tests pass | ✓ |
| **17** | `analyze` CLI command | — | `test_cli_analyze.py` | ✓ |
| **18** | Topology-aware LLM prompt in `proposal_node` | — | — | ✓ |
| **19** | `large/enterprise` multi-file templates | `partials/*.tf.j2` | BookStoreApp gen test | last |

Steps 1–4 can be done in parallel. Steps 5–9 must be sequential (each depends on the prior).
Steps 10–12 can be parallel. Steps 13–16 can be parallel.

---

## What Is Explicitly Out of Scope

These items would make the plan overly complex now and are deferred:

- **EKS / Kubernetes templates** — GKE Autopilot and AKS are listed in the tier table
  as targets but no Terraform templates are written yet. The `large` profile falls back
  to ECS multi-service until the K8s templates are ready.
- **Database-per-service isolation** — the `enterprise` template uses a shared RDS cluster
  with schema-level isolation. Cross-service DB autonomy requires per-service RDS and is
  a separate feature.
- **Multi-region deployment** — out of scope; single-region always.
- **CI/CD pipeline generation** — GitHub Actions / CodePipeline is not part of this plan.
- **`.aetherdeploy/context.json` caching** — useful but not needed for correctness.
  Added when topology detection becomes slow on large repos.

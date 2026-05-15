"""Workload fingerprint — typed representation of what is being deployed.

Defined in MEJORAS.md §11.4 and §13.x. This is the foundation for the
next-generation proposal engine: a structured object that replaces the loose
``infrastructure_hints: list[str]`` with discrete fields the constraint solver
can reason over.

Design notes
------------
* **Additive, not replacing.** Existing ``ProjectAnalysis`` /
  ``ApplicationTopology`` (in ``models.py``) keep working. A new builder
  (``from_topology``) projects today's data into the new schema so call sites
  can migrate incrementally.
* **JSON-safe.** All fields are primitives / dataclasses, no enums; serializes
  cleanly under LangGraph's ``JsonPlusSerializer``.
* **Dynamic fields optional.** Sandbox profiling (§11.2) and telemetry ingestion
  (§11.3) populate ``observed_*`` fields later. ``None`` means "not measured
  yet", which the solver treats as widest plausible range.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .models import ApplicationTopology


StateKind = Literal["stateless", "sticky", "stateful"]
TrafficShape = Literal["steady", "bursty", "scheduled", "unknown"]


# ---------------------------------------------------------------------------
# Static observations (from AST / topology / config)
# ---------------------------------------------------------------------------

@dataclass
class Endpoint:
    """A handler exposed by the application."""
    path: str
    method: str
    framework: str | None = None
    auth: Literal["none", "token", "session", "unknown"] = "unknown"


@dataclass
class BackgroundJob:
    """A scheduled task, queue consumer, or long-running worker."""
    name: str
    kind: Literal["cron", "queue", "stream", "worker"] = "worker"
    schedule: str | None = None  # cron expression when kind == "cron"


@dataclass
class DataAccessPattern:
    """How the app talks to a data store."""
    store: Literal["sql", "nosql", "blob", "cache", "search", "queue"]
    read_write_ratio: float | None = None     # 0.0 = write-only, 1.0 = read-only
    has_transactions: bool = False
    has_joins: bool = False
    avg_query_kb: float | None = None


# ---------------------------------------------------------------------------
# Declarative user input (SLOs, budget, compliance)
# ---------------------------------------------------------------------------

@dataclass
class SLO:
    """Service-level objectives declared by the user (MEJORAS.md §12)."""
    p99_latency_ms: int | None = None
    availability_pct: float | None = None      # e.g. 99.9
    cold_start_ms: int | None = None
    rto_minutes: int | None = None             # recovery time objective
    rpo_minutes: int | None = None             # recovery point objective


@dataclass
class TrafficProfile:
    """Expected traffic shape (declared or inferred from telemetry)."""
    avg_rps: float | None = None
    peak_rps: float | None = None
    daily_pattern: TrafficShape = "unknown"
    geographic_distribution: list[str] = field(default_factory=list)  # ISO country codes


# ---------------------------------------------------------------------------
# Fingerprint — top-level aggregate
# ---------------------------------------------------------------------------

@dataclass
class WorkloadFingerprint:
    """All inputs the proposal engine needs in one typed object.

    Backwards-compatibility: callers can still derive a ``ProjectAnalysis`` via
    :meth:`as_legacy_hints` to feed existing code paths during migration.
    """

    # -- static signals (always present) -------------------------------------
    project_name: str
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    handlers: list[Endpoint] = field(default_factory=list)
    background_jobs: list[BackgroundJob] = field(default_factory=list)
    data_access: list[DataAccessPattern] = field(default_factory=list)
    external_egress_hosts: list[str] = field(default_factory=list)

    # -- categorical / boolean signals ---------------------------------------
    state_kind: StateKind = "stateless"
    uses_websockets: bool = False
    long_running: bool = False                  # handler > 15 min cap
    cpu_bound: bool = False
    memory_bound: bool = False
    has_dockerfile: bool = False
    has_compose: bool = False
    architecture: Literal["monolith", "monolith-with-workers", "fullstack", "microservices"] = "monolith"

    # -- dynamic observations (optional, from profiling / telemetry) ---------
    observed_memory_p99_mb: float | None = None
    observed_cpu_p99_pct: float | None = None
    observed_cold_start_ms: float | None = None
    observed_rps: float | None = None

    # -- declarative inputs (from user CLI flags) ----------------------------
    slo: SLO | None = None
    budget_usd_month: int | None = None
    compliance: list[str] = field(default_factory=list)
    traffic: TrafficProfile | None = None

    # -- legacy bridge -------------------------------------------------------
    legacy_hints: list[str] = field(default_factory=list)
    """Original string hints from ProjectDetector. Kept for migration only."""

    def as_legacy_hints(self) -> list[str]:
        """Flatten the fingerprint into the old hint-string format.

        Used during migration: existing nodes that consume
        ``ProjectAnalysis.infrastructure_hints`` keep working unchanged when a
        fingerprint is built from an old topology.
        """
        hints = list(self.legacy_hints)
        if self.uses_websockets and "uses-websockets" not in hints:
            hints.append("uses-websockets")
        if self.long_running and "long-running-task" not in hints:
            hints.append("long-running-task")
        if self.cpu_bound and "cpu-bound" not in hints:
            hints.append("cpu-bound")
        if self.memory_bound and "memory-bound" not in hints:
            hints.append("memory-bound")
        if self.state_kind == "stateful" and "stateful" not in hints:
            hints.append("stateful")
        return hints

    @classmethod
    def from_topology(cls, topology: ApplicationTopology) -> "WorkloadFingerprint":
        """Bridge: project an existing ``ApplicationTopology`` into a fingerprint.

        Only the fields derivable from today's detector are populated. AST and
        sandbox profiling (§11.1 / §11.2) will fill the rest in later passes.
        """
        languages = list({s.language for s in topology.services})
        frameworks = list({s.framework for s in topology.services if s.framework})
        hints = list(dict.fromkeys(topology.all_hints))

        if topology.service_count >= 5 or "has-service-discovery" in hints:
            arch: str = "microservices"
        elif "has-worker" in hints:
            arch = "monolith-with-workers"
        elif "fullstack" in hints:
            arch = "fullstack"
        else:
            arch = "monolith"

        return cls(
            project_name=topology.project_name,
            languages=languages,
            frameworks=frameworks,
            architecture=arch,  # type: ignore[arg-type]
            has_dockerfile=any(s.has_dockerfile for s in topology.services),
            has_compose=topology.has_compose,
            uses_websockets="uses-websockets" in hints,
            long_running="long-running-task" in hints,
            cpu_bound="cpu-bound" in hints,
            memory_bound="memory-bound" in hints,
            state_kind="stateful" if "stateful" in hints else "stateless",
            legacy_hints=hints,
        )

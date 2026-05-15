"""Shared dataclasses for the agent and analyzers.

No internal imports to avoid circular dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LanguageAnalysis:
    language: str
    framework: str | None = None
    version: str | None = None
    dependencies: list[str] = field(default_factory=list)
    dev_dependencies: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    exposed_ports: list[int] = field(default_factory=list)
    has_dockerfile: bool = False
    has_tests: bool = False
    architecture_hints: list[str] = field(default_factory=list)


@dataclass
class ProjectAnalysis:
    primary_language: str
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    architecture: str = "monolith"
    dependencies: list[str] = field(default_factory=list)
    exposed_ports: list[int] = field(default_factory=list)
    has_dockerfile: bool = False
    has_tests: bool = False
    infrastructure_hints: list[str] = field(default_factory=list)
    raw_analyses: list[LanguageAnalysis] = field(default_factory=list)


@dataclass
class ServiceRecommendation:
    service_name: str
    purpose: str
    justification: str
    estimated_monthly_cost: str
    terraform_resource: str


@dataclass
class EnvironmentConfig:
    name: str
    strategy: str


@dataclass
class Decision:
    """Why a particular service was chosen for a given purpose.

    Attached to ``ArchitectureProposal.decisions``. Powers the ``--explain``
    output and the auditable Architecture Decision Records described in
    MEJORAS.md §6.1 and §17.1.

    Fields
    ------
    purpose:
        The purpose slot in the proposal (e.g. ``"compute"``, ``"database"``).
    chosen_service:
        Human-readable name of the selected service.
    alternatives_considered:
        Tuples of ``(service_name, score, reason)``. ``score`` is the
        normalized cost score the optimizer used (0.0 = cheapest);
        ``reason`` explains why the alternative was excluded or not picked.
    signals_used:
        Project / workload signals that drove the selection (e.g.
        ``"rps_observed=45"`` or ``"uses-websockets"``).
    constraints_applied:
        Hard constraints that limited the candidate set (compliance,
        region availability, SLO factibility).
    confidence:
        ``0.0–1.0`` — how confident the engine is in this choice. The CLI
        can gate ``--yolo`` mode on this (MEJORAS.md §17.2).
    """
    purpose: str
    chosen_service: str
    alternatives_considered: list[tuple[str, float, str]] = field(default_factory=list)
    signals_used: list[str] = field(default_factory=list)
    constraints_applied: list[str] = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class ArchitectureProposal:
    provider: str
    region: str
    services: list[ServiceRecommendation] = field(default_factory=list)
    total_estimated_cost: str = "~$0/mes"
    security_notes: list[str] = field(default_factory=list)
    scalability_notes: list[str] = field(default_factory=list)
    environments: dict[str, EnvironmentConfig] = field(default_factory=dict)
    # New optional fields — None keeps all existing construction sites working
    metadata: ProposalMetadata | None = None
    cloud_mappings: dict[str, str] = field(default_factory=dict)
    decisions: list[Decision] = field(default_factory=list)


@dataclass
class DeploymentResult:
    environments: dict[str, dict[str, Any]] = field(default_factory=dict)
    endpoints: list[str] = field(default_factory=list)
    success: bool = False


# ---------------------------------------------------------------------------
# Topology models — added below existing dataclasses (no modifications above)
# ---------------------------------------------------------------------------

# Complexity profile values as plain strings (not Enum) so LangGraph's
# JsonPlusSerializer handles them natively without explicit registration.
PROFILE_NANO       = "nano"
PROFILE_MICRO      = "micro"
PROFILE_SMALL      = "small"
PROFILE_STANDARD   = "standard"
PROFILE_LARGE      = "large"
PROFILE_ENTERPRISE = "enterprise"


@dataclass
class ServiceSummary:
    """Lightweight per-service record inside ApplicationTopology."""
    name: str
    path_relative: str       # relative to project root — never absolute (JSON-safe)
    language: str
    framework: str | None
    hints: list[str]         # architecture_hints from that service's analyzer
    ports: list[int]
    has_dockerfile: bool


@dataclass
class ApplicationTopology:
    """Full project topology produced by ProjectDetector.detect_topology().

    Stored in AetherState.application_topology (new optional field).
    The existing AetherState.project_analysis is always populated too,
    derived via as_project_analysis() for all existing code paths.
    """
    project_name: str
    profile: str                      # one of PROFILE_* constants above
    service_count: int
    services: list[ServiceSummary]
    all_hints: list[str]              # union of hints from all services (deduped)
    has_compose: bool
    compose_service_count: int        # 0 if no compose file
    llm_reasoning: str | None = None  # one-sentence rationale from LLM enricher

    def as_project_analysis(self) -> ProjectAnalysis:
        """Derives a backwards-compatible ProjectAnalysis from this topology."""
        primary_lang = self.services[0].language if self.services else "unknown"
        frameworks = list({s.framework for s in self.services if s.framework})
        hints = list(dict.fromkeys(self.all_hints))  # deduped, order preserved
        ports = sorted({p for s in self.services for p in s.ports}) or [8080]

        if self.service_count >= 5 or "has-service-discovery" in hints:
            arch = "microservices"
        elif "has-worker" in hints:
            arch = "monolith-with-workers"
        elif "fullstack" in hints:
            arch = "fullstack"
        else:
            arch = "monolith"

        return ProjectAnalysis(
            primary_language=primary_lang,
            languages=list({s.language for s in self.services}),
            frameworks=frameworks,
            architecture=arch,
            dependencies=hints,
            exposed_ports=ports,
            has_dockerfile=any(s.has_dockerfile for s in self.services),
            has_tests=False,
            infrastructure_hints=hints,
        )


@dataclass
class ProposalMetadata:
    """Attached to ArchitectureProposal when topology is available."""
    profile: str                      # one of PROFILE_* constants
    confidence: float                 # 0.0–1.0
    deploy_time_estimate: str         # e.g. "3–5 min"
    cost_low_usd: int
    cost_high_usd: int
    unknowns: list[str] = field(default_factory=list)

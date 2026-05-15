"""Bridge between :mod:`solver` (CP-SAT output) and the existing proposal
model (:class:`ArchitectureProposal`).

Lets callers run the solver, get up to three Pareto-optimal proposals, and
render them through the same TUI / explain pipeline that the LangGraph node
uses for the legacy matrix-based proposal.

This adapter does **not** call the LLM. Justifications come from the catalog
``justification`` field; richer prose can be layered on top later.
"""
from __future__ import annotations

from .models import (
    ArchitectureProposal,
    Decision,
    ProposalMetadata,
    ServiceRecommendation,
)
from .providers.aws.services import SERVICE_ALTERNATIVES
from .solver import SolverProposal, solve
from .workload import WorkloadFingerprint


def solve_to_proposals(
    fingerprint: WorkloadFingerprint,
    *,
    provider: str = "aws",
    region: str = "us-east-1",
) -> list[ArchitectureProposal]:
    """Run the solver and wrap each strategy as an ArchitectureProposal."""
    catalog = SERVICE_ALTERNATIVES if provider == "aws" else {}
    if not catalog:
        return []
    solver_results = solve(fingerprint, catalog)
    return [_to_proposal(sp, provider, region, fingerprint) for sp in solver_results]


def _to_proposal(
    sp: SolverProposal,
    provider: str,
    region: str,
    fingerprint: WorkloadFingerprint,
) -> ArchitectureProposal:
    services: list[ServiceRecommendation] = []
    decisions: list[Decision] = []
    hints = fingerprint.as_legacy_hints()

    rejected_by_purpose: dict[str, list[tuple[str, str]]] = {}
    for purpose, service, reason in sp.rejected:
        rejected_by_purpose.setdefault(purpose, []).append((service, reason))

    for choice in sp.choices:
        opt = choice.option
        services.append(
            ServiceRecommendation(
                service_name=opt["service"],
                purpose=choice.purpose,
                justification=opt["justification"],
                estimated_monthly_cost=f"~${opt['cost_low']}-{opt['cost_high']}",
                terraform_resource=opt["resource"],
            )
        )
        alternatives = [
            (svc, 1.0, reason)
            for (svc, reason) in rejected_by_purpose.get(choice.purpose, [])
        ]
        decisions.append(
            Decision(
                purpose=choice.purpose,
                chosen_service=opt["service"],
                alternatives_considered=alternatives,
                signals_used=hints[:6],
                constraints_applied=sp.notes,
                confidence=0.9 if sp.strategy != "performant" else 0.75,
            )
        )

    metadata = ProposalMetadata(
        profile=sp.strategy,
        confidence=0.9 if sp.strategy != "performant" else 0.75,
        deploy_time_estimate="3-5 min",
        cost_low_usd=sp.total_cost_low,
        cost_high_usd=sp.total_cost_high,
    )
    return ArchitectureProposal(
        provider=provider,
        region=region,
        services=services,
        total_estimated_cost=f"~${sp.total_cost_low}-{sp.total_cost_high}/month ({sp.strategy})",
        security_notes=[],
        scalability_notes=sp.notes,
        environments={},
        metadata=metadata,
        decisions=decisions,
    )

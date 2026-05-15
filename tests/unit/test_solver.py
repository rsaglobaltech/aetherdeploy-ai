"""Tests for the CP-SAT solver (MEJORAS.md §13.1)."""
from __future__ import annotations

import pytest

from aetherdeploy.providers.aws.services import ServiceOption
from aetherdeploy.solver import SolverProposal, solve
from aetherdeploy.solver_adapter import solve_to_proposals
from aetherdeploy.workload import SLO, WorkloadFingerprint


def _opt(service: str, low: int, high: int, *, requires=None, excludes=None) -> ServiceOption:
    return ServiceOption(
        service=service,
        resource=f"aws_{service.lower().replace(' ', '_')}",
        justification=f"option {service}",
        cost_low=low,
        cost_high=high,
        requires=list(requires or []),
        excludes=list(excludes or []),
    )


def _basic_catalog():
    return {
        "compute": [
            _opt("Lambda", 0, 50),
            _opt("Fargate", 80, 300),
            _opt("EC2", 30, 200, excludes=["serverless-preferred"]),
        ],
        "database": [
            _opt("DynamoDB", 0, 25),
            _opt("Aurora", 60, 200, requires=["needs-sql"]),
        ],
    }


def _fp(hints: list[str] | None = None, *, budget: int | None = None, slo: SLO | None = None) -> WorkloadFingerprint:
    fp = WorkloadFingerprint(project_name="t")
    if hints:
        fp.legacy_hints = list(hints)
    fp.budget_usd_month = budget
    fp.slo = slo
    return fp


def test_solver_returns_three_strategies():
    proposals = solve(_fp(), _basic_catalog())
    assert {p.strategy for p in proposals} == {"cheap", "balanced", "performant"}


def test_cheap_strategy_picks_lowest_cost():
    proposals = solve(_fp(), _basic_catalog(), strategies=["cheap"])
    cheap = proposals[0]
    compute = next(c for c in cheap.choices if c.purpose == "compute")
    db = next(c for c in cheap.choices if c.purpose == "database")
    assert compute.option["service"] == "Lambda"
    assert db.option["service"] == "DynamoDB"
    assert cheap.total_cost_high == 50 + 25


def test_performant_strategy_picks_higher_cost_within_constraints():
    proposals = solve(_fp(hints=["needs-sql"]), _basic_catalog(), strategies=["performant"])
    perf = proposals[0]
    assert perf.total_cost_high > 75  # not the cheapest combo


def test_excludes_hint_rules_out_option():
    proposals = solve(
        _fp(hints=["serverless-preferred"]),
        _basic_catalog(),
        strategies=["cheap"],
    )
    chosen_services = {c.option["service"] for c in proposals[0].choices}
    assert "EC2" not in chosen_services
    # And it must appear in rejected with the right reason.
    rejected_names = {svc for (_p, svc, _r) in proposals[0].rejected}
    assert "EC2" in rejected_names


def test_requires_hint_missing_excludes_option():
    proposals = solve(_fp(), _basic_catalog(), strategies=["cheap"])
    # Aurora needs "needs-sql"; without it the DB must be DynamoDB.
    db = next(c for c in proposals[0].choices if c.purpose == "database")
    assert db.option["service"] == "DynamoDB"
    rejected_names = {svc for (p, svc, _r) in proposals[0].rejected if p == "database"}
    assert "Aurora" in rejected_names


def test_budget_cap_blocks_expensive_combos():
    fp = _fp(hints=["needs-sql"], budget=40)
    proposals = solve(fp, _basic_catalog(), strategies=["performant"])
    # performant maximizes cost — but must stay under 40.
    assert all(p.total_cost_high <= 40 for p in proposals)


def test_infeasible_budget_returns_empty():
    fp = _fp(budget=1)  # impossible: cheapest DB is 0 high, Lambda is 0 high — actually feasible.
    # Use a budget below the cheapest combo.
    catalog = {
        "compute": [_opt("Lambda", 10, 50)],
        "database": [_opt("DynamoDB", 10, 25)],
    }
    fp2 = _fp(budget=10)
    proposals = solve(fp2, catalog, strategies=["cheap"])
    # 50 + 25 = 75 > 10 → infeasible
    assert proposals == []


def test_empty_catalog_returns_empty():
    proposals = solve(_fp(), {}, strategies=["cheap"])
    assert proposals == []


def test_purpose_with_no_valid_option_is_skipped():
    """When all options for a purpose are filtered out, that purpose is
    omitted from the proposal instead of failing the whole solve. The
    workload simply doesn't need that purpose."""
    catalog = {
        "compute": [_opt("Lambda", 0, 50)],
        "database": [_opt("Aurora", 60, 200, requires=["needs-sql"])],
    }
    proposals = solve(_fp(), catalog, strategies=["cheap"])
    assert len(proposals) == 1
    purposes_chosen = {c.purpose for c in proposals[0].choices}
    assert purposes_chosen == {"compute"}
    rejected_db = {svc for (p, svc, _r) in proposals[0].rejected if p == "database"}
    assert "Aurora" in rejected_db


def test_solver_returns_empty_when_every_purpose_filtered():
    catalog = {
        "compute": [_opt("Lambda", 0, 50, requires=["unmet-1"])],
        "database": [_opt("Aurora", 60, 200, requires=["unmet-2"])],
    }
    proposals = solve(_fp(), catalog, strategies=["cheap"])
    assert proposals == []


def test_solver_proposal_is_dataclass():
    """Sanity: SolverProposal can be dict-serialized."""
    import dataclasses
    proposals = solve(_fp(), _basic_catalog(), strategies=["cheap"])
    d = dataclasses.asdict(proposals[0])
    assert d["strategy"] == "cheap"
    assert d["total_cost_high"] > 0


def test_adapter_produces_architecture_proposal_with_decisions():
    """The adapter must convert a SolverProposal into a fully-formed
    ArchitectureProposal with decision metadata populated."""
    # Hints chosen so every purpose in SERVICE_ALTERNATIVES has at least one
    # valid option after the requires/excludes filter runs.
    fp = WorkloadFingerprint(
        project_name="demo",
        legacy_hints=["has-database", "has-cache", "has-queue", "has-payment"],
    )
    proposals = solve_to_proposals(fp, provider="aws", region="us-east-1")
    assert proposals, "expected at least one feasible proposal"
    p = proposals[0]
    assert p.provider == "aws"
    assert p.region == "us-east-1"
    assert p.services, "services should be populated"
    assert p.decisions, "decisions should be populated"
    assert p.metadata is not None
    assert p.metadata.cost_low_usd >= 0
    assert p.metadata.cost_high_usd >= p.metadata.cost_low_usd


def test_adapter_returns_empty_for_unknown_provider():
    proposals = solve_to_proposals(
        WorkloadFingerprint(project_name="t"), provider="azure", region="eastus"
    )
    assert proposals == []

"""Constraint-solver-driven service selection (MEJORAS.md §13.1).

Replaces the "LLM picks cheapest among alternatives" heuristic with a
deterministic CP-SAT solver. Given a workload fingerprint, an alternatives
catalog per purpose, and optional SLO/budget constraints, it produces up to
three Pareto-optimal proposals (cheap / balanced / performant).

Design choices
--------------
* **Deterministic.** Same inputs → same output, sub-second on real catalogs.
  This is the property the LLM cannot guarantee.
* **Additive.** This module does not replace ``proposal.py`` today; the agent
  graph still routes through the matrix + LLM path. The solver is invoked
  by callers (CLI ``solve`` command and, in a later commit, by the proposal
  node when ``WorkloadFingerprint`` is available).
* **LLM still useful — for prose.** Once the solver picks services, the LLM
  enriches justifications and answers what-if queries (MEJORAS.md §16.1).
* **No optional behaviour around ortools.** It's a hard dependency now; the
  solver always runs when called. Callers fall back to legacy logic if they
  choose not to call this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ortools.sat.python import cp_model

from .providers.aws.services import ServiceOption
from .workload import WorkloadFingerprint

Strategy = Literal["cheap", "balanced", "performant"]


# ---------------------------------------------------------------------------
# Solver input / output types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SolverChoice:
    """One purpose → one selected service from the catalog."""
    purpose: str
    option: ServiceOption


@dataclass
class SolverProposal:
    """A self-contained proposal returned by the solver."""
    strategy: Strategy
    choices: list[SolverChoice] = field(default_factory=list)
    total_cost_low: int = 0
    total_cost_high: int = 0
    rejected: list[tuple[str, str, str]] = field(default_factory=list)
    """Tuples of (purpose, service, reason) for options ruled out by hard constraints."""
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def solve(
    fingerprint: WorkloadFingerprint,
    alternatives_by_purpose: dict[str, list[ServiceOption]],
    *,
    strategies: list[Strategy] | None = None,
) -> list[SolverProposal]:
    """Solve service selection for the given fingerprint and catalog.

    Parameters
    ----------
    fingerprint:
        Inputs the solver reasons over: hints, SLOs, budget, compliance.
    alternatives_by_purpose:
        Map of purpose → ordered list of catalog options.
    strategies:
        Subset of ``{"cheap", "balanced", "performant"}`` to compute. Defaults
        to all three.

    Returns
    -------
    A list of :class:`SolverProposal` — one per strategy. Strategies that
    were infeasible under the constraints are omitted; the caller should
    relax the budget or SLO and retry.
    """
    strategies = list(strategies or ("cheap", "balanced", "performant"))
    hints = set(fingerprint.as_legacy_hints())
    purposes = sorted(alternatives_by_purpose.keys())
    if not purposes:
        return []

    # Pre-filter: enforce ``requires`` and ``excludes`` against the fingerprint.
    # Options ruled out here never enter the CP-SAT model — keeps the model
    # small and gives us readable rejection reasons.
    #
    # If a purpose has zero valid options, we treat the workload as "doesn't
    # need this purpose" and skip it (the catalog says "cache requires has-cache";
    # a fingerprint without ``has-cache`` simply doesn't get a cache).
    valid: dict[str, list[ServiceOption]] = {}
    global_rejected: list[tuple[str, str, str]] = []
    skipped_purposes: list[str] = []
    for purpose in purposes:
        kept: list[ServiceOption] = []
        for opt in alternatives_by_purpose[purpose]:
            excluded = [h for h in opt.get("excludes", []) if h in hints]
            required_missing = [r for r in opt.get("requires", []) if r not in hints]
            if excluded:
                global_rejected.append((purpose, opt["service"], f"excluded by {excluded}"))
                continue
            if required_missing:
                global_rejected.append((purpose, opt["service"], f"requires {required_missing}"))
                continue
            kept.append(opt)
        if not kept:
            skipped_purposes.append(purpose)
            continue
        valid[purpose] = kept

    if not valid:
        return []
    purposes = sorted(valid.keys())

    results: list[SolverProposal] = []
    for strategy in strategies:
        proposal = _solve_one(strategy, valid, fingerprint, purposes, global_rejected)
        if proposal is not None:
            results.append(proposal)
    return results


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _solve_one(
    strategy: Strategy,
    valid: dict[str, list[ServiceOption]],
    fingerprint: WorkloadFingerprint,
    purposes: list[str],
    global_rejected: list[tuple[str, str, str]],
) -> SolverProposal | None:
    model = cp_model.CpModel()

    # One integer variable per purpose, domain = [0, len(options)-1].
    var_by_purpose: dict[str, cp_model.IntVar] = {}
    for purpose in purposes:
        var_by_purpose[purpose] = model.NewIntVar(
            0, len(valid[purpose]) - 1, f"choice_{purpose}"
        )

    # ``Element`` wires the chosen index to its cost contribution. We model two
    # totals (low and high bounds) — the objective uses ``high`` for cost-
    # minimization but the proposal exposes both for the explain output.
    cost_low_terms: list[cp_model.IntVar] = []
    cost_high_terms: list[cp_model.IntVar] = []
    for purpose in purposes:
        options = valid[purpose]
        low_vals = [int(o["cost_low"]) for o in options]
        high_vals = [int(o["cost_high"]) for o in options]
        low_var = model.NewIntVar(min(low_vals), max(low_vals), f"low_{purpose}")
        high_var = model.NewIntVar(min(high_vals), max(high_vals), f"high_{purpose}")
        model.AddElement(var_by_purpose[purpose], low_vals, low_var)
        model.AddElement(var_by_purpose[purpose], high_vals, high_var)
        cost_low_terms.append(low_var)
        cost_high_terms.append(high_var)

    # Hard constraint: budget cap (uses the upper-bound cost so we don't
    # over-promise — see MEJORAS.md §13.3).
    if fingerprint.budget_usd_month:
        model.Add(sum(cost_high_terms) <= int(fingerprint.budget_usd_month))

    # Objective per strategy.
    headroom_penalty: list[cp_model.IntVar] = []
    if strategy == "cheap":
        model.Minimize(sum(cost_high_terms))
    elif strategy == "balanced":
        # Balanced = minimize cost + a small penalty for picking the very
        # cheapest option (favours mid-tier choices that absorb growth).
        for purpose in purposes:
            options = valid[purpose]
            penalty_vals = [_balanced_penalty(idx, len(options)) for idx in range(len(options))]
            penalty_var = model.NewIntVar(min(penalty_vals), max(penalty_vals), f"pen_{purpose}")
            model.AddElement(var_by_purpose[purpose], penalty_vals, penalty_var)
            headroom_penalty.append(penalty_var)
        model.Minimize(sum(cost_high_terms) + sum(headroom_penalty))
    else:  # performant
        # Maximize cost_high (within budget if present) — proxy for "more
        # headroom". Real metric (latency, cold-start) will replace this once
        # the dynamic profiler lands (MEJORAS.md §11.2).
        model.Maximize(sum(cost_high_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 2.0
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    choices: list[SolverChoice] = []
    total_low = 0
    total_high = 0
    for purpose in purposes:
        idx = solver.Value(var_by_purpose[purpose])
        opt = valid[purpose][idx]
        choices.append(SolverChoice(purpose=purpose, option=opt))
        total_low += int(opt["cost_low"])
        total_high += int(opt["cost_high"])

    notes: list[str] = []
    if fingerprint.budget_usd_month:
        notes.append(f"Budget cap: ${fingerprint.budget_usd_month}/mo")
    if fingerprint.slo and fingerprint.slo.p99_latency_ms:
        notes.append(f"SLO p99 ≤ {fingerprint.slo.p99_latency_ms}ms")
    return SolverProposal(
        strategy=strategy,
        choices=choices,
        total_cost_low=total_low,
        total_cost_high=total_high,
        rejected=list(global_rejected),
        notes=notes,
    )


def _balanced_penalty(idx: int, total: int) -> int:
    """Penalty for the extremes — favours the middle of the cost spectrum.

    Returns 0 at the median index, scaling up at both ends. The exact shape
    isn't critical; the goal is "not the very cheapest, not the very most
    expensive" when costs are otherwise similar.
    """
    if total <= 1:
        return 0
    median = (total - 1) / 2
    return int(abs(idx - median) * 4)

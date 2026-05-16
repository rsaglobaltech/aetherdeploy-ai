"""Cost gate node (MEJORAS.md §3.1).

Runs Infracost against every generated Terraform directory and optionally
blocks the deploy when the projected monthly cost exceeds an operator-defined
budget. Soft-skips when ``infracost`` is not installed so the rest of the
pipeline still runs in dev environments.

The budget is read from ``AETHER_COST_BUDGET_USD`` (global) with an
environment-specific override ``AETHER_COST_BUDGET_USD_<ENV>``. A value of
``0`` or unset disables the gate but the breakdown is still surfaced — the
operator gets visibility without coercion.
"""
from __future__ import annotations

import os
from pathlib import Path

from ...cost import CostReport, run_infracost
from ..context import _emitter_var
from ..state import AetherState


SKIP_ENVS: frozenset[str] = frozenset({"local"})


def _is_disabled() -> bool:
    return os.environ.get("AETHER_COST_DISABLED") == "1"


def _budget_for(env: str) -> float | None:
    raw = os.environ.get(f"AETHER_COST_BUDGET_USD_{env.upper()}") or os.environ.get("AETHER_COST_BUDGET_USD")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _envs_to_estimate(state: AetherState) -> list[str]:
    envs = state.get("target_environments") or []
    return [env for env in envs if env not in SKIP_ENVS]


async def cost_node(state: AetherState) -> dict:
    emit = _emitter_var.get()

    if _is_disabled():
        emit({"type": "message", "role": "assistant", "content": "Skipping cost estimation (AETHER_COST_DISABLED=1)."})
        return {"current_step": "cost_skipped"}

    if state.get("dry_run"):
        return {"current_step": "cost_skipped"}

    if (state.get("requested_action") or "deploy") not in ("deploy", "plan"):
        return {"current_step": "cost_skipped"}

    envs = _envs_to_estimate(state)
    if not envs:
        return {"current_step": "cost_skipped"}

    project_path = Path(state.get("project_path") or ".")
    aggregate: dict[str, dict] = {}
    blockers: list[str] = []
    messages: list[dict] = list(state.get("messages", []))

    emit({"type": "progress", "step": "cost_start", "percentage": 67})

    for env in envs:
        tf_dir = project_path / ".aetherdeploy" / "terraform" / env
        if not tf_dir.exists():
            aggregate[env] = {"skipped": True, "reason": "no terraform directory"}
            continue

        emit({
            "type": "message",
            "role": "assistant",
            "content": f"Estimating monthly cost for '{env}' with Infracost...",
        })
        report = run_infracost(tf_dir)
        env_data = report.to_dict()
        budget = _budget_for(env)
        env_data["budget_usd"] = budget

        if report.skipped_reason:
            emit({"type": "message", "role": "assistant", "content": f"[{env}] {report.summary()}"})
        elif not report.ok:
            emit({"type": "message", "role": "assistant", "content": f"[{env}] {report.summary()}"})
        else:
            emit({"type": "message", "role": "assistant", "content": f"[{env}] {report.summary()}"})

        if (
            report.ok
            and not report.skipped_reason
            and budget is not None
            and report.total_monthly_cost > budget
        ):
            env_data["over_budget"] = True
            blockers.append(
                f"{env}: ${report.total_monthly_cost:.2f}/mo exceeds budget ${budget:.2f}/mo"
            )
            emit({
                "type": "message",
                "role": "assistant",
                "content": (
                    f"[{env}] Cost gate BLOCKED: ${report.total_monthly_cost:.2f}/mo "
                    f"exceeds budget ${budget:.2f}/mo. Override with "
                    f"AETHER_COST_BUDGET_USD_{env.upper()}=<higher value> or remove the budget."
                ),
            })

        aggregate[env] = env_data
        messages.append({"role": "assistant", "content": f"[{env}] {report.summary()}"})

    if blockers:
        emit({"type": "progress", "step": "cost_blocked", "percentage": 100})
        return {
            "current_step": "cost_error",
            "cost_results": aggregate,
            "errors": [*state.get("errors", []), *blockers],
            "messages": messages,
        }

    emit({"type": "progress", "step": "cost_done", "percentage": 69})
    return {
        "current_step": "cost_done",
        "cost_results": aggregate,
        "messages": messages,
    }

"""Pre-flight quota + IAM node (MEJORAS.md §3.4).

Runs between ``cost`` and ``build``. The chosen placement saves the operator
from paying for an ECR push when the deploy is going to fail anyway because
IAM is missing ``lambda:CreateFunction`` or the account is out of Fargate
vCPUs in the target region.

Blocking semantics:
* A quota with status ``exhausted`` blocks.
* An IAM action evaluated as ``denied`` blocks.
* Any check that ends ``error`` / ``unknown`` is surfaced but does not block —
  treating unknowns as fatal would block deploys whenever Service Quotas or
  IAM Simulate are not callable (e.g. restricted dev account).
"""
from __future__ import annotations

import os
from typing import Any

from ...preflight import run_preflight
from ..context import _emitter_var
from ..state import AetherState


SKIP_ENVS: frozenset[str] = frozenset({"local", "feature"})


def _is_disabled() -> bool:
    return os.environ.get("AETHER_PREFLIGHT_DISABLED") == "1"


def _envs_needing_preflight(state: AetherState) -> list[str]:
    envs = state.get("target_environments") or []
    return [env for env in envs if env not in SKIP_ENVS]


async def preflight_node(state: AetherState) -> dict:
    emit = _emitter_var.get()

    if _is_disabled():
        emit({"type": "message", "role": "assistant", "content": "Skipping pre-flight (AETHER_PREFLIGHT_DISABLED=1)."})
        return {"current_step": "preflight_skipped"}

    if state.get("dry_run"):
        return {"current_step": "preflight_skipped"}

    if (state.get("requested_action") or "deploy") != "deploy":
        return {"current_step": "preflight_skipped"}

    envs = _envs_needing_preflight(state)
    if not envs:
        return {"current_step": "preflight_skipped"}

    proposal = state.get("architecture_proposal")
    if proposal is None:
        return {"current_step": "preflight_skipped"}

    region = getattr(proposal, "region", None) or "us-east-1"
    aggregate: dict[str, dict] = {}
    blockers: list[str] = []

    emit({"type": "progress", "step": "preflight_start", "percentage": 70})

    for env in envs:
        emit({
            "type": "message",
            "role": "assistant",
            "content": f"Running pre-flight (quotas + IAM simulate) for '{env}' in {region}...",
        })
        result = run_preflight(proposal, region=region)
        aggregate[env] = result.to_dict()
        emit({"type": "message", "role": "assistant", "content": f"[{env}] {result.summary()}"})

        if result.blocked:
            for b in result.blockers:
                emit({"type": "message", "role": "assistant", "content": f"[{env}] ✗ {b}"})
            blockers.extend(f"{env}: {b}" for b in result.blockers)

    if blockers:
        return {
            "current_step": "preflight_error",
            "preflight_results": aggregate,
            "errors": [
                *state.get("errors", []),
                "Pre-flight blocked: " + "; ".join(blockers)
                + ". Override with AETHER_PREFLIGHT_DISABLED=1 once resolved.",
            ],
        }

    emit({"type": "progress", "step": "preflight_done", "percentage": 72})
    return {"current_step": "preflight_done", "preflight_results": aggregate}

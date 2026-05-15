"""Terraform policy gate node (MEJORAS.md §3.2).

Runs after generation and before the image build. The placement is deliberate:
catching ``S3 public`` / ``IAM *:*`` / ``0.0.0.0/0`` violations before pushing
a 400 MB image saves time, registry storage, and a chunk of ECR cost.

Hard policy failures end the deploy. They can be acknowledged on a per-rule
basis via ``AETHER_ALLOW_POLICY`` (comma-separated rule IDs). The acknowledged
rules stay in the report so the audit log records what was overridden — only
the block decision is bypassed.
"""
from __future__ import annotations

import os
from pathlib import Path

from ...policy import PolicyResult, run_policy_gate
from ..context import _emitter_var
from ..state import AetherState


SKIP_ENVS: frozenset[str] = frozenset({"local"})


def _is_disabled() -> bool:
    return os.environ.get("AETHER_POLICY_DISABLED") == "1"


def _allowlist_from_env() -> list[str]:
    raw = os.environ.get("AETHER_ALLOW_POLICY", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _envs_to_check(state: AetherState) -> list[str]:
    envs = state.get("target_environments") or []
    return [env for env in envs if env not in SKIP_ENVS]


def _format_blockers(result: PolicyResult, limit: int = 10) -> str:
    lines: list[str] = []
    for finding in result.blocked_by[:limit]:
        loc = f"{finding.file}"
        if finding.line is not None:
            loc = f"{loc}:{finding.line}"
        lines.append(
            f"  ✗ {finding.severity.upper()} {finding.rule_id} [{finding.tool}] "
            f"{finding.resource} ({loc}) — {finding.message}"
        )
    extra = len(result.blocked_by) - limit
    if extra > 0:
        lines.append(f"  … and {extra} more")
    return "\n".join(lines)


async def policy_node(state: AetherState) -> dict:
    """Runs Checkov + tfsec on every generated Terraform directory."""
    emit = _emitter_var.get()

    if _is_disabled():
        emit({"type": "message", "role": "assistant", "content": "Skipping policy gate (AETHER_POLICY_DISABLED=1)."})
        return {"current_step": "policy_skipped"}

    if state.get("dry_run"):
        return {"current_step": "policy_skipped"}

    project_path = Path(state.get("project_path") or ".")
    envs = _envs_to_check(state)
    if not envs:
        return {"current_step": "policy_skipped"}

    allowlist = _allowlist_from_env()
    aggregate: dict[str, dict] = {}
    blocking_envs: list[str] = []
    new_messages: list[dict] = list(state.get("messages", []))

    emit({"type": "progress", "step": "policy_start", "percentage": 64})

    for env in envs:
        tf_dir = project_path / ".aetherdeploy" / "terraform" / env
        if not tf_dir.exists():
            aggregate[env] = {"skipped": True, "reason": "no terraform directory"}
            continue

        emit({
            "type": "message",
            "role": "assistant",
            "content": f"Running policy gate on .aetherdeploy/terraform/{env} ...",
        })
        result = run_policy_gate(tf_dir, allowlist=allowlist)
        aggregate[env] = _result_to_dict(result)

        emit({"type": "message", "role": "assistant", "content": result.summary()})
        if result.overrides_applied:
            emit({
                "type": "message",
                "role": "assistant",
                "content": f"Allow-list applied to: {', '.join(sorted(set(result.overrides_applied)))}",
            })
        if result.blocked:
            emit({"type": "message", "role": "assistant", "content": _format_blockers(result)})
            blocking_envs.append(env)
        new_messages.append({"role": "assistant", "content": f"[{env}] {result.summary()}"})

    if blocking_envs:
        emit({"type": "progress", "step": "policy_blocked", "percentage": 100})
        return {
            "current_step": "policy_error",
            "policy_results": aggregate,
            "errors": [
                *state.get("errors", []),
                f"Policy gate blocked: {', '.join(blocking_envs)}. Set AETHER_ALLOW_POLICY=<rule_id,...> "
                "to override specific rules (logged for audit).",
            ],
            "messages": new_messages,
        }

    emit({"type": "progress", "step": "policy_done", "percentage": 66})
    return {
        "current_step": "policy_done",
        "policy_results": aggregate,
        "messages": new_messages,
    }


def _result_to_dict(result: PolicyResult) -> dict:
    return {
        "blocked": result.blocked,
        "summary": result.summary(),
        "tools_run": result.report.tools_run,
        "tools_skipped": result.report.tools_skipped,
        "overrides_applied": result.overrides_applied,
        "findings": [f.to_dict() for f in result.report.findings],
        "blocked_by": [f.to_dict() for f in result.blocked_by],
    }

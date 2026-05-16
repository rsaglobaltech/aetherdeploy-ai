"""Database migration node (MEJORAS.md §2.2).

Runs after the infrastructure is up but before the deploy reports success.
Detects the migration tool (Flyway, Alembic, Prisma, …), then either runs the
tool in a local container (feature env) or schedules an ECS one-off task (any
cloud env).

The node is intentionally lenient: a missing migration tool, a missing
``DATABASE_URL`` discovered at run-time, or an unsupported provider produce a
warning and let the deploy continue. A migration that actually fails to apply,
however, blocks the deploy — that is what this stage exists for.
"""
from __future__ import annotations

import os
from pathlib import Path

from ...migrations import (
    MigrationPlan,
    MigrationRunResult,
    detect_migrations,
    run_migration_docker,
    run_migration_ecs,
)
from ...migrations.detector import select_primary_plan
from ...migrations.runner import EcsRunTaskTarget
from ..context import _emitter_var
from ..state import AetherState


SKIP_ENVS: frozenset[str] = frozenset({"local"})


def _is_disabled() -> bool:
    return os.environ.get("AETHER_MIGRATIONS_DISABLED") == "1"


def _envs_to_migrate(state: AetherState) -> list[str]:
    envs = state.get("target_environments") or []
    return [env for env in envs if env not in SKIP_ENVS]


def _database_url_for(env: str) -> str | None:
    """Operator-supplied DB URL for an environment.

    Precedence: ``AETHER_DB_URL_<ENV>`` (case-insensitive match) → ``DATABASE_URL``.
    """
    for key in (f"AETHER_DB_URL_{env.upper()}", "DATABASE_URL"):
        value = os.environ.get(key)
        if value:
            return value
    return None


def _extract_endpoints(state: AetherState, env: str) -> list[str]:
    result = state.get("deployment_result")
    if result is None:
        return []
    environments = getattr(result, "environments", None) or {}
    env_result = environments.get(env, {})
    return env_result.get("endpoints", []) or []


def _ecs_target_from_state(state: AetherState, env: str) -> EcsRunTaskTarget | None:
    """The CLI / agent state can carry an explicit migration target through
    ``state["migration_targets"][env]``. Without it we cannot run on ECS — the
    node logs that and skips.
    """
    targets = state.get("migration_targets") or {}
    cfg = targets.get(env)
    if not cfg:
        return None
    return EcsRunTaskTarget(
        cluster=cfg["cluster"],
        task_definition=cfg["task_definition"],
        container_name=cfg.get("container_name", "app"),
        subnets=list(cfg.get("subnets") or []),
        security_groups=list(cfg.get("security_groups") or []),
        assign_public_ip=bool(cfg.get("assign_public_ip", False)),
        region=cfg.get("region", "us-east-1"),
    )


async def migration_node(state: AetherState) -> dict:
    emit = _emitter_var.get()

    if _is_disabled():
        emit({"type": "message", "role": "assistant", "content": "Skipping migrations (AETHER_MIGRATIONS_DISABLED=1)."})
        return {"current_step": "migrations_skipped"}

    if state.get("dry_run"):
        return {"current_step": "migrations_skipped"}

    if (state.get("requested_action") or "deploy") not in ("deploy",):
        return {"current_step": "migrations_skipped"}

    envs = _envs_to_migrate(state)
    if not envs:
        return {"current_step": "migrations_skipped"}

    project_path = Path(state.get("project_path") or ".")
    plans = detect_migrations(project_path)
    if not plans:
        emit({"type": "message", "role": "assistant", "content": "No database migrations detected — skipping."})
        return {"current_step": "migrations_skipped"}

    primary = select_primary_plan(plans)
    assert primary is not None  # plans is non-empty so select_primary_plan returns one
    detected_msg = ", ".join(p.tool for p in plans)
    emit({"type": "message", "role": "assistant", "content": f"Detected migration tool(s): {detected_msg}. Running '{primary.tool}'."})

    results: dict[str, dict] = {}
    errors: list[str] = []

    for env in envs:
        outcome = await _run_for_env(env, primary, state, project_path)
        results[env] = outcome.to_dict()
        if not outcome.ok:
            errors.append(f"{env}: {outcome.error or 'migration failed'}")

    new_messages = list(state.get("messages", []))
    new_messages.append({
        "role": "assistant",
        "content": "Migrations: " + ", ".join(f"{env}={r['ok'] and 'ok' or 'failed'}" for env, r in results.items()),
    })

    if errors:
        return {
            "current_step": "migrations_error",
            "migration_results": results,
            "errors": [*state.get("errors", []), *errors],
            "messages": new_messages,
        }

    return {
        "current_step": "migrations_done",
        "migration_results": results,
        "messages": new_messages,
    }


async def _run_for_env(
    env: str,
    plan: MigrationPlan,
    state: AetherState,
    project_path: Path,
) -> MigrationRunResult:
    emit = _emitter_var.get()
    database_url = _database_url_for(env)
    if not database_url:
        emit({
            "type": "message",
            "role": "assistant",
            "content": (
                f"[{env}] No DATABASE_URL provided "
                f"(set AETHER_DB_URL_{env.upper()} or DATABASE_URL). Skipping migration for this env."
            ),
        })
        return MigrationRunResult(
            ok=True,
            backend="skipped",
            tool=plan.tool,
            error=f"DATABASE_URL not set for {env}",
        )

    forward = lambda line: emit({"type": "message", "role": "assistant", "content": f"[{env}] {line}"})

    if env == "feature":
        emit({"type": "message", "role": "assistant", "content": f"[{env}] Running {plan.tool} migration via local docker..."})
        return await run_migration_docker(
            plan=plan,
            database_url=database_url,
            project_path=project_path,
            on_line=forward,
        )

    target = _ecs_target_from_state(state, env)
    if target is None:
        emit({
            "type": "message",
            "role": "assistant",
            "content": (
                f"[{env}] No ECS run-task target configured. "
                "Provide state['migration_targets'][env] with cluster/task_definition/subnets. "
                "Skipping migration — set up the target and rerun, or run the migration manually."
            ),
        })
        return MigrationRunResult(
            ok=True,
            backend="skipped",
            tool=plan.tool,
            error="no ECS migration target configured",
        )

    emit({"type": "message", "role": "assistant", "content": f"[{env}] Running {plan.tool} migration via ECS one-off task..."})
    return run_migration_ecs(
        plan=plan,
        database_url=database_url,
        target=target,
        on_status=forward,
    )

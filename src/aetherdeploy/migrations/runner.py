"""Migration execution backends (MEJORAS.md §2.2).

Two backends are supported in v1:

* :func:`run_migration_docker` — runs the migration tool in a local docker
  container against a reachable database URL (LocalStack / docker-compose).
* :func:`run_migration_ecs` — schedules a one-off Fargate task with overridden
  command and env on the same cluster as the app, then waits for completion.

Both return a uniform :class:`MigrationRunResult` so the agent does not need
to switch on backend.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .detector import MigrationPlan


LineHandler = Callable[[str], None]


@dataclass
class MigrationRunResult:
    ok: bool
    backend: str                       # "docker" | "ecs"
    tool: str
    duration_s: float = 0.0
    exit_code: int | None = None
    task_id: str | None = None         # for ECS: the task ARN
    error: str | None = None
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "backend": self.backend,
            "tool": self.tool,
            "duration_s": round(self.duration_s, 2),
            "exit_code": self.exit_code,
            "task_id": self.task_id,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Local docker backend
# ---------------------------------------------------------------------------

async def run_migration_docker(
    plan: MigrationPlan,
    database_url: str,
    project_path: Path,
    image: str | None = None,
    network: str | None = None,
    on_line: LineHandler | None = None,
) -> MigrationRunResult:
    """Runs ``plan.command`` inside the migration container with ``plan.env_var=database_url``.

    The project directory is mounted at ``/workspace`` so the container has
    access to migration scripts. For Flyway specifically the SQL directory is
    mounted at ``/flyway/sql`` to match the default ``-locations``.
    """
    chosen_image = image or plan.image_hint

    docker_cmd: list[str] = ["docker", "run", "--rm"]
    if network:
        docker_cmd.extend(["--network", network])
    docker_cmd.extend(["-e", f"{plan.env_var}={database_url}"])
    docker_cmd.extend(["-v", f"{project_path}:/workspace"])
    docker_cmd.extend(["-w", "/workspace"])

    if plan.tool == "flyway":
        docker_cmd.extend(["-v", f"{plan.migrations_dir}:/flyway/sql"])

    docker_cmd.append(chosen_image)
    docker_cmd.extend(plan.command)

    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *docker_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    async def _drain(stream, sink, forward: bool):
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip("\n")
            sink.append(decoded)
            if forward and on_line:
                on_line(decoded)

    await asyncio.gather(
        _drain(proc.stdout, stdout_lines, True),
        _drain(proc.stderr, stderr_lines, False),
    )
    rc = await proc.wait()
    duration = time.monotonic() - start

    return MigrationRunResult(
        ok=rc == 0,
        backend="docker",
        tool=plan.tool,
        duration_s=duration,
        exit_code=rc,
        stdout="\n".join(stdout_lines),
        stderr="\n".join(stderr_lines),
        error=None if rc == 0 else (stderr_lines[-1] if stderr_lines else "docker migration exited non-zero"),
    )


# ---------------------------------------------------------------------------
# AWS ECS RunTask backend
# ---------------------------------------------------------------------------

@dataclass
class EcsRunTaskTarget:
    cluster: str
    task_definition: str               # name or ARN
    container_name: str
    subnets: list[str] = field(default_factory=list)
    security_groups: list[str] = field(default_factory=list)
    assign_public_ip: bool = False
    region: str = "us-east-1"


def _ecs_client(region: str) -> Any:
    try:
        import boto3  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required to run migrations on ECS. Install `aetherdeploy[aws]`."
        ) from exc
    return boto3.client("ecs", region_name=region)


def run_migration_ecs(
    plan: MigrationPlan,
    database_url: str,
    target: EcsRunTaskTarget,
    wait_timeout_s: int = 600,
    poll_interval_s: int = 6,
    on_status: LineHandler | None = None,
    boto3_client: Any | None = None,
) -> MigrationRunResult:
    """Runs an ECS one-off Fargate task with overridden command and waits for it to finish."""
    start = time.monotonic()
    try:
        client = boto3_client or _ecs_client(target.region)
    except RuntimeError as exc:
        return MigrationRunResult(ok=False, backend="ecs", tool=plan.tool, error=str(exc))

    overrides = {
        "containerOverrides": [
            {
                "name": target.container_name,
                "command": list(plan.command),
                "environment": [{"name": plan.env_var, "value": database_url}],
            }
        ]
    }

    network_cfg = None
    if target.subnets:
        network_cfg = {
            "awsvpcConfiguration": {
                "subnets": target.subnets,
                "securityGroups": target.security_groups,
                "assignPublicIp": "ENABLED" if target.assign_public_ip else "DISABLED",
            }
        }

    try:
        run_args = {
            "cluster": target.cluster,
            "taskDefinition": target.task_definition,
            "launchType": "FARGATE",
            "overrides": overrides,
            "count": 1,
        }
        if network_cfg:
            run_args["networkConfiguration"] = network_cfg
        response = client.run_task(**run_args)
    except Exception as exc:  # noqa: BLE001
        return MigrationRunResult(ok=False, backend="ecs", tool=plan.tool, error=f"ecs run_task failed: {exc}")

    failures = response.get("failures") or []
    if failures:
        reason = failures[0].get("reason", "unknown failure")
        return MigrationRunResult(ok=False, backend="ecs", tool=plan.tool, error=f"ecs run_task failures: {reason}")

    tasks = response.get("tasks") or []
    if not tasks:
        return MigrationRunResult(ok=False, backend="ecs", tool=plan.tool, error="ecs run_task returned no tasks")
    task_arn = tasks[0]["taskArn"]

    deadline = time.monotonic() + wait_timeout_s
    last_status = ""
    exit_code: int | None = None
    while time.monotonic() < deadline:
        described = client.describe_tasks(cluster=target.cluster, tasks=[task_arn])
        task = (described.get("tasks") or [{}])[0]
        status = task.get("lastStatus", "")
        if status != last_status and on_status:
            on_status(f"ecs task status: {status}")
        last_status = status
        if status == "STOPPED":
            containers = task.get("containers") or [{}]
            exit_code = containers[0].get("exitCode")
            break
        time.sleep(poll_interval_s)
    else:
        return MigrationRunResult(
            ok=False,
            backend="ecs",
            tool=plan.tool,
            task_id=task_arn,
            error=f"timed out after {wait_timeout_s}s in status '{last_status}'",
            duration_s=time.monotonic() - start,
        )

    duration = time.monotonic() - start
    if exit_code == 0:
        return MigrationRunResult(
            ok=True,
            backend="ecs",
            tool=plan.tool,
            exit_code=0,
            task_id=task_arn,
            duration_s=duration,
        )
    return MigrationRunResult(
        ok=False,
        backend="ecs",
        tool=plan.tool,
        exit_code=exit_code,
        task_id=task_arn,
        error=f"migration exited with code {exit_code}",
        duration_s=duration,
    )

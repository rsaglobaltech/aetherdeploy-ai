from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from ...agent.tools.shell import run_sync
from ...docker.composer import DockerComposer
from ...docker.runner import DockerRunner
from ...models import DeploymentResult
from ...terraform.runner import TerraformRunner
from ..context import _emitter_var  # noqa: F401 — re-exported for backward compat
from ..state import AetherState
from .health import check_endpoints

_composer = DockerComposer()
_docker = DockerRunner()
_terraform = TerraformRunner()

# Patterns that indicate the Terraform provider could not authenticate.
# These errors do not mean the IaC is wrong — just that credentials are missing.
_CRED_ERROR_RE = re.compile(
    r"No valid credential sources found"
    r"|failed to refresh cached credentials"
    r"|NoCredentialProviders"
    r"|InvalidClientTokenId"
    r"|credential.source",
    re.IGNORECASE,
)


def _is_credential_error(error: str) -> bool:
    return bool(_CRED_ERROR_RE.search(error))


# ---------------------------------------------------------------------------
# Comprobaciones de credenciales
# ---------------------------------------------------------------------------

def check_feature_credentials() -> dict | None:
    """Returns a dict with missing fields for the feature environment (LocalStack), or None if OK."""
    if not os.environ.get("LOCALSTACK_AUTH_TOKEN"):
        return {
            "provider": "localstack",
            "message": (
                "LocalStack requires an authentication token. "
                "Get yours at https://app.localstack.cloud/workspace/auth-token"
            ),
            "fields": ["LOCALSTACK_AUTH_TOKEN"],
        }
    return None


def check_prod_credentials(provider: str) -> dict | None:
    """Returns a dict with missing credential fields for the given provider, or None if OK."""
    provider = provider.lower()

    if provider == "aws":
        has_env = os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")
        has_file = Path(os.path.expanduser("~/.aws/credentials")).exists()
        if not has_env and not has_file:
            return {
                "provider": "aws",
                "message": (
                    "No AWS credentials found. "
                    "Provide AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
                ),
                "fields": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
            }

    elif provider == "gcp":
        has_env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        has_file = Path(os.path.expanduser(
            "~/.config/gcloud/application_default_credentials.json"
        )).exists()
        if not has_env and not has_file:
            return {
                "provider": "gcp",
                "message": (
                    "No GCP credentials found. "
                    "Provide the path to your service account JSON file."
                ),
                "fields": ["GOOGLE_APPLICATION_CREDENTIALS"],
            }

    elif provider == "azure":
        has_env = (
            os.environ.get("AZURE_CLIENT_ID")
            and os.environ.get("AZURE_CLIENT_SECRET")
            and os.environ.get("AZURE_TENANT_ID")
        )
        az_token = Path(os.path.expanduser("~/.azure/msal_token_cache.json")).exists()
        if not has_env and not az_token:
            return {
                "provider": "azure",
                "message": (
                    "No Azure credentials found. "
                    "Provide AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, and AZURE_TENANT_ID."
                ),
                "fields": ["AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID"],
            }

    return None


async def execution_node(state: AetherState) -> dict:
    """Executes the requested action, routing by environment: local / feature / prod."""
    emit = _emitter_var.get()
    envs = state.get("target_environments") or ["prod"]
    dry_run = state.get("dry_run", False)
    requested_action = state.get("requested_action") or "deploy"
    project_path = Path(state.get("project_path") or ".")
    analysis = state.get("project_analysis")
    proposal = state.get("architecture_proposal")

    if requested_action == "plan":
        return await _plan_environments(project_path, proposal, envs, state)

    if requested_action == "destroy":
        return await _destroy_environments(project_path, envs, state)

    if dry_run:
        return {
            "deployment_result": DeploymentResult(success=True, endpoints=["(dry-run)"]),
            "current_step": "deployed",
            "messages": [
                *state.get("messages", []),
                {"role": "assistant", "content": "Dry-run complete. No real deployment was executed."},
            ],
        }

    env_results: dict = {}
    all_endpoints: list[str] = []
    errors: list[str] = []

    for env in envs:
        if env == "local":
            result = await _deploy_local(project_path, analysis, proposal)
        elif env == "feature":
            result = await _deploy_feature(project_path, analysis, proposal)
        else:
            result = await _deploy_prod(project_path, analysis, proposal, env)

        env_results[env] = result
        all_endpoints.extend(result.get("endpoints", []))
        if not result.get("success"):
            errors.append(f"{env}: {result.get('error', 'unknown failure')}")

    success = len(errors) == 0
    endpoint_str = ", ".join(all_endpoints) if all_endpoints else "(none)"

    return {
        "deployment_result": DeploymentResult(
            environments=env_results,
            endpoints=all_endpoints,
            success=success,
        ),
        "current_step": "deployed" if success else "deploy_error",
        "errors": [*state.get("errors", []), *errors],
        "messages": [
            *state.get("messages", []),
            {
                "role": "assistant",
                "content": (
                    f"{'✓ Deployment complete' if success else '✗ Deployment finished with errors'}. "
                    f"Endpoints: {endpoint_str}"
                ),
            },
        ],
    }


async def _plan_environments(project_path: Path, proposal, envs: list[str], state: AetherState) -> dict:
    """Runs terraform init/plan per environment and returns a preview without applying changes."""
    env_results: dict = {}
    errors: list[str] = []

    for env in envs:
        if env == "local":
            result = {
                "success": True,
                "preview": "Local environment uses Docker Compose; no terraform plan required.",
            }
        else:
            result = await _terraform_plan_env(project_path, proposal, env)

        env_results[env] = result
        if not result.get("success"):
            errors.append(f"{env}: {result.get('error', 'unknown failure')}")

    success = len(errors) == 0

    # If every failure is a credential error, the IaC is correct but cloud auth is missing.
    # Report as a soft success: Terraform config is ready, credentials needed for the live diff.
    if not success and all(
        env_results.get(env, {}).get("credential_error")
        for env in env_results
        if not env_results[env].get("success")
    ):
        emit = _emitter_var.get()
        emit({"type": "progress", "step": "plan_cred_missing", "percentage": 100})
        return {
            "deployment_result": DeploymentResult(
                environments=env_results,
                endpoints=[],
                success=False,
            ),
            "current_step": "plan_ready",
            "errors": state.get("errors", []),
            "messages": [
                *state.get("messages", []),
                {
                    "role": "assistant",
                    "content": (
                        "✓ Terraform configuration generated at `.aetherdeploy/terraform/`.\n"
                        "Terraform plan requires cloud credentials to calculate the live diff. "
                        "Configure your credentials and run /plan again for the full diff, "
                        "or run /deploy to apply the configuration directly."
                    ),
                },
            ],
        }

    return {
        "deployment_result": DeploymentResult(
            environments=env_results,
            endpoints=["(plan: no changes applied)"] if success else [],
            success=success,
        ),
        "current_step": "plan_ready" if success else "deploy_error",
        "errors": [*state.get("errors", []), *errors],
        "messages": [
            *state.get("messages", []),
            {
                "role": "assistant",
                "content": (
                    "Plan complete. No infrastructure was applied; review the summary above before running /deploy."
                    if success
                    else "The plan encountered errors. No infrastructure was applied."
                ),
            },
        ],
    }


async def _destroy_environments(project_path: Path, envs: list[str], state: AetherState) -> dict:
    """Runs terraform destroy for each environment and stops Docker Compose for local."""
    emit = _emitter_var.get()
    env_results: dict = {}
    errors: list[str] = []

    for env in envs:
        if env == "local":
            result = await _destroy_local(project_path)
        elif env == "feature":
            result = await _destroy_feature(project_path)
        else:
            result = await _destroy_terraform_env(project_path, env)

        env_results[env] = result
        if not result.get("success"):
            errors.append(f"{env}: {result.get('error', 'unknown failure')}")

    success = len(errors) == 0
    return {
        "deployment_result": DeploymentResult(
            environments=env_results,
            endpoints=[],
            success=success,
        ),
        "current_step": "destroyed" if success else "deploy_error",
        "errors": [*state.get("errors", []), *errors],
        "messages": [
            *state.get("messages", []),
            {
                "role": "assistant",
                "content": (
                    "✓ All resources destroyed successfully."
                    if success
                    else "✗ Destroy finished with errors. Some resources may still exist."
                ),
            },
        ],
    }


async def _destroy_local(project_path: Path) -> dict:
    """Stops and removes Docker Compose services for the local environment."""
    emit = _emitter_var.get()
    emit({"type": "progress", "step": "docker_down", "percentage": 50})
    emit({"type": "message", "role": "assistant", "content": "Stopping local Docker Compose services..."})

    compose_file = project_path / ".aetherdeploy" / "docker-compose.yml"
    feature_compose = project_path / ".aetherdeploy" / "docker-compose.feature.yml"

    stopped_any = False
    for cf in (compose_file, feature_compose):
        if cf.exists():
            result = _docker.down(cf)
            if not result.ok:
                return {"success": False, "error": f"docker compose down failed: {_redact_secrets(result.stderr)}"}
            stopped_any = True

    if not stopped_any:
        emit({"type": "message", "role": "assistant", "content": "No local Docker Compose files found; nothing to stop."})

    emit({"type": "progress", "step": "docker_down_done", "percentage": 100})
    return {"success": True}


async def _destroy_feature(project_path: Path) -> dict:
    """Destroys the feature environment: stops Docker Compose (and LocalStack) then
    purges the Terraform state file.  Running terraform destroy is skipped because
    LocalStack is ephemeral — stopping the container wipes all resources, making a
    destroy plan unnecessary and avoiding issues with unsupported API operations."""
    emit = _emitter_var.get()

    emit({"type": "progress", "step": "feature_down", "percentage": 40})
    emit({"type": "message", "role": "assistant", "content": "Stopping feature environment (LocalStack + app containers)..."})

    feature_compose = project_path / ".aetherdeploy" / "docker-compose.feature.yml"
    if feature_compose.exists():
        result = _docker.down(feature_compose)
        if not result.ok:
            return {"success": False, "error": f"docker compose down failed: {_redact_secrets(result.stderr)}"}
    else:
        emit({"type": "message", "role": "assistant", "content": "No feature docker-compose file found; containers may already be stopped."})

    emit({"type": "progress", "step": "feature_state_clean", "percentage": 80})
    tf_dir = project_path / ".aetherdeploy" / "terraform" / "feature"
    for stale in ("terraform.tfstate", "terraform.tfstate.backup"):
        stale_path = tf_dir / stale
        if stale_path.exists():
            stale_path.unlink()
            emit({"type": "message", "role": "assistant", "content": f"Removed stale Terraform state: {stale}"})

    emit({"type": "progress", "step": "feature_down_done", "percentage": 100})
    return {"success": True}


async def _destroy_terraform_env(project_path: Path, env: str) -> dict:
    """Runs terraform destroy for a cloud environment."""
    emit = _emitter_var.get()
    tf_dir = project_path / ".aetherdeploy" / "terraform" / env

    if not tf_dir.exists():
        emit({
            "type": "message",
            "role": "assistant",
            "content": f"No Terraform directory found at {tf_dir} — nothing to destroy for '{env}'.",
        })
        return {"success": True, "skipped": True}

    if not _terraform.is_available():
        return {"success": False, "error": "terraform CLI is not available"}

    def _fwd(line: str) -> None:
        if line.strip():
            emit({"type": "message", "role": "assistant", "content": _redact_secrets(line)})

    emit({"type": "progress", "step": f"{env}_tf_init", "percentage": 30})
    emit({"type": "message", "role": "assistant", "content": f"Initializing Terraform for '{env}' (read-only state)..."})
    backend = env != "feature"
    init = await _terraform.init_streaming(tf_dir, backend=backend, on_line=_fwd)
    if not init.ok:
        return {"success": False, "error": _extract_tf_error(init.stderr or init.stdout)}

    emit({"type": "progress", "step": f"{env}_tf_destroy", "percentage": 60})
    emit({"type": "message", "role": "assistant", "content": f"Destroying infrastructure for '{env}'..."})
    destroy = await _terraform.destroy_streaming(
        tf_dir,
        vars={"environment": env} if env == "feature" else None,
        auto_approve=True,
        on_line=_fwd,
    )
    if not destroy.ok:
        return {"success": False, "error": _extract_tf_error(destroy.stderr or destroy.stdout)}

    emit({"type": "progress", "step": f"{env}_destroy_done", "percentage": 100})
    return {"success": True}


async def _deploy_local(project_path: Path, analysis, proposal) -> dict:
    """Deploys with Docker Compose in the local environment."""
    emit = _emitter_var.get()
    emit({"type": "progress", "step": "docker_build", "percentage": 65})
    if not _docker.is_available():
        return {"success": False, "error": "Docker is not available on this system"}

    if analysis is None or proposal is None:
        return {"success": False, "error": "Missing project analysis or proposal — cannot generate docker-compose"}

    emit({"type": "message", "role": "assistant", "content": "Generating docker-compose.yml..."})
    compose_content = _composer.generate(analysis, proposal)
    compose_file = project_path / ".aetherdeploy" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
    compose_file.write_text(compose_content)

    emit({"type": "progress", "step": "docker_up", "percentage": 80})
    emit({"type": "message", "role": "assistant", "content": "Starting containers..."})
    result = _docker.up(compose_file)
    if not result.ok:
        return {"success": False, "error": result.stderr or "docker compose up failed"}

    emit({"type": "progress", "step": "docker_ready", "percentage": 100})
    ports = analysis.exposed_ports or [8080]
    endpoints = _docker.get_service_urls(compose_file, ports[0])
    return {"success": True, "endpoints": endpoints, "compose_file": str(compose_file)}


async def _deploy_feature(project_path: Path, analysis, proposal) -> dict:
    """Deploys the feature environment using LocalStack (Docker Compose + Terraform).

    Flow:
    1. Generates docker-compose.feature.yml including the localstack service.
    2. Starts Docker Compose (app + localstack + aux services).
    3. Waits until LocalStack is healthy.
    4. Runs Terraform against localhost:4566 with dummy credentials.
    """
    emit = _emitter_var.get()
    emit({"type": "progress", "step": "feature_preflight", "percentage": 66})
    emit({
        "type": "message",
        "role": "assistant",
        "content": "Checking local services required for the feature environment...",
    })

    if not _docker.is_available():
        return {
            "success": False,
            "error": (
                "Docker is not available. The feature environment requires LocalStack, "
                "the app, and a local proxy running via Docker Compose."
            ),
        }

    if analysis is None or proposal is None:
        return {"success": False, "error": "Missing project analysis or proposal — cannot generate feature docker-compose"}

    localstack_ready = _is_localstack_healthy(project_path)
    if localstack_ready:
        emit({
            "type": "message",
            "role": "assistant",
            "content": "LocalStack is already responding at localhost:4566 — reusing it for this feature deployment.",
        })
    else:
        emit({
            "type": "message",
            "role": "assistant",
            "content": (
                "LocalStack is not running at localhost:4566. "
                "Starting LocalStack, the app, and nginx via Docker Compose."
            ),
        })

    emit({"type": "progress", "step": "feature_compose_gen", "percentage": 70})
    emit({
        "type": "message",
        "role": "assistant",
        "content": "Preparing docker-compose.feature.yml with LocalStack and auxiliary services...",
    })
    compose_content = _composer.generate_feature(analysis, proposal)
    compose_file = project_path / ".aetherdeploy" / "docker-compose.feature.yml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
    compose_file.write_text(compose_content)

    nginx_conf = _composer.generate_nginx_conf(analysis)
    nginx_conf_file = project_path / ".aetherdeploy" / "nginx.feature.conf"
    nginx_conf_file.write_text(nginx_conf)

    emit({"type": "progress", "step": "docker_up", "percentage": 74})
    emit({
        "type": "message",
        "role": "assistant",
        "content": "Running docker compose up to start all feature services...",
    })
    result = _docker.up(compose_file)
    if not result.ok:
        return {"success": False, "error": f"docker compose up failed: {_redact_secrets(result.stderr)}"}

    emit({"type": "progress", "step": "localstack_wait", "percentage": 78})
    emit({
        "type": "message",
        "role": "assistant",
        "content": "Waiting for LocalStack to finish booting and pass its healthcheck...",
    })
    wait_result = await _wait_for_localstack(project_path)
    if not wait_result["ok"]:
        return {"success": False, "error": wait_result["error"]}
    emit({
        "type": "message",
        "role": "assistant",
        "content": "LocalStack is ready. Continuing with Terraform against the local endpoint.",
    })

    # Docker and LocalStack are running — compute local endpoints now so they are
    # always included in the result even if Terraform encounters errors later.
    ports = analysis.exposed_ports or [8080]
    local_endpoints = [
        f"http://localhost:{ports[0]}",
        "http://localhost",
    ]

    tf_dir = project_path / ".aetherdeploy" / "terraform" / "feature"
    if not tf_dir.exists():
        return {
            "success": False,
            "error": f"No Terraform configuration found at {tf_dir}. Run generation first.",
            "endpoints": local_endpoints,
        }

    if not _terraform.is_available():
        return {"success": False, "error": "terraform CLI is not available", "endpoints": local_endpoints}

    # LocalStack is ephemeral — a restart wipes all resources. If a previous state
    # file references IDs that no longer exist, Terraform's refresh will fail.
    # Always start fresh: delete the local state before init so every feature deploy
    # is a clean apply against whatever LocalStack has right now.
    for stale in ("terraform.tfstate", "terraform.tfstate.backup"):
        stale_path = tf_dir / stale
        if stale_path.exists():
            stale_path.unlink()

    # -backend=false: the S3 backend from the template does not exist in LocalStack yet;
    # feature environments use local state.
    def _fwd(line: str) -> None:
        if line.strip():
            emit({"type": "message", "role": "assistant", "content": _redact_secrets(line)})

    emit({"type": "progress", "step": "tf_init", "percentage": 82})
    emit({"type": "message", "role": "assistant", "content": "Initializing Terraform for the feature environment..."})
    init = await _terraform.init_streaming(tf_dir, backend=False, on_line=_fwd)
    if not init.ok:
        return {"success": False, "error": _extract_tf_error(init.stderr or init.stdout), "endpoints": local_endpoints}

    emit({"type": "progress", "step": "tf_apply", "percentage": 90})
    emit({"type": "message", "role": "assistant", "content": "Provisioning services in LocalStack..."})
    apply = await _terraform.apply_streaming(
        tf_dir,
        vars={"environment": "feature"},
        auto_approve=True,
        on_line=_fwd,
    )
    if not apply.ok:
        return {"success": False, "error": _extract_tf_error(apply.stderr or apply.stdout), "endpoints": local_endpoints}

    output = _terraform.output(tf_dir)
    tf_endpoints = _extract_endpoints(output.stdout)
    all_endpoints = local_endpoints + tf_endpoints

    health = await _run_health_checks(all_endpoints, analysis, "feature")
    emit({"type": "progress", "step": "feature_done", "percentage": 100})

    if not health.ok:
        return {
            "success": False,
            "error": f"Post-deploy health checks failed:\n{health.summary()}",
            "endpoints": all_endpoints,
            "localstack": True,
            "compose_file": str(compose_file),
            "health": _health_to_dict(health),
        }

    return {
        "success": True,
        "endpoints": all_endpoints,
        "localstack": True,
        "compose_file": str(compose_file),
        "health": _health_to_dict(health),
    }


async def _wait_for_localstack(project_path: Path, timeout: int = 60) -> dict:
    """Polls until LocalStack responds at localhost:4566 or the timeout is reached."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = run_sync(
            ["curl", "-sf", "http://localhost:4566/_localstack/health"],
            cwd=str(project_path),
        )
        if r.ok:
            return {"ok": True}
        await asyncio.sleep(3)
    return {"ok": False, "error": f"LocalStack did not respond within {timeout}s"}


def _is_localstack_healthy(project_path: Path) -> bool:
    """Returns True if LocalStack is already responding before starting Compose."""
    result = run_sync(
        ["curl", "-sf", "http://localhost:4566/_localstack/health"],
        cwd=str(project_path),
    )
    return result.ok


async def _deploy_prod(project_path: Path, analysis, proposal, env: str) -> dict:
    """Deploys with Terraform to the specified environment."""
    emit = _emitter_var.get()
    tf_dir = project_path / ".aetherdeploy" / "terraform" / env
    if not tf_dir.exists():
        return {"success": False, "error": f"No Terraform configuration found at {tf_dir}"}

    if not _terraform.is_available():
        return {"success": False, "error": "terraform CLI is not available"}

    def _fwd(line: str) -> None:
        if line.strip():
            emit({"type": "message", "role": "assistant", "content": _redact_secrets(line)})

    emit({"type": "progress", "step": "tf_init", "percentage": 68})
    emit({"type": "message", "role": "assistant", "content": "Initializing Terraform (downloading providers)..."})
    init = await _terraform.init_streaming(tf_dir, on_line=_fwd)
    if not init.ok:
        return {"success": False, "error": _extract_tf_error(init.stderr or init.stdout)}

    emit({"type": "progress", "step": "tf_plan", "percentage": 78})
    emit({"type": "message", "role": "assistant", "content": "Calculating change plan (terraform plan)..."})
    plan = await _terraform.plan_streaming(tf_dir, on_line=_fwd)
    if not plan.ok:
        return {"success": False, "error": _extract_tf_error(plan.stderr or plan.stdout)}

    emit({"type": "progress", "step": "tf_apply", "percentage": 84})
    emit({"type": "message", "role": "assistant", "content": "Applying infrastructure..."})
    apply = await _terraform.apply_streaming(tf_dir, auto_approve=True, on_line=_fwd)
    if not apply.ok:
        return {"success": False, "error": _extract_tf_error(apply.stderr or apply.stdout)}

    emit({"type": "progress", "step": "tf_output", "percentage": 98})
    output = _terraform.output(tf_dir)
    endpoints = _extract_endpoints(output.stdout)

    health = await _run_health_checks(endpoints, analysis, env)
    emit({"type": "progress", "step": "prod_done", "percentage": 100})
    if not health.ok:
        return {
            "success": False,
            "error": f"Post-deploy health checks failed:\n{health.summary()}",
            "endpoints": endpoints,
            "health": _health_to_dict(health),
        }
    return {
        "success": True,
        "endpoints": endpoints,
        "health": _health_to_dict(health),
    }


async def _terraform_plan_env(project_path: Path, proposal, env: str) -> dict:
    """Initializes Terraform and calculates a plan for the given environment."""
    emit = _emitter_var.get()
    tf_dir = project_path / ".aetherdeploy" / "terraform" / env
    if not tf_dir.exists():
        return {"success": False, "error": f"No Terraform configuration found at {tf_dir}"}

    if not _terraform.is_available():
        return {"success": False, "error": "terraform CLI is not available"}

    emit({"type": "progress", "step": f"{env}_tf_init", "percentage": 68})
    emit({
        "type": "message",
        "role": "assistant",
        "content": _deployment_preview(proposal, env, tf_dir),
    })

    def _fwd(line: str) -> None:
        line = line.strip()
        if line:
            emit({"type": "message", "role": "assistant", "content": _friendly_tf_line(_redact_secrets(line))})

    backend = env != "feature"
    emit({
        "type": "message",
        "role": "assistant",
        "content": (
            "Initializing Terraform with local state for the feature environment..."
            if env == "feature"
            else "Initializing Terraform with the remote provider backend..."
        ),
    })
    init = await _terraform.init_streaming(tf_dir, backend=backend, on_line=_fwd)
    if not init.ok:
        err = _extract_tf_error(init.stderr or init.stdout)
        return {"success": False, "error": err, "credential_error": _is_credential_error(err)}

    emit({"type": "progress", "step": f"{env}_tf_plan", "percentage": 86})
    emit({
        "type": "message",
        "role": "assistant",
        "content": (
            f"Calculating changes for '{env}' with terraform plan -refresh=false..."
            if env == "feature"
            else f"Calculating changes for '{env}' with terraform plan..."
        ),
    })
    vars = {"environment": env} if env == "feature" else None
    plan = await _terraform.plan_streaming(tf_dir, vars=vars, refresh=env != "feature", on_line=_fwd)
    if not plan.ok:
        err = _extract_tf_error(plan.stderr or plan.stdout)
        return {"success": False, "error": err, "credential_error": _is_credential_error(err)}

    summary = _summarize_tf_plan(plan.stdout)
    emit({"type": "progress", "step": f"{env}_plan_ready", "percentage": 100})
    emit({"type": "message", "role": "assistant", "content": summary})
    return {"success": True, "summary": summary, "terraform_dir": str(tf_dir)}


def _sanitize_branch(branch: str) -> str:
    """Convierte 'feature/user-auth' → 'userauth' (válido para Terraform workspace)."""
    sanitized = re.sub(r"[^a-zA-Z0-9]", "", branch.split("/")[-1])
    return sanitized[:20] or "feat"


def _extract_endpoints(terraform_output: str) -> list[str]:
    """Extrae URLs del output JSON de terraform output."""
    import json
    try:
        data = json.loads(terraform_output)
        urls = []
        for key in ("app_url", "endpoint", "url", "dns_name"):
            if key in data:
                urls.append(data[key].get("value", ""))
        return [u for u in urls if u]
    except (json.JSONDecodeError, AttributeError):
        return []


# ---------------------------------------------------------------------------
# Health checks (MEJORAS.md §2.4)
# ---------------------------------------------------------------------------

def _health_timeout_for_env(env: str) -> float:
    override = os.environ.get("AETHER_HEALTHCHECK_TIMEOUT_S")
    if override:
        try:
            return max(1.0, float(override))
        except ValueError:
            pass
    return 30.0 if env == "feature" else 180.0


async def _run_health_checks(endpoints: list[str], analysis, env: str):
    """Polls deployment endpoints and reports the result through the emitter."""
    emit = _emitter_var.get()
    if os.environ.get("AETHER_HEALTHCHECK_DISABLED") == "1":
        from .health import HealthReport
        emit({"type": "message", "role": "assistant", "content": "Skipping post-deploy health checks (AETHER_HEALTHCHECK_DISABLED=1)."})
        return HealthReport()

    if not endpoints:
        from .health import HealthReport
        return HealthReport()

    emit({"type": "progress", "step": f"{env}_healthcheck", "percentage": 95})
    emit({
        "type": "message",
        "role": "assistant",
        "content": f"Probing {len(endpoints)} endpoint(s) for readiness...",
    })

    report = await check_endpoints(
        endpoints,
        analysis=analysis,
        timeout_s=_health_timeout_for_env(env),
    )

    if report.any_probed:
        emit({"type": "message", "role": "assistant", "content": report.summary()})
    return report


def _health_to_dict(report) -> dict:
    return {
        "ok": report.ok,
        "checks": [
            {
                "url": c.url,
                "status": c.status,
                "http_status": c.http_status,
                "latency_ms": c.latency_ms,
                "attempts": c.attempts,
                "error": c.error,
            }
            for c in report.checks
        ],
    }


def _extract_tf_error(raw: str) -> str:
    """Extrae las líneas de error más relevantes del output de Terraform.

    Terraform escribe sus errores con el formato '│ Error: ...' o 'Error: ...'.
    Extrae esas líneas en lugar de truncar desde el inicio.
    """
    raw = _redact_secrets(raw)
    lines = raw.splitlines()
    error_lines = [
        line for line in lines
        if "Error:" in line or line.startswith("│") or line.strip().lower().startswith("error")
    ]
    if error_lines:
        return "\n".join(error_lines[:20])
    # Fallback: últimas 800 chars (donde suelen estar los errores en Terraform)
    return raw[-800:] if len(raw) > 800 else raw


def _deployment_preview(proposal, env: str, tf_dir: Path) -> str:
    provider = getattr(proposal, "provider", "cloud")
    region = getattr(proposal, "region", "unknown region")
    services = getattr(proposal, "services", []) or []
    lines = [
        f"Plan for '{env}': Terraform will use provider {provider.upper()} in {region}.",
        f"IaC directory: {tf_dir}",
    ]
    if env == "feature":
        lines.append("Feature mode: same AWS resources declared, pointing to LocalStack.")
    if services:
        rendered = ", ".join(f"{svc.service_name} ({svc.purpose})" for svc in services[:6])
        lines.append(f"Resources to preview: {rendered}.")
    return "\n".join(lines)


def _friendly_tf_line(line: str) -> str:
    if line.startswith("Terraform has been successfully initialized"):
        return "Terraform initialized successfully."
    if line.startswith("Terraform will perform the following actions"):
        return "Terraform calculated the following planned actions:"
    if line.startswith("Plan: "):
        return _summarize_tf_plan(line)
    return line


def _summarize_tf_plan(raw: str) -> str:
    match = re.search(r"Plan:\s*(\d+)\s+to add,\s*(\d+)\s+to change,\s*(\d+)\s+to destroy", raw)
    if match:
        add, change, destroy = match.groups()
        return (
            "Terraform summary: "
            f"{add} to create, {change} to modify, {destroy} to destroy. "
            "No changes were applied."
        )
    if "No changes." in raw:
        return "Terraform summary: no pending changes. No changes were applied."
    return "Terraform summary: plan calculated. No changes were applied."


def _redact_secrets(text: str) -> str:
    """Oculta valores de credenciales conocidos antes de enviarlos al frontend."""
    redacted = text
    for key in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_TENANT_ID",
        "LOCALSTACK_AUTH_TOKEN",
    ):
        value = os.environ.get(key)
        if value and len(value) >= 4:
            redacted = redacted.replace(value, "[REDACTED]")
    redacted = re.sub(r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_ACCESS_KEY]", redacted)
    redacted = re.sub(
        r"(?i)(secret_access_key|client_secret|token|password)\s*=\s*['\"]?[^'\"\s]+",
        r"\1=[REDACTED]",
        redacted,
    )
    return redacted

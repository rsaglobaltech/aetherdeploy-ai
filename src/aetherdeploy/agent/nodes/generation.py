from __future__ import annotations

from pathlib import Path

from ...config import get_config
from ...providers import get_provider
from ...terraform.generator import TerraformGenerator
from ...terraform.state_backend import ensure_state_backend
from ..context import _emitter_var
from ..state import AetherState

_generator = TerraformGenerator()


async def generation_node(state: AetherState) -> dict:
    """Genera los ficheros Terraform y los escribe en .aetherdeploy/terraform/."""
    emit = _emitter_var.get()
    project_path = state.get("project_path") or "."
    proposal = state.get("architecture_proposal")
    dry_run = state.get("dry_run", False)
    envs = state.get("target_environments") or ["prod"]

    if proposal is None:
        return {
            "current_step": "generation_error",
            "errors": [*state.get("errors", []), "No architecture proposal available"],
        }

    emit({"type": "progress", "step": "generation_start", "percentage": 60})
    emit({"type": "message", "role": "assistant", "content": "Generating infrastructure as code..."})
    project_name = Path(project_path).resolve().name.lower().replace(" ", "-").replace("_", "-") or "app"
    all_configs: dict[str, str] = {}
    backend_bootstraps: dict[str, dict] = {}
    use_remote_backend = get_config().state_backend == "remote"

    for env in envs:
        if env == "local":
            continue  # local environment is handled by Docker

        provider = get_provider(proposal.provider)
        # Feature env always uses local state (LocalStack is ephemeral). For
        # other envs the user can opt out of the remote backend explicitly via
        # AETHER_STATE_BACKEND=local.
        backend = None
        if env != "feature" and use_remote_backend:
            backend = provider.get_terraform_backend_config(env, project_name)

        configs = _generator.generate(proposal, project_name, environment=env, backend_config=backend)
        all_configs.update({f"{env}/{k}": v for k, v in configs.items()})

        if not dry_run:
            output_dir = Path(project_path) / ".aetherdeploy" / "terraform" / env
            _generator.write(configs, output_dir)

        if backend and not dry_run:
            emit({
                "type": "message",
                "role": "assistant",
                "content": (
                    f"Bootstrapping remote state for '{env}': "
                    f"s3://{backend['bucket']} + DynamoDB '{backend.get('dynamodb_table', 'n/a')}' in {backend['region']}..."
                ),
            })
            bootstrap = ensure_state_backend(backend)
            backend_bootstraps[env] = bootstrap.to_dict()
            if bootstrap.skipped_reason:
                emit({
                    "type": "message",
                    "role": "assistant",
                    "content": f"State backend bootstrap skipped: {bootstrap.skipped_reason}",
                })
            if bootstrap.errors:
                for err in bootstrap.errors:
                    emit({"type": "message", "role": "assistant", "content": f"State backend warning: {err}"})
            if bootstrap.ok and not bootstrap.skipped_reason:
                emit({
                    "type": "message",
                    "role": "assistant",
                    "content": (
                        f"State backend ready for '{env}': "
                        f"bucket={bootstrap.bucket_status}, lock={bootstrap.lock_status}."
                    ),
                })

    action = "generated (dry-run)" if dry_run else "written to `.aetherdeploy/terraform/`"

    return {
        "terraform_configs": all_configs,
        "state_backends": backend_bootstraps,
        "current_step": "generation_done",
        "messages": [
            *state.get("messages", []),
            {
                "role": "assistant",
                "content": (
                    f"Terraform files {action}: "
                    f"{', '.join(configs.keys())} for {', '.join(e for e in envs if e != 'local')}"
                ),
            },
        ],
    }

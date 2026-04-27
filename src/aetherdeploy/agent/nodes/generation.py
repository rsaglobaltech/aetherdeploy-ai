from __future__ import annotations

from pathlib import Path

from ...providers import get_provider
from ...terraform.generator import TerraformGenerator
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

    for env in envs:
        if env == "local":
            continue  # local environment is handled by Docker

        provider = get_provider(proposal.provider)
        # Feature env uses LocalStack with local state — no S3 backend
        backend = None if env == "feature" else provider.get_terraform_backend_config(env, project_name)
        configs = _generator.generate(proposal, project_name, environment=env, backend_config=backend)
        all_configs.update({f"{env}/{k}": v for k, v in configs.items()})

        if not dry_run:
            output_dir = Path(project_path) / ".aetherdeploy" / "terraform" / env
            _generator.write(configs, output_dir)

    action = "generated (dry-run)" if dry_run else "written to `.aetherdeploy/terraform/`"

    return {
        "terraform_configs": all_configs,
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

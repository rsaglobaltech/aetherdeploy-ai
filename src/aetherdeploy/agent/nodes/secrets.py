"""Application secrets node (MEJORAS.md §3.3).

Runs between ``generation`` and ``policy``. The placement is deliberate:

* Generation has just written the per-env Terraform directory, so we can drop
  ``secrets.tf`` next to it without a second write pass.
* Policy needs the secret resources in scope to validate that, for instance,
  the IAM role granting ``GetSecretValue`` does not also grant ``*``.

The node is detection-driven: no example files → no work, no surprise resources
created on someone else's account. When detection finds something the operator
sees the breakdown via the emitter and can opt out via
``AETHER_SECRETS_DISABLED=1`` for legacy projects that manage secrets out-of-band.
"""
from __future__ import annotations

import os
from pathlib import Path

from ...secrets import (
    detect_secrets,
    generate_secrets_tf,
)
from ...secrets.generator import write_secrets_tf
from ...secrets.provisioner import provision_aws_secret_values
from ..context import _emitter_var
from ..state import AetherState


SKIP_ENVS: frozenset[str] = frozenset({"local"})


def _is_disabled() -> bool:
    return os.environ.get("AETHER_SECRETS_DISABLED") == "1"


def _envs_to_handle(state: AetherState) -> list[str]:
    envs = state.get("target_environments") or []
    return [env for env in envs if env not in SKIP_ENVS]


def _project_slug(project_path: Path) -> str:
    return project_path.resolve().name.lower().replace(" ", "-").replace("_", "-") or "app"


async def secrets_node(state: AetherState) -> dict:
    emit = _emitter_var.get()

    if _is_disabled():
        emit({"type": "message", "role": "assistant", "content": "Skipping secrets handling (AETHER_SECRETS_DISABLED=1)."})
        return {"current_step": "secrets_skipped"}

    if state.get("dry_run"):
        return {"current_step": "secrets_skipped"}

    project_path = Path(state.get("project_path") or ".")
    specs = detect_secrets(project_path)
    if not specs:
        return {"current_step": "secrets_skipped", "secrets_results": {}}

    proposal = state.get("architecture_proposal")
    provider = (getattr(proposal, "provider", "") or "").lower() if proposal else "aws"
    project_name = _project_slug(project_path)

    envs = _envs_to_handle(state)
    if not envs:
        return {"current_step": "secrets_skipped"}

    emit({
        "type": "message",
        "role": "assistant",
        "content": (
            f"Detected {len(specs)} application secret(s): "
            + ", ".join(s.name for s in specs)
            + ". Generating cloud-vault resources..."
        ),
    })

    results: dict[str, dict] = {}

    for env in envs:
        tf_dir = project_path / ".aetherdeploy" / "terraform" / env
        if not tf_dir.exists():
            results[env] = {"skipped": True, "reason": "no terraform directory"}
            continue

        hcl = generate_secrets_tf(provider, specs, project_name=project_name, environment=env)
        written_path = write_secrets_tf(hcl, tf_dir)

        env_data: dict = {
            "detected": [
                {"name": s.name, "source": str(s.source.relative_to(project_path))}
                for s in specs
            ],
            "secrets_tf": str(written_path) if written_path else None,
            "provider": provider,
        }

        # When the operator supplied values via AETHER_SECRET_VALUE_<KEY> and the
        # provider has a runtime backend, write them. Without values the tf
        # resources still get created (containers) so the operator can fill them
        # later via the CLI or console — that path is documented in the README.
        if provider == "aws" and env != "feature":
            any_value_set = any(os.environ.get(f"AETHER_SECRET_VALUE_{s.name.upper()}") for s in specs)
            if any_value_set:
                region = getattr(proposal, "region", None) or "us-east-1"
                emit({
                    "type": "message",
                    "role": "assistant",
                    "content": f"Writing supplied secret values to AWS Secrets Manager in {region} (env '{env}')...",
                })
                provision = provision_aws_secret_values(
                    specs,
                    project_name=project_name,
                    environment=env,
                    region=region,
                )
                env_data["provisioned"] = [r.to_dict() for r in provision]
                # Surface a per-secret status without leaking values
                for r in provision:
                    emit({
                        "type": "message",
                        "role": "assistant",
                        "content": f"  • {r.name}: {r.status}" + (f" ({r.error})" if r.error else ""),
                    })
            else:
                env_data["provisioned"] = []
                missing = [s.name for s in specs]
                emit({
                    "type": "message",
                    "role": "assistant",
                    "content": (
                        f"No AETHER_SECRET_VALUE_* env vars set for '{env}'. "
                        f"Secret containers will be created empty for: {', '.join(missing)}. "
                        "Populate them later via AWS CLI / console or rerun with values set."
                    ),
                })

        results[env] = env_data

    return {
        "current_step": "secrets_done",
        "secrets_results": results,
    }

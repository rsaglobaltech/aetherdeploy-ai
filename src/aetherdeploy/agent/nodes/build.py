"""Container image build & push node (MEJORAS.md §2.1).

Sits between ``generation`` and ``execution``. For every target environment
that needs a container image (i.e. not ``local`` and not ``feature``):

1. Detects the project Dockerfile.
2. Ensures the registry exists for the configured provider (ECR for AWS).
3. Authenticates Docker against the registry with an ephemeral token.
4. Builds the image tagged with the current git SHA and ``latest``.
5. Pushes both tags.
6. Writes ``terraform.tfvars`` so the existing apply picks up
   ``var.image_uri``.

If anything fails the node records the error in ``AetherState["errors"]`` and
the graph short-circuits to the end — the operator can rerun once the issue
(missing Dockerfile, registry permissions, docker daemon) is resolved.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ...docker.builder import (
    BuildResult,
    build_image,
    compute_image_tag,
    detect_dockerfile,
    docker_login,
    is_docker_available,
    push_image,
)
from ..context import _emitter_var
from ..state import AetherState


SKIP_ENVS: frozenset[str] = frozenset({"local", "feature"})


def _project_slug(project_path: Path) -> str:
    return project_path.resolve().name.lower().replace(" ", "-").replace("_", "-") or "app"


def _is_build_disabled() -> bool:
    return os.environ.get("AETHER_BUILD_DISABLED") == "1"


def _envs_needing_image(state: AetherState) -> list[str]:
    requested = state.get("requested_action") or "deploy"
    if requested != "deploy":
        return []
    envs = state.get("target_environments") or []
    return [env for env in envs if env not in SKIP_ENVS]


async def build_node(state: AetherState) -> dict:
    """Builds and pushes the container image for every cloud target environment."""
    emit = _emitter_var.get()
    project_path = Path(state.get("project_path") or ".")
    proposal = state.get("architecture_proposal")
    dry_run = state.get("dry_run", False)

    if _is_build_disabled():
        emit({"type": "message", "role": "assistant", "content": "Skipping image build (AETHER_BUILD_DISABLED=1)."})
        return {"current_step": "build_skipped"}

    if dry_run:
        return {"current_step": "build_skipped"}

    targets = _envs_needing_image(state)
    if not targets:
        return {"current_step": "build_skipped"}

    if proposal is None:
        return {
            "current_step": "build_error",
            "errors": [*state.get("errors", []), "No architecture proposal — cannot build image"],
        }

    dockerfile = detect_dockerfile(project_path)
    if dockerfile is None:
        emit({
            "type": "message",
            "role": "assistant",
            "content": (
                "No Dockerfile found — skipping image build. Lambda zip packaging and "
                "buildless deploys still work; container compute (Fargate, Cloud Run, "
                "Container Apps) will fail until a Dockerfile is added."
            ),
        })
        return {"current_step": "build_skipped"}

    if not is_docker_available():
        return {
            "current_step": "build_error",
            "errors": [*state.get("errors", []), "Docker CLI is not available — cannot build image"],
        }

    provider = (getattr(proposal, "provider", "") or "").lower()
    if provider != "aws":
        emit({
            "type": "message",
            "role": "assistant",
            "content": (
                f"Image push for provider '{provider}' is not implemented yet (only AWS/ECR). "
                "Skipping build — apply will require an externally pushed image."
            ),
        })
        return {"current_step": "build_skipped"}

    tag = compute_image_tag(project_path)
    project_name = _project_slug(project_path)
    results: dict[str, dict] = {}
    errors: list[str] = []

    for env in targets:
        env_result = await _build_and_push_aws(
            project_path=project_path,
            project_name=project_name,
            env=env,
            tag=tag,
            proposal=proposal,
            emit=emit,
        )
        results[env] = env_result
        if not env_result.get("ok"):
            errors.append(f"{env}: {env_result.get('error', 'build failed')}")

    if errors:
        return {
            "current_step": "build_error",
            "errors": [*state.get("errors", []), *errors],
            "image_builds": results,
        }

    return {
        "current_step": "build_done",
        "image_builds": results,
        "messages": [
            *state.get("messages", []),
            {
                "role": "assistant",
                "content": (
                    "Image built and pushed for: "
                    + ", ".join(f"{env}={r['image_uri']}" for env, r in results.items())
                ),
            },
        ],
    }


async def _build_and_push_aws(
    project_path: Path,
    project_name: str,
    env: str,
    tag: str,
    proposal,
    emit,
) -> dict:
    """ECR-specific bootstrap, login, build, push for a single environment."""
    from ...providers.aws.registry import (
        EcrError,
        derive_repository_name,
        ensure_ecr_repository,
        get_ecr_credentials,
    )

    region = getattr(proposal, "region", None) or "us-east-1"
    repo_name = derive_repository_name(project_name, env)

    emit({"type": "progress", "step": f"{env}_ecr_bootstrap", "percentage": 55})
    emit({"type": "message", "role": "assistant", "content": f"Ensuring ECR repository '{repo_name}' in {region}..."})

    try:
        repo = ensure_ecr_repository(repo_name, region=region)
        creds = get_ecr_credentials(region)
    except EcrError as exc:
        return {"ok": False, "error": str(exc)}

    emit({"type": "progress", "step": f"{env}_docker_login", "percentage": 58})
    login = await docker_login(
        creds.registry,
        creds.username,
        creds.password,
        on_line=lambda line: emit({"type": "message", "role": "assistant", "content": line}),
    )
    if not login.ok:
        return {"ok": False, "error": login.error or "docker login failed"}

    emit({"type": "progress", "step": f"{env}_docker_build", "percentage": 62})
    emit({"type": "message", "role": "assistant", "content": f"Building image {repo.uri}:{tag} (platform linux/amd64)..."})
    build = await build_image(
        project_path=project_path,
        image_uri=repo.uri,
        tag=tag,
        on_line=lambda line: emit({"type": "message", "role": "assistant", "content": line}),
    )
    if not build.ok:
        return {"ok": False, "error": build.error or "docker build failed"}

    emit({"type": "progress", "step": f"{env}_docker_push", "percentage": 66})
    emit({"type": "message", "role": "assistant", "content": f"Pushing {build.image_uri}..."})
    push = await push_image(
        build.image_uri,  # already includes the SHA tag
        on_line=lambda line: emit({"type": "message", "role": "assistant", "content": line}),
    )
    if not push.ok:
        return {"ok": False, "error": push.error or "docker push failed"}

    latest_push = await push_image(
        f"{repo.uri}:latest",
        on_line=lambda line: emit({"type": "message", "role": "assistant", "content": line}),
    )
    if not latest_push.ok:
        # latest is a convenience tag; don't fail the deploy if only :latest push fails
        emit({"type": "message", "role": "assistant", "content": f"Warning: failed to push :latest tag — {latest_push.error}"})

    image_uri = build.image_uri or f"{repo.uri}:{tag}"
    # Templates declare `var.container_image`; aliasing here keeps Terraform
    # picking the freshly pushed tag without rewriting the templates.
    tfvars_path = _write_tfvars(
        project_path,
        env,
        {"container_image": image_uri, "image_uri": image_uri},
    )

    return {
        "ok": True,
        "image_uri": image_uri,
        "registry": repo.registry,
        "repository": repo.uri,
        "tag": tag,
        "tfvars": str(tfvars_path),
    }


def _write_tfvars(project_path: Path, env: str, vars: dict[str, str]) -> Path:
    """Persists Terraform variable values so the existing apply step picks them up.

    A JSON-encoded tfvars file (``terraform.tfvars.json``) is preferred over
    HCL because it serializes safely without quoting heuristics.
    """
    tf_dir = project_path / ".aetherdeploy" / "terraform" / env
    tf_dir.mkdir(parents=True, exist_ok=True)
    target = tf_dir / "terraform.tfvars.json"

    existing: dict[str, str] = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text() or "{}")
        except json.JSONDecodeError:
            existing = {}
    existing.update(vars)
    target.write_text(json.dumps(existing, indent=2))
    return target

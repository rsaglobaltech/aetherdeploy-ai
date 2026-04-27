from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional
from uuid import uuid4

import typer
from rich.console import Console

from .agent.context import _emitter_var
from .agent.graph import build_graph
from .agent.nodes.execution import check_prod_credentials, execution_node
from .agent.nodes.generation import generation_node
from .agent.state import AetherState
from .agent.tools.github import GitHubTool
from .cli_credentials import (
    ALLOWED_CREDENTIAL_KEYS as _ALLOWED_CREDENTIAL_KEYS,
    check_credentials_for_envs as _check_credentials_for_envs,
    credential_fields_for as _credential_fields_for,
    validate_provider_credentials as _validate_provider_credentials,
    validate_required_credentials as _validate_required_credentials,
)
from .cli_nlu import (
    detect_explicit_action as _detect_explicit_action,
    detect_intent as _detect_intent,
    extract_path_hint as _extract_path_hint,
    parse_env_reply as _parse_env_reply,
    parse_requested_action as _parse_requested_action,
    resolve_project_path as _resolve_project_path,
)
from .observability import log_event, setup_logging
from .output.renderer import Renderer

app = typer.Typer(
    name="aetherdeploy",
    help="AI-powered multicloud deployment agent",
    no_args_is_help=False,
)

console = Console()
renderer = Renderer()


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@app.command()
def deploy(
    instruction: str = typer.Argument(..., help="Natural language instruction"),
    project: str = typer.Option(".", "--project", "-p", help="Path to the project"),
    provider: Optional[str] = typer.Option(None, "--provider", help="Cloud provider (aws/gcp/azure)"),
    env: list[str] = typer.Option(["prod"], "--env", "-e", help="Target environments"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Generate IaC only; do not deploy"),
):
    """Deploy a project to the cloud using natural language."""
    log_path = setup_logging()
    log_event(
        "cli.deploy.start",
        instruction=instruction,
        project=project,
        provider=provider,
        environments=env,
        dry_run=dry_run,
        log_file=str(log_path),
    )
    asyncio.run(_deploy_async(instruction, project, provider, env, dry_run))


@app.command()
def destroy(
    project: str = typer.Option(".", "--project", "-p", help="Path to the project"),
    provider: Optional[str] = typer.Option(None, "--provider", help="Cloud provider (aws/gcp/azure)"),
    env: list[str] = typer.Option(["prod"], "--env", "-e", help="Target environments to destroy"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Destroy all resources and applications created by AetherDeploy."""
    log_path = setup_logging()
    log_event(
        "cli.destroy.start",
        project=project,
        provider=provider,
        environments=env,
        log_file=str(log_path),
    )

    if not yes:
        envs_str = ", ".join(env)
        console.print(
            f"\n[bold red]WARNING:[/bold red] This will permanently destroy all infrastructure "
            f"for environments: [yellow]{envs_str}[/yellow]"
        )
        confirmed = typer.confirm("Are you sure you want to continue?", default=False)
        if not confirmed:
            console.print("[dim]Destroy cancelled.[/dim]")
            raise typer.Exit(0)

    asyncio.run(_destroy_async(project, provider, env))


@app.command(name="stream", hidden=True)
def stream_mode():
    """JSON-lines stream mode for the Ink frontend. Internal use."""
    log_path = setup_logging()
    log_event("cli.stream.start", log_file=str(log_path))
    asyncio.run(_stream_mode(str(log_path)))


@app.command()
def analyze(
    project: str = typer.Option(".", "--project", "-p", help="Path to the project"),
    output: str = typer.Option("text", "--output", "-o", help="Output format: text | json"),
    llm: bool = typer.Option(False, "--llm", help="Force LLM enrichment pass (requires LLM backend)"),
):
    """Analyse a project and show what AetherDeploy understands. No deployment, no credentials."""
    asyncio.run(_analyze_async(project, output, llm))


# ---------------------------------------------------------------------------
# Async implementations
# ---------------------------------------------------------------------------

async def _analyze_async(project: str, output: str, use_llm: bool) -> None:
    """Pure analysis — no graph, no credentials, no Terraform."""
    import json as _json
    from .analyzers.detector import ProjectDetector
    from .analyzers.llm_enricher import LLMAnalysisEnricher
    from .agent.nodes.analysis import _rebuild_topology_with_hints

    path = Path(project).resolve()
    topology = ProjectDetector().detect_topology(path)

    enricher = LLMAnalysisEnricher()
    if use_llm or enricher._should_enrich(topology.all_hints, topology.service_count, path):
        enrichment = await enricher.enrich(
            static_hints=topology.all_hints,
            service_count=topology.service_count,
            project_path=path,
        )
        if enrichment.llm_used:
            topology = _rebuild_topology_with_hints(topology, enrichment.hints)
            import dataclasses as _dc
            topology = _dc.replace(topology, llm_reasoning=enrichment.reasoning or None)

    if output == "json":
        data = {
            "project_name": topology.project_name,
            "profile": topology.profile,
            "service_count": topology.service_count,
            "services": [
                {
                    "name": s.name,
                    "language": s.language,
                    "framework": s.framework,
                    "hints": s.hints,
                    "ports": s.ports,
                }
                for s in topology.services
            ],
            "all_hints": topology.all_hints,
            "has_compose": topology.has_compose,
        }
        console.print(_json.dumps(data, indent=2))
        return

    # Text output
    console.print(f"\n[bold]Project Analysis — {topology.project_name}[/bold]")
    console.rule()
    console.print(f"Profile      [cyan]{topology.profile.upper()}[/cyan]")
    console.print(f"Services     {topology.service_count}")
    for svc in topology.services:
        lang = f"{svc.language}" + (f" ({svc.framework})" if svc.framework else "")
        ports_str = ", ".join(str(p) for p in svc.ports) if svc.ports else "—"
        console.print(f"  ├─ {svc.name:<40} {lang:<30} ports: {ports_str}")

    notable = [h for h in topology.all_hints if h != "api-only"]
    if notable:
        console.print(f"Signals      {', '.join(notable)}")

    reasoning = topology.llm_reasoning
    if reasoning:
        console.print(f"LLM note     [dim]{reasoning}[/dim]")

    console.print()
    console.print(
        "Run [bold]aetherdeploy deploy[/bold] to propose infrastructure, "
        "or [bold]aetherdeploy deploy --dry-run[/bold] to preview Terraform."
    )


async def _deploy_async(
    instruction: str,
    project: str,
    provider: str | None,
    envs: list[str],
    dry_run: bool,
) -> None:
    graph = build_graph()
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: AetherState = {
        "user_message": instruction,
        "project_path": project,
        "github_url": None,
        "target_environments": envs,
        "preferred_provider": provider,
        "dry_run": dry_run,
        "requested_action": "deploy",
        "messages": [{"role": "user", "content": instruction}],
        "current_step": "start",
        "errors": [],
        "terraform_configs": {},
        "docker_compose": None,
        "project_analysis": None,
        "architecture_proposal": None,
        "deployment_result": None,
        "user_approved": None,
        "user_modifications": None,
    }

    async for event in graph.astream(initial_state, config):
        _handle_event(event)

    while graph.get_state(config).next:
        graph_state = graph.get_state(config)
        next_nodes = list(graph_state.next)

        if "promotion" in next_nodes:
            feature_result = graph_state.values.get("deployment_result")
            endpoints = feature_result.endpoints if feature_result else []
            endpoint_str = ", ".join(endpoints) or "http://localhost:8080"
            console.print(
                f"\n[bold green]✓ Your app is running in the feature environment (LocalStack).[/bold green]"
            )
            console.print(f"  Endpoints: [cyan]{endpoint_str}[/cyan]")
            answer = typer.prompt("\nPromote to real production?", default="n")
            promote = answer.strip().lower() in ("y", "yes", "s", "si", "sí")
            graph.update_state(config, {"promoted_to_prod": promote})

        else:
            proposal = graph_state.values.get("architecture_proposal")
            if proposal:
                renderer.render_proposal(proposal)

            answer = typer.prompt("\nDo you want to proceed with this proposal?", default="y")
            approved = answer.strip().lower() in ("y", "yes", "s", "si", "sí")

            if not approved:
                modifications = typer.prompt("What changes do you want? (enter to cancel)", default="")
                graph.update_state(config, {
                    "user_approved": False,
                    "user_modifications": modifications or None,
                })
            else:
                graph.update_state(config, {"user_approved": True, "user_modifications": None})

        async for event in graph.astream(None, config):
            _handle_event(event)

    final = graph.get_state(config).values
    result = final.get("deployment_result")
    if result:
        renderer.render_deployment_result(result)


async def _destroy_async(
    project: str,
    provider: str | None,
    envs: list[str],
) -> None:
    """Destroy infrastructure by running terraform destroy for each environment."""
    project_path = Path(project)
    tf_base = project_path / ".aetherdeploy" / "terraform"

    if not tf_base.exists():
        console.print(
            f"[yellow]No .aetherdeploy/terraform directory found in {project}.[/yellow]\n"
            "Nothing to destroy."
        )
        return

    from .agent.nodes.execution import _destroy_environments, _emitter_var

    def _emit(event: dict) -> None:
        msg_type = event.get("type", "")
        if msg_type == "message":
            renderer.render_message(event.get("role", "assistant"), event.get("content", ""))
        elif msg_type == "progress":
            renderer.render_step(event.get("step", ""))
        elif msg_type == "error":
            console.print(f"[red]Error:[/red] {event.get('message', '')}")

    state: AetherState = {
        "user_message": "destroy",
        "project_path": project,
        "github_url": None,
        "target_environments": envs,
        "preferred_provider": provider,
        "dry_run": False,
        "requested_action": "destroy",
        "messages": [],
        "current_step": "start",
        "errors": [],
        "terraform_configs": {},
        "docker_compose": None,
        "project_analysis": None,
        "architecture_proposal": None,
        "deployment_result": None,
        "user_approved": True,
        "user_modifications": None,
    }

    token = _emitter_var.set(_emit)
    try:
        result_dict = await _destroy_environments(project_path, envs, state)
    finally:
        _emitter_var.reset(token)

    result = result_dict.get("deployment_result")
    if result:
        renderer.render_deployment_result(result)


def _handle_event(event: dict) -> None:
    for node_name, node_output in event.items():
        if node_name.startswith("__"):
            continue
        if not isinstance(node_output, dict):
            continue
        step = node_output.get("current_step", "")
        messages = node_output.get("messages", [])
        if messages:
            last = messages[-1]
            renderer.render_message(last.get("role", "assistant"), last.get("content", ""))
        if step:
            renderer.render_step(step)


# ---------------------------------------------------------------------------
# Stream JSON-lines mode
# ---------------------------------------------------------------------------

async def _stream_mode(log_file: str | None = None) -> None:
    """Reads JSON commands from stdin and emits JSON events to stdout."""
    graph = build_graph()
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    initialized = False
    emitted_proposal = False
    _target_envs: list[str] = []
    _target_provider: str = "aws"
    _pending_credentials_action: str | None = None
    _last_instruction: str = ""
    _manual_promotion_pending = False
    # Onboarding state — active when the user hasn't passed --project at startup
    _onboarding_step: str | None = None       # "await_project" | "await_env" | None (done)
    _onboarding_project: str | None = None    # path collected during onboarding
    _pending_instruction: str = ""
    _pending_action: str = "deploy"
    _pending_provider: str | None = None
    _pending_envs: list[str] = []

    def emit(event: dict) -> None:
        print(json.dumps(event, default=str), flush=True)

    from .config import get_config as _get_config
    _cfg = _get_config()
    emit({
        "type": "ready",
        "log_file": log_file,
        "llm_provider": _cfg.llm_backend,
        "llm_model": _cfg.llm_model,
    })

    async def run_graph(state_or_none):
        nonlocal initialized, _target_provider, emitted_proposal
        token = _emitter_var.set(emit)
        log_event(
            "agent.graph.run.start",
            initialized=initialized,
            has_initial_state=state_or_none is not None,
            thread_id=thread_id,
        )
        try:
            async for event in graph.astream(state_or_none, config):
                log_event("agent.graph.event", thread_id=thread_id, graph_event=event)
                for node_name, node_output in event.items():
                    if node_name.startswith("__"):
                        continue
                    if not isinstance(node_output, dict):
                        continue
                    step = node_output.get("current_step", "")
                    messages = node_output.get("messages", [])
                    if messages:
                        last = messages[-1]
                        emit({
                            "type": "message",
                            "role": last.get("role", "assistant"),
                            "content": last.get("content", ""),
                        })
                    if step:
                        state_label = _step_to_agent_state(step)
                        emit({"type": "state_change", "state": state_label, "step": step})
                    proposal = node_output.get("architecture_proposal")
                    if proposal and not emitted_proposal:
                        emitted_proposal = True
                        _target_provider = proposal.provider
                        _emit_proposal(emit, proposal)

            graph_state = graph.get_state(config)
            log_event(
                "agent.graph.run.end",
                thread_id=thread_id,
                next_nodes=list(graph_state.next),
                current_step=graph_state.values.get("current_step"),
            )
            if graph_state.next:
                next_nodes = list(graph_state.next)

                if "promotion" in next_nodes:
                    feature_result = graph_state.values.get("deployment_result")
                    endpoints = feature_result.endpoints if feature_result else []
                    emit({
                        "type": "waiting_promotion",
                        "message": (
                            "Your app is running in the feature environment (LocalStack). "
                            "Promote to real production?"
                        ),
                        "endpoints": endpoints,
                    })
                else:
                    proposal = graph_state.values.get("architecture_proposal")
                    if proposal:
                        _target_provider = proposal.provider
                        if not emitted_proposal:
                            emitted_proposal = True
                            _emit_proposal(emit, proposal)
                    emit({
                        "type": "waiting_confirmation",
                        "message": "Do you want to proceed with this proposal?",
                    })

            else:
                final_step = graph.get_state(config).values.get("current_step")
                if final_step != "discovery_needs_github":
                    _result = graph.get_state(config).values.get("deployment_result")
                    _req_action = graph.get_state(config).values.get("requested_action") or "deploy"
                    if _result and _result.success and _req_action in ("deploy", "plan"):
                        emit({
                            "type": "deployment_complete",
                            "action": _req_action,
                            "endpoints": _result.endpoints or [],
                            "environments": list((_result.environments or {}).keys()),
                            "provider": _target_provider,
                        })
                    emit({"type": "done"})

            initialized = True
        except Exception as exc:  # noqa: BLE001
            log_event(
                "agent.graph.run.error",
                level="ERROR",
                thread_id=thread_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            emit({"type": "error", "message": f"Internal agent error: {exc}", "recoverable": False})
        finally:
            _emitter_var.reset(token)

    def emit_node_output(node_output: dict) -> None:
        messages = node_output.get("messages", [])
        if messages:
            last = messages[-1]
            emit({
                "type": "message",
                "role": last.get("role", "assistant"),
                "content": last.get("content", ""),
            })
        step = node_output.get("current_step", "")
        if step:
            emit({"type": "state_change", "state": _step_to_agent_state(step), "step": step})

    async def run_action_from_current(
        action: str,
        cmd: dict | None = None,
        *,
        envs_override: list[str] | None = None,
        skip_credential_check: bool = False,
    ) -> None:
        nonlocal _target_envs, _target_provider, _pending_credentials_action, _manual_promotion_pending

        current = dict(graph.get_state(config).values)

        if action == "destroy":
            # Destroy does not require a prior architecture proposal.
            if envs_override is not None:
                envs = envs_override
            elif cmd and cmd.get("environments"):
                envs = list(cmd.get("environments") or [])
            else:
                envs = current.get("target_environments") or _target_envs or ["prod"]

            provider = (cmd or {}).get("provider") or current.get("preferred_provider") or _target_provider
            project_path = Path(current.get("project_path") or ".")
            _target_envs = list(envs)
            _target_provider = provider

            emit({"type": "message", "role": "assistant", "content": f"Destroying infrastructure for: {', '.join(envs)}..."})
            emit({"type": "state_change", "state": "deploying", "step": "destroying"})

            from .agent.nodes.execution import _destroy_environments

            destroy_state: AetherState = {
                **current,
                "requested_action": "destroy",
                "target_environments": list(envs),
                "preferred_provider": provider,
                "user_approved": True,
            }
            token = _emitter_var.set(emit)
            try:
                result_dict = await _destroy_environments(project_path, list(envs), destroy_state)
                emit_node_output(result_dict)
            except Exception as exc:  # noqa: BLE001
                emit({"type": "error", "message": f"Error running /destroy: {exc}", "recoverable": False})
                return
            finally:
                _emitter_var.reset(token)

            emit({"type": "done"})
            return

        if not current.get("architecture_proposal"):
            emit({
                "type": "error",
                "message": "Run /init first to analyse the project and generate a proposal.",
                "recoverable": True,
            })
            return

        if envs_override is not None:
            envs = envs_override
        elif cmd and cmd.get("environments"):
            envs = list(cmd.get("environments") or [])
        else:
            envs = current.get("target_environments") or _target_envs or ["prod"]

        provider = (cmd or {}).get("provider") or current.get("preferred_provider") or _target_provider
        dry_run_value = (cmd or {}).get("dry_run")
        dry_run = bool(dry_run_value) if dry_run_value is not None else bool(current.get("dry_run", False))

        _target_envs = list(envs)
        _target_provider = provider

        current.update({
            "requested_action": action,
            "target_environments": list(envs),
            "preferred_provider": provider,
            "dry_run": dry_run,
            "user_approved": True,
            "user_modifications": None,
        })
        graph.update_state(config, current)

        if action == "init":
            proposal = current.get("architecture_proposal")
            if proposal:
                _emit_proposal(emit, proposal)
            emit({
                "type": "message",
                "role": "assistant",
                "content": "Initial context ready. Use /plan to preview Terraform or /deploy to apply.",
            })
            emit({"type": "done"})
            return

        if action not in {"plan", "deploy"}:
            emit({"type": "error", "message": f"Unsupported action: {action}", "recoverable": True})
            return

        if not skip_credential_check:
            cred_missing = _check_credentials_for_envs(_target_envs, _target_provider, action)
            if cred_missing:
                _pending_credentials_action = f"slash_{action}"
                emit({
                    "type": "waiting_credentials",
                    "provider": cred_missing["provider"],
                    "message": cred_missing["message"],
                    "fields": cred_missing["fields"],
                })
                return

            ok, validation_msg = await _validate_required_credentials(_target_envs, _target_provider, action)
            if not ok:
                _pending_credentials_action = f"slash_{action}"
                fields = _credential_fields_for(_target_envs, _target_provider)
                emit({"type": "error", "message": validation_msg, "recoverable": True})
                emit({
                    "type": "waiting_credentials",
                    "provider": fields["provider"],
                    "message": validation_msg,
                    "fields": fields["fields"],
                })
                return
            emit({"type": "message", "role": "assistant", "content": f"✓ {validation_msg}"})

        emit({
            "type": "message",
            "role": "assistant",
            "content": (
                "Continuing from the current proposal: generating Terraform and calculating the plan."
                if action == "plan"
                else "Continuing from the current proposal: generating Terraform and deploying the selected environment."
            ),
        })

        token = _emitter_var.set(emit)
        try:
            generation_output = await generation_node(current)
            emit_node_output(generation_output)
            current.update(generation_output)
            graph.update_state(config, current)

            execution_output = await execution_node(current)
            emit_node_output(execution_output)
            current.update(execution_output)
            graph.update_state(config, current)
        except Exception as exc:  # noqa: BLE001
            log_event(
                "cli.stream.action.error",
                level="ERROR",
                action=action,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            emit({"type": "error", "message": f"Error running /{action}: {exc}", "recoverable": False})
            return
        finally:
            _emitter_var.reset(token)

        result = current.get("deployment_result")
        if action == "deploy" and "feature" in _target_envs and result and result.success:
            _manual_promotion_pending = True
            emit({
                "type": "waiting_promotion",
                "message": (
                    "Your app is running in the feature environment (LocalStack). "
                    "Promote to real production?"
                ),
                "endpoints": result.endpoints,
            })
            return

        if result and result.success and action in ("deploy", "plan"):
            emit({
                "type": "deployment_complete",
                "action": action,
                "endpoints": result.endpoints or [],
                "environments": list((result.environments or {}).keys()),
                "provider": _target_provider,
            })

        emit({"type": "done"})

    async def _launch_from_onboarding() -> None:
        """Build the initial graph state from onboarding data and start the agent graph."""
        nonlocal _target_envs, _target_provider, _last_instruction

        project = _onboarding_project or "."
        envs_to_use = list(_pending_envs) if _pending_envs else ["prod"]
        provider = _pending_provider
        instruction = _pending_instruction or "deploy this project"
        action = _pending_action or "deploy"

        _target_envs = envs_to_use
        if provider:
            _target_provider = provider
        _last_instruction = instruction

        emit({
            "type": "message",
            "role": "assistant",
            "content": f"Analysing `{project}` for a **{envs_to_use[0]}** deployment…",
        })

        initial_state: AetherState = {
            "user_message": instruction,
            "project_path": project,
            "github_url": None,
            "target_environments": envs_to_use,
            "preferred_provider": provider,
            "dry_run": False,
            "requested_action": action,
            "messages": [{"role": "user", "content": instruction}],
            "current_step": "start",
            "errors": [],
            "terraform_configs": {},
            "docker_compose": None,
            "project_analysis": None,
            "architecture_proposal": None,
            "deployment_result": None,
            "user_approved": None,
            "user_modifications": None,
        }
        emit({"type": "state_change", "state": "analyzing", "step": "start"})
        await run_graph(initial_state)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            log_event("cli.stream.invalid_json", level="WARNING", raw=line)
            emit({"type": "error", "message": f"Invalid JSON command: {line!r}", "recoverable": True})
            continue
        log_event("cli.stream.command", command=cmd)

        # ---- Onboarding intercept: collecting project path ----
        if cmd["type"] == "message" and not initialized and _onboarding_step == "await_project":
            candidate = cmd.get("content", "").strip()
            resolved = _resolve_project_path(candidate)
            if resolved is None:
                emit({
                    "type": "message",
                    "role": "assistant",
                    "content": (
                        f"I couldn't find a directory at `{candidate}`. "
                        "Please check the path and try again."
                    ),
                })
                continue
            _onboarding_project = resolved
            if _pending_envs:
                # Env already known — launch directly
                _onboarding_step = None
                await _launch_from_onboarding()
            else:
                _onboarding_step = "await_env"
                emit({
                    "type": "message",
                    "role": "assistant",
                    "content": (
                        f"Found the project at `{resolved}`.\n"
                        "Which environment do you want to target?\n"
                        "  **feature** — ephemeral, runs against LocalStack\n"
                        "  **prod** — real cloud infrastructure\n"
                        "  **staging** — pre-production"
                    ),
                })
            continue

        # ---- Onboarding intercept: collecting environment ----
        if cmd["type"] == "message" and not initialized and _onboarding_step == "await_env":
            env = _parse_env_reply(cmd.get("content", ""))
            if env is None:
                emit({
                    "type": "message",
                    "role": "assistant",
                    "content": "Please choose one: **feature**, **prod**, or **staging**.",
                })
                continue
            _pending_envs = [env]
            _onboarding_step = None
            await _launch_from_onboarding()
            continue

        if cmd["type"] == "message" and not initialized:
            content = cmd.get("content", "")

            # ---- Onboarding: --project was not provided at startup → ask for path/env ----
            if cmd.get("project_path") is None and _onboarding_step is None:
                _ob_action, _ob_instruction = _parse_requested_action(content)
                _, _ob_prov, _ob_envs_det = _detect_intent(content)
                _ob_incoming_envs = list(cmd.get("environments") or _ob_envs_det or [])
                _ob_path = _extract_path_hint(content)

                if _ob_path is None:
                    # No path found → ask for project directory
                    _pending_instruction = _ob_instruction
                    _pending_action = _ob_action
                    _pending_provider = _ob_prov or cmd.get("provider")
                    _pending_envs = _ob_incoming_envs
                    _onboarding_step = "await_project"
                    if _pending_provider:
                        _target_provider = _pending_provider
                    emit({
                        "type": "message",
                        "role": "assistant",
                        "content": (
                            "Where is your project? "
                            "Enter the path to the directory, or `.` for the current directory."
                        ),
                    })
                    continue

                elif not _ob_incoming_envs:
                    # Path found but no env → confirm path and ask for env
                    _pending_instruction = _ob_instruction
                    _pending_action = _ob_action
                    _pending_provider = _ob_prov or cmd.get("provider")
                    _pending_envs = []
                    _onboarding_project = _ob_path
                    _onboarding_step = "await_env"
                    if _pending_provider:
                        _target_provider = _pending_provider
                    emit({
                        "type": "message",
                        "role": "assistant",
                        "content": (
                            f"Got it — found the project at `{_ob_path}`.\n"
                            "Which environment do you want to target?\n"
                            "  **feature** — ephemeral, runs against LocalStack\n"
                            "  **prod** — real cloud infrastructure\n"
                            "  **staging** — pre-production"
                        ),
                    })
                    continue

                else:
                    # Both path and env found inline → inject path and proceed normally
                    cmd = {**cmd, "project_path": _ob_path}

            requested_action, instruction = _parse_requested_action(content)

            # Respect explicit provider/env overrides from the frontend payload
            _last_instruction = instruction
            incoming_envs = cmd.get("environments") or []
            if not incoming_envs:
                _, detected_provider, detected_envs = _detect_intent(content)
                if detected_envs:
                    incoming_envs = detected_envs
                if detected_provider and not cmd.get("provider"):
                    cmd = {**cmd, "provider": detected_provider}
            _target_envs.extend(incoming_envs or ["prod"])

            # If provider explicitly specified in the command payload or detected from NL,
            # update _target_provider immediately so credential checks use the right provider.
            if cmd.get("provider"):
                _target_provider = cmd["provider"]

            initial_state: AetherState = {
                "user_message": instruction,
                "project_path": cmd.get("project_path", "."),
                "github_url": None,
                "target_environments": _target_envs,
                "preferred_provider": cmd.get("provider"),
                "dry_run": cmd.get("dry_run", False),
                "requested_action": requested_action,
                "messages": [{"role": "user", "content": instruction}],
                "current_step": "start",
                "errors": [],
                "terraform_configs": {},
                "docker_compose": None,
                "project_analysis": None,
                "architecture_proposal": None,
                "deployment_result": None,
                "user_approved": None,
                "user_modifications": None,
            }
            emit({"type": "state_change", "state": "analyzing", "step": "start"})

            if requested_action == "destroy":
                # Destroy before any deployment — just attempt with whatever Terraform state exists
                emit({"type": "message", "role": "assistant", "content": "Running destroy against any existing Terraform state..."})
                project_path = Path(cmd.get("project_path", "."))
                token = _emitter_var.set(emit)
                try:
                    from .agent.nodes.execution import _destroy_environments
                    result_dict = await _destroy_environments(project_path, _target_envs, initial_state)
                    emit_node_output(result_dict)
                except Exception as exc:  # noqa: BLE001
                    emit({"type": "error", "message": f"Destroy error: {exc}", "recoverable": False})
                finally:
                    _emitter_var.reset(token)
                emit({"type": "done"})
                initialized = True
            elif requested_action == "init":
                emit({
                    "type": "message",
                    "role": "assistant",
                    "content": "Initializing project context: I'll analyze the stack and provider without running Terraform.",
                })
                await run_graph(initial_state)
            elif requested_action == "plan":
                emit({
                    "type": "message",
                    "role": "assistant",
                    "content": "Preparing a plan: I'll generate IaC and run terraform init/plan without applying changes.",
                })
                await run_graph(initial_state)
            else:
                await run_graph(initial_state)

        elif cmd["type"] == "message" and initialized:
            content = cmd.get("content", "").strip()
            command = content.split()[0].lower() if content.startswith("/") else ""
            if command in {"/init", "/plan", "/deploy", "/destroy"}:
                await run_action_from_current(command.removeprefix("/"), cmd)
                continue

            # Natural language follow-up — use explicit-only detection to avoid
            # treating casual messages (questions, comments) as action commands.
            explicit_action = _detect_explicit_action(content)
            _, provider_nl, envs_nl = _detect_intent(content)

            # Update tracked provider whenever user mentions one explicitly
            if provider_nl:
                _target_provider = provider_nl

            if explicit_action:
                override_cmd = {
                    **cmd,
                    "environments": envs_nl or None,
                    "provider": provider_nl or None,
                }
                await run_action_from_current(explicit_action, override_cmd)
                continue

            handled = await _handle_followup_message(content, graph, config, emit)
            if handled:
                maybe_state = graph.get_state(config).values
                if maybe_state.get("current_step") == "github_cloned":
                    await run_graph({
                        **maybe_state,
                        "user_message": _last_instruction or content,
                        "messages": [
                            *maybe_state.get("messages", []),
                            {"role": "user", "content": content},
                        ],
                    })
            else:
                emit({
                    "type": "message",
                    "role": "assistant",
                    "content": (
                        "I can continue when you respond to an active prompt "
                        "(confirmation, promotion, credentials, or a GitHub URL)."
                    ),
                })

        elif cmd["type"] == "confirm":
            approved = cmd.get("value", False)
            graph.update_state(config, {"user_approved": approved, "user_modifications": None})
            if approved:
                requested_action = graph.get_state(config).values.get("requested_action") or "deploy"
                # For plan action, skip credential validation — terraform plan may run with
                # local state and the execution node handles any missing-credential errors.
                if requested_action == "plan":
                    emit({"type": "state_change", "state": "planning", "step": "confirmed"})
                    await run_graph(None)
                else:
                    cred_missing = _check_credentials_for_envs(_target_envs, _target_provider, requested_action)
                    if cred_missing:
                        _pending_credentials_action = "confirmed"
                        emit({
                            "type": "waiting_credentials",
                            "provider": cred_missing["provider"],
                            "message": cred_missing["message"],
                            "fields": cred_missing["fields"],
                        })
                    else:
                        ok, validation_msg = await _validate_required_credentials(
                            _target_envs,
                            _target_provider,
                            requested_action,
                        )
                        if not ok:
                            _pending_credentials_action = "confirmed"
                            fields = _credential_fields_for(_target_envs, _target_provider)
                            emit({"type": "error", "message": validation_msg, "recoverable": True})
                            emit({
                                "type": "waiting_credentials",
                                "provider": fields["provider"],
                                "message": validation_msg,
                                "fields": fields["fields"],
                            })
                        else:
                            emit({"type": "message", "role": "assistant", "content": f"✓ {validation_msg}"})
                            emit({"type": "state_change", "state": "planning", "step": "confirmed"})
                            await run_graph(None)
            else:
                emit({"type": "state_change", "state": "idle", "step": "rejected"})

        elif cmd["type"] == "credentials":
            values: dict = cmd.get("values", {})
            for key, value in values.items():
                if key in _ALLOWED_CREDENTIAL_KEYS and value:
                    os.environ[key] = value
            emit({"type": "message", "role": "assistant", "content": "Verifying credentials..."})
            requested_action = graph.get_state(config).values.get("requested_action") or "deploy"
            if _pending_credentials_action == "promote":
                ok, validation_msg = await _validate_provider_credentials(_target_provider)
            else:
                ok, validation_msg = await _validate_required_credentials(
                    _target_envs,
                    _target_provider,
                    requested_action,
                )
            if not ok:
                emit({"type": "error", "message": validation_msg, "recoverable": True})
                cred_missing = _check_credentials_for_envs(_target_envs, _target_provider, requested_action)
                if cred_missing:
                    emit({
                        "type": "waiting_credentials",
                        "provider": cred_missing["provider"],
                        "message": f"{validation_msg}\n\n{cred_missing['message']}",
                        "fields": cred_missing["fields"],
                    })
            else:
                emit({"type": "message", "role": "assistant", "content": f"✓ {validation_msg}"})
                if _pending_credentials_action == "confirmed":
                    _pending_credentials_action = None
                    emit({"type": "state_change", "state": "planning", "step": "confirmed"})
                    await run_graph(None)
                elif _pending_credentials_action == "slash_plan":
                    _pending_credentials_action = None
                    await run_action_from_current("plan", skip_credential_check=True)
                elif _pending_credentials_action == "slash_deploy":
                    _pending_credentials_action = None
                    await run_action_from_current("deploy", skip_credential_check=True)
                elif _pending_credentials_action == "manual_promote":
                    _pending_credentials_action = None
                    await run_action_from_current("deploy", envs_override=["prod"], skip_credential_check=True)
                elif _pending_credentials_action == "promote":
                    _pending_credentials_action = None
                    emit({"type": "state_change", "state": "deploying", "step": "promoting"})
                    await run_graph(None)

        elif cmd["type"] == "promote":
            promote = cmd.get("value", False)
            if _manual_promotion_pending:
                _manual_promotion_pending = False
                if not promote:
                    emit({"type": "state_change", "state": "success", "step": "promote_rejected"})
                    emit({"type": "done"})
                    continue

                _target_envs = ["prod"]
                cred_missing = check_prod_credentials(_target_provider)
                if cred_missing:
                    _pending_credentials_action = "manual_promote"
                    emit({
                        "type": "waiting_credentials",
                        "provider": cred_missing["provider"],
                        "message": cred_missing["message"],
                        "fields": cred_missing["fields"],
                    })
                    continue

                ok, validation_msg = await _validate_provider_credentials(_target_provider)
                if not ok:
                    _pending_credentials_action = "manual_promote"
                    fields = _credential_fields_for(["prod"], _target_provider)
                    emit({"type": "error", "message": validation_msg, "recoverable": True})
                    emit({
                        "type": "waiting_credentials",
                        "provider": fields["provider"],
                        "message": validation_msg,
                        "fields": fields["fields"],
                    })
                    continue

                emit({"type": "message", "role": "assistant", "content": f"✓ {validation_msg}"})
                await run_action_from_current("deploy", envs_override=["prod"], skip_credential_check=True)
                continue

            graph.update_state(config, {"promoted_to_prod": promote})
            if promote:
                cred_missing = check_prod_credentials(_target_provider)
                if cred_missing:
                    _pending_credentials_action = "promote"
                    emit({
                        "type": "waiting_credentials",
                        "provider": cred_missing["provider"],
                        "message": cred_missing["message"],
                        "fields": cred_missing["fields"],
                    })
                else:
                    ok, validation_msg = await _validate_provider_credentials(_target_provider)
                    if not ok:
                        _pending_credentials_action = "promote"
                        fields = _credential_fields_for(["prod"], _target_provider)
                        emit({"type": "error", "message": validation_msg, "recoverable": True})
                        emit({
                            "type": "waiting_credentials",
                            "provider": fields["provider"],
                            "message": validation_msg,
                            "fields": fields["fields"],
                        })
                    else:
                        emit({"type": "message", "role": "assistant", "content": f"✓ {validation_msg}"})
                        emit({"type": "state_change", "state": "deploying", "step": "promoting"})
                        await run_graph(None)
            else:
                emit({"type": "state_change", "state": "success", "step": "promote_rejected"})
                emit({"type": "done"})

        elif cmd["type"] == "cancel":
            emit({"type": "done"})
            return

    emit({"type": "done"})


async def _handle_followup_message(content: str, graph, config: dict, emit) -> bool:
    if not content:
        return False

    if content.startswith("/"):
        _handle_slash_command(content, graph, config, emit)
        return True

    state = graph.get_state(config).values
    if state.get("current_step") != "discovery_needs_github":
        return False

    if not _looks_like_github_url(content):
        emit({
            "type": "message",
            "role": "assistant",
            "content": "I need a valid GitHub URL, e.g. https://github.com/org/repo.",
        })
        return True

    emit({"type": "state_change", "state": "analyzing", "step": "github_clone"})
    emit({"type": "message", "role": "assistant", "content": "Cloning GitHub repository..."})
    try:
        target = await _clone_github_repo(content)
    except Exception as exc:  # noqa: BLE001
        emit({"type": "error", "message": f"Could not clone repository: {exc}", "recoverable": True})
        return True

    graph.update_state(config, {
        "github_url": content,
        "project_path": str(target),
        "current_step": "github_cloned",
        "messages": [
            *state.get("messages", []),
            {"role": "assistant", "content": f"Repository cloned to `{target}`. Continuing analysis..."},
        ],
    })
    return True


async def _clone_github_repo(url: str) -> Path:
    dest = Path(tempfile.mkdtemp(prefix="aetherdeploy-"))
    loop = asyncio.get_event_loop()
    cloned = await loop.run_in_executor(None, GitHubTool().clone_repo, url, dest)
    _register_temp_dir(dest)
    return cloned


# Tracks temporary directories created during this session so they can be
# cleaned up on process exit.
_temp_dirs: list[Path] = []


def _register_temp_dir(path: Path) -> None:
    import atexit
    import shutil

    _temp_dirs.append(path)
    if len(_temp_dirs) == 1:
        def _cleanup() -> None:
            for d in _temp_dirs:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass
        atexit.register(_cleanup)


def _looks_like_github_url(value: str) -> bool:
    return bool(
        value.startswith("https://github.com/")
        or value.startswith("git@github.com:")
    )


def _handle_slash_command(content: str, graph, config: dict, emit) -> None:
    command = content.split()[0].lower()
    if command == "/help":
        emit({
            "type": "message",
            "role": "assistant",
            "content": (
                "Available commands: /help, /status, /init, /plan, /deploy, /destroy.\n"
                "You can also use natural language in English or Spanish.\n"
                "Use /status to see the current step."
            ),
        })
        return
    if command == "/status":
        state = graph.get_state(config).values
        emit({
            "type": "message",
            "role": "assistant",
            "content": f"Current status: {state.get('current_step', 'not started')}",
        })
        return
    if command in {"/init", "/plan", "/deploy", "/destroy"}:
        state = graph.get_state(config).values
        current = state.get("requested_action") or "deploy"
        descriptions = {
            "/init": (
                "Analyses the project and prepares the architecture proposal. "
                f"Current action: {current}; step: {state.get('current_step', 'not started')}."
            ),
            "/plan": (
                "Generates Terraform and runs terraform init/plan against the provider. "
                "No changes are applied."
            ),
            "/deploy": (
                "Approves and applies infrastructure after the proposal. "
                "Use the confirmation button when ready."
            ),
            "/destroy": (
                "Destroys all infrastructure created by AetherDeploy for this project. "
                "Requires confirmation."
            ),
        }
        emit({
            "type": "message",
            "role": "assistant",
            "content": descriptions.get(command, f"Unknown command: {command}"),
        })
        return
    emit({
        "type": "error",
        "message": f"Unknown command: {command}. Try /help.",
        "recoverable": True,
    })




def _step_to_agent_state(step: str) -> str:
    mapping = {
        "start": "analyzing",
        "discovery_done": "analyzing",
        "discovery_needs_github": "idle",
        "analysis_done": "planning",
        "proposal_ready": "idle",
        "confirmed": "planning",
        "needs_revision": "planning",
        "rejected": "idle",
        "generation_done": "deploying",
        "plan_ready": "success",
        "deployed": "success",
        "destroyed": "success",
        "deploy_error": "error",
        "promote_approved": "deploying",
        "promote_rejected": "success",
        "destroying": "deploying",
    }
    return mapping.get(step, "idle")


def _emit_proposal(emit, proposal) -> None:
    emit({
        "type": "proposal",
        "data": {
            "provider": proposal.provider,
            "region": proposal.region,
            "services": [
                {
                    "service": s.service_name,
                    "purpose": s.purpose,
                    "justification": s.justification,
                    "cost": s.estimated_monthly_cost,
                }
                for s in proposal.services
            ],
            "totalCost": proposal.total_estimated_cost,
            "securityNotes": proposal.security_notes,
            "scalabilityNotes": proposal.scalability_notes,
            "environments": [
                {
                    "name": name,
                    "strategy": env.strategy,
                }
                for name, env in proposal.environments.items()
            ],
        },
    })


if __name__ == "__main__":
    app()

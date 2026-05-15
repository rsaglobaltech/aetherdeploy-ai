"""Proposal generation node for AetherDeploy.

This module drives the two-pass LLM architecture:

Pass 1 — Deterministic baseline
    ``provider.recommend_architecture()`` uses decision matrices to select
    services based on detected hints and complexity profile.  This is fast,
    reproducible, and requires no LLM.

Pass 2 — AI cost optimization + review (single LLM call)
    ``_enrich_with_llm()`` sends the baseline proposal to the LLM together
    with the full ``SERVICE_ALTERNATIVES`` catalog.  The LLM:
      a) Replaces each service with the cheapest valid alternative
         (constrained by ``requires`` / ``excludes`` rules in the catalog).
      b) Returns security notes, scalability notes, and a rationale.

    ``_apply_service_overrides()`` patches the proposal in-place and
    recalculates ``total_estimated_cost``.

If the LLM is unavailable the baseline proposal is used unchanged — the
agent never blocks on LLM availability.
"""
from __future__ import annotations

import asyncio
import re

from ...config import get_config
from ...llm.base import LLMBackendFactory
from ...models import ArchitectureProposal, Decision, ServiceRecommendation
from ...observability import log_event
from ...providers import get_provider
from ...providers.aws.services import SERVICE_ALTERNATIVES
from ..prompts import (
    ARCHITECTURE_REVIEW_SYSTEM,
    COST_OPTIMIZE_SYSTEM,
    architecture_review_user_prompt,
    cost_optimize_user_prompt,
)
from ..context import _emitter_var
from ..state import AetherState


# ---------------------------------------------------------------------------
# Node entry point
# ---------------------------------------------------------------------------

async def proposal_node(state: AetherState) -> dict:
    """Generates the architecture proposal using the selected provider.

    Flow
    ----
    1. Build deterministic baseline via provider matrices.
    2. Enrich with LLM: cost-optimize services + add review notes.
    3. Emit proposal and executive summary to the frontend.
    """
    emit = _emitter_var.get()
    provider_name = state.get("preferred_provider") or "aws"
    envs = state.get("target_environments") or ["prod"]
    analysis = state.get("project_analysis")
    topology = state.get("application_topology")

    if analysis is None:
        return {
            "current_step": "proposal_error",
            "errors": [*state.get("errors", []), "No project analysis available"],
        }

    emit({"type": "progress", "step": "proposal_calculate", "percentage": 45})
    emit({"type": "message", "role": "assistant", "content": "Calculating optimal architecture..."})
    provider = get_provider(provider_name)
    proposal = provider.recommend_architecture(analysis, envs, topology=topology)

    emit({
        "type": "message",
        "role": "assistant",
        "content": "Consulting the LLM to cost-optimize and validate the proposal...",
    })
    llm_note, llm_status, optimization_summary = await _enrich_with_llm(
        proposal, state, emit, topology=topology, provider_name=provider_name
    )
    if llm_status:
        emit({"type": "message", "role": "assistant", "content": llm_status})
    if optimization_summary:
        emit({"type": "message", "role": "assistant", "content": optimization_summary})
    if llm_note:
        emit({"type": "message", "role": "assistant", "content": llm_note})

    summary = _format_proposal_summary(provider_name, proposal, analysis, envs)

    # Populate the decision graph from the final proposal + signals/catalog.
    # Additive — does not change selection, only annotates "why" per service.
    proposal.decisions = _build_decisions(proposal, analysis, provider_name)

    assistant_messages = [
        *([{"role": "assistant", "content": llm_status}] if llm_status else []),
        *([{"role": "assistant", "content": optimization_summary}] if optimization_summary else []),
        *([{"role": "assistant", "content": llm_note}] if llm_note else []),
        {"role": "assistant", "content": summary},
    ]
    return {
        "architecture_proposal": proposal,
        "current_step": "proposal_ready",
        "messages": [*state.get("messages", []), *assistant_messages],
    }


def _build_decisions(
    proposal: ArchitectureProposal,
    analysis,
    provider_name: str,
) -> list[Decision]:
    """Derive Decision records from the final proposal + alternatives catalog.

    MEJORAS.md §17.1. For each chosen service we record:
      * which alternatives existed in the catalog,
      * their normalized cost score,
      * which hints excluded an alternative (if any),
      * the project signals that informed the choice.

    Only AWS has a populated ``SERVICE_ALTERNATIVES`` catalog today
    (MEJORAS.md §4.1 calls for GCP/Azure parity). For other providers we still
    emit a minimal Decision so the explain output stays consistent.
    """
    hints = list(getattr(analysis, "infrastructure_hints", []) or [])
    catalog = SERVICE_ALTERNATIVES if provider_name == "aws" else {}
    decisions: list[Decision] = []

    for svc in proposal.services:
        purpose = svc.purpose
        alternatives = catalog.get(purpose, [])
        if not alternatives:
            decisions.append(
                Decision(
                    purpose=purpose,
                    chosen_service=svc.service_name,
                    signals_used=hints[:6],
                    confidence=0.7,
                )
            )
            continue

        # Normalize alternatives by cost_high (lower = better score).
        max_cost = max((alt["cost_high"] for alt in alternatives), default=1) or 1
        considered: list[tuple[str, float, str]] = []
        chosen_score = 1.0
        for alt in alternatives:
            score = alt["cost_high"] / max_cost
            excluded_by = [h for h in alt.get("excludes", []) if h in hints]
            required_missing = [r for r in alt.get("requires", []) if r not in hints]
            if alt["service"] == svc.service_name.split(" (")[0]:
                chosen_score = score
                continue  # the chosen one is not listed as an "alternative considered"
            reason: str
            if excluded_by:
                reason = f"excluded by hints {excluded_by}"
            elif required_missing:
                reason = f"requires missing signals {required_missing}"
            elif score > chosen_score:
                reason = "more expensive than the chosen option"
            else:
                reason = "valid candidate, not selected by optimizer"
            considered.append((alt["service"], score, reason))

        decisions.append(
            Decision(
                purpose=purpose,
                chosen_service=svc.service_name,
                alternatives_considered=considered,
                signals_used=hints[:6],
                constraints_applied=[h for h in hints if h.startswith("requires-") or h in ("gdpr-eu", "hipaa", "pci")],
                confidence=max(0.55, 1.0 - chosen_score / 2),
            )
        )
    return decisions


# ---------------------------------------------------------------------------
# LLM enrichment — cost optimization + architecture review
# ---------------------------------------------------------------------------

async def _enrich_with_llm(
    proposal: ArchitectureProposal,
    state: AetherState,
    emit,
    *,
    topology=None,
    provider_name: str = "aws",
) -> tuple[str | None, str | None, str | None]:
    """Runs the combined cost-optimization and architecture-review LLM pass.

    Parameters
    ----------
    proposal:
        Baseline proposal produced by the provider matrices.  Modified
        **in-place** when service_overrides are applied.
    state:
        Current agent state (provides user_message, hints, environments).
    emit:
        Event emitter callable for streaming status messages.
    topology:
        ``ApplicationTopology`` if available, else ``None``.
    provider_name:
        Cloud provider key; only AWS has a SERVICE_ALTERNATIVES catalog.

    Returns
    -------
    llm_note:
        AI rationale string to display in the chat, or ``None``.
    llm_status:
        One-line status ("LLM active", "LLM unavailable", …).
    optimization_summary:
        Human-readable list of service changes made, or ``None``.
    """
    config = get_config()
    log_event(
        "llm.enrichment.start",
        backend=config.llm_backend,
        model=config.llm_model,
        provider=proposal.provider,
    )

    try:
        backend = LLMBackendFactory.create(
            config.llm_backend,
            model=config.llm_model,
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
        )
    except Exception as exc:
        log_event(
            "llm.enrichment.unavailable",
            level="WARNING",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None, "LLM unavailable; using provider heuristic rules for the proposal.", None

    # Build the alternatives catalog for the active provider.
    # For GCP / Azure we fall back to the legacy review prompt (no catalog yet).
    use_cost_optimize = provider_name == "aws"
    catalog = SERVICE_ALTERNATIVES if use_cost_optimize else {}

    proposed_services = [
        {
            "purpose": s.purpose,
            "service": s.service_name,
            "resource": s.terraform_resource,
            "cost": s.estimated_monthly_cost,
        }
        for s in proposal.services
    ]

    analysis = state.get("project_analysis")
    hints = list(topology.all_hints) if topology is not None else list(
        getattr(analysis, "infrastructure_hints", [])
    )

    if use_cost_optimize:
        system = COST_OPTIMIZE_SYSTEM
        user_content = cost_optimize_user_prompt(
            instruction=state.get("user_message", ""),
            provider=proposal.provider,
            region=proposal.region,
            environments=state.get("target_environments") or ["prod"],
            proposed_services=proposed_services,
            service_catalog=catalog,
            project_hints=hints,
            profile=topology.profile if topology is not None else None,
            service_count=topology.service_count if topology is not None else None,
            language=analysis.primary_language if analysis else "unknown",
            frameworks=analysis.frameworks if analysis else [],
        )
    else:
        system = ARCHITECTURE_REVIEW_SYSTEM
        services_str = ", ".join(f"{s.service_name} ({s.purpose})" for s in proposal.services)
        user_content = architecture_review_user_prompt(
            instruction=state.get("user_message", ""),
            action=state.get("requested_action") or "deploy",
            provider=proposal.provider,
            region=proposal.region,
            environments=state.get("target_environments") or ["prod"],
            services=services_str,
            language=analysis.primary_language if analysis else "unknown",
            frameworks=analysis.frameworks if analysis else [],
            profile=topology.profile if topology is not None else None,
            service_count=topology.service_count if topology is not None else None,
            all_hints=hints or None,
        )

    messages = [{"role": "user", "content": user_content}]

    # Retry loop — honours llm_wait_forever so the agent never silently
    # skips enrichment when the model is temporarily unavailable.
    attempt = 0
    while True:
        attempt += 1
        try:
            log_event(
                "llm.enrichment.waiting",
                backend=config.llm_backend,
                model=config.llm_model,
                attempt=attempt,
            )
            data = await backend.complete_json(messages, system=system)
            break
        except Exception as exc:
            log_event(
                "llm.enrichment.failed",
                level="WARNING",
                attempt=attempt,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            if not config.llm_wait_forever:
                return None, (
                    f"LLM unavailable ({config.llm_backend}/{config.llm_model}); "
                    "using provider heuristic rules for the proposal."
                ), None
            if attempt == 1 or attempt % 5 == 0:
                emit({
                    "type": "message",
                    "role": "assistant",
                    "content": (
                        f"LLM still unavailable ({config.llm_backend}/{config.llm_model}). "
                        "Waiting — start Ollama or download the model if needed."
                    ),
                })
            await asyncio.sleep(config.llm_retry_interval)

    await _close_backend(backend)

    # ------------------------------------------------------------------
    # Apply service overrides (cost-optimization path only)
    # ------------------------------------------------------------------
    optimization_summary: str | None = None
    if use_cost_optimize:
        overrides = data.get("service_overrides") or []
        if isinstance(overrides, list) and overrides:
            changes = _apply_service_overrides(proposal, overrides)
            if changes:
                _recalculate_total_cost(proposal)
                optimization_summary = (
                    "Cost optimization applied:\n"
                    + "\n".join(f"  • {c}" for c in changes)
                )
                log_event(
                    "llm.cost_optimize.applied",
                    changes=changes,
                    new_total=proposal.total_estimated_cost,
                )

    # ------------------------------------------------------------------
    # Apply security / scalability notes (both paths)
    # ------------------------------------------------------------------
    for note in data.get("security_notes", [])[:3]:
        if isinstance(note, str) and note.strip() and note not in proposal.security_notes:
            proposal.security_notes.append(note)
    for note in data.get("scalability_notes", [])[:3]:
        if isinstance(note, str) and note.strip() and note not in proposal.scalability_notes:
            proposal.scalability_notes.append(note)

    rationale = data.get("rationale")
    log_event(
        "llm.enrichment.success",
        backend=config.llm_backend,
        model=config.llm_model,
        overrides_applied=bool(optimization_summary),
        has_rationale=isinstance(rationale, str) and bool(rationale.strip()),
    )

    status = f"LLM active ({config.llm_backend}/{config.llm_model}); proposal optimized."
    note = (
        f"AI rationale: {rationale}"
        if isinstance(rationale, str) and rationale.strip()
        else None
    )
    return note, status, optimization_summary


# ---------------------------------------------------------------------------
# Service override application
# ---------------------------------------------------------------------------

def _apply_service_overrides(
    proposal: ArchitectureProposal, overrides: list[dict]
) -> list[str]:
    """Patches proposal.services in-place with LLM-selected alternatives.

    For each override the LLM emitted:
    - Finds the ``ServiceRecommendation`` whose ``purpose`` matches.
    - Replaces ``service_name``, ``terraform_resource``, ``justification``,
      and ``estimated_monthly_cost``.
    - Records a human-readable change description for the summary.

    The LLM may only override services whose purpose exists in the proposal;
    unrecognised purposes are silently ignored to prevent hallucination
    from adding phantom services.

    Parameters
    ----------
    proposal:
        The proposal to patch (modified in-place).
    overrides:
        List of dicts as returned by the LLM (see ``COST_OPTIMIZE_SYSTEM``).

    Returns
    -------
    List of human-readable change descriptions, empty if nothing changed.
    """
    # Index proposal services by purpose for O(1) lookup.
    by_purpose: dict[str, ServiceRecommendation] = {
        svc.purpose: svc for svc in proposal.services
    }

    changes: list[str] = []
    for override in overrides:
        purpose = override.get("purpose", "").strip()
        new_service = override.get("service", "").strip()
        new_resource = override.get("resource", "").strip()
        new_justification = override.get("justification", "").strip()
        new_cost = override.get("cost_range", "").strip()
        saving = override.get("saving_vs_default", "").strip()

        if not purpose or not new_service:
            continue

        svc = by_purpose.get(purpose)
        if svc is None:
            # LLM referenced a purpose not in the proposal — ignore.
            log_event(
                "llm.cost_optimize.unknown_purpose",
                level="WARNING",
                purpose=purpose,
                new_service=new_service,
            )
            continue

        if svc.service_name == new_service:
            # No change — LLM confirmed the matrix default is already optimal.
            continue

        old_service = svc.service_name
        svc.service_name = new_service
        if new_resource:
            svc.terraform_resource = new_resource
        if new_justification:
            svc.justification = new_justification
        if new_cost:
            svc.estimated_monthly_cost = new_cost

        change = f"{old_service} → {new_service}"
        if saving:
            change += f" ({saving})"
        changes.append(change)

    return changes


def _recalculate_total_cost(proposal: ArchitectureProposal) -> None:
    """Re-derives ``total_estimated_cost`` from the current service list.

    Parses ``estimated_monthly_cost`` strings of the form ``"~$low-high"``
    and sums low/high bounds across all services.  If parsing fails for any
    service its cost is treated as zero (conservative, prevents crashes on
    non-standard LLM cost strings).
    """
    low_total, high_total = 0, 0
    pattern = re.compile(r"\$(\d+)[\-–](\d+)")

    for svc in proposal.services:
        m = pattern.search(svc.estimated_monthly_cost)
        if m:
            low_total += int(m.group(1))
            high_total += int(m.group(2))

    proposal.total_estimated_cost = f"~${low_total}-{high_total}/month"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _close_backend(backend) -> None:
    """Closes the LLM backend connection if it exposes an ``aclose`` method."""
    close = getattr(backend, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:
        pass


def _format_proposal_summary(
    provider_name: str,
    proposal: ArchitectureProposal,
    analysis,
    envs: list[str],
) -> str:
    """Formats the executive summary shown in the chat after the proposal."""
    framework_str = f" + {', '.join(analysis.frameworks)}" if analysis.frameworks else ""
    service_names = ", ".join(s.service_name for s in proposal.services[:4])
    env_text = ", ".join(envs)
    strategies = ", ".join(
        f"{name}:{cfg.strategy}" for name, cfg in proposal.environments.items()
    )
    return (
        "Executive Summary of Proposal\n"
        f"- Stack: {analysis.primary_language}{framework_str} ({analysis.architecture})\n"
        f"- Provider: {provider_name.upper()} in {proposal.region}\n"
        f"- Environments: {env_text}"
        f"{f' ({strategies})' if strategies else ''}\n"
        f"- Main Services: {service_names}\n"
        f"- Estimated Cost: {proposal.total_estimated_cost}\n"
        "Next step: /plan to preview Terraform without applying; "
        "/deploy to apply the chosen environment."
    )

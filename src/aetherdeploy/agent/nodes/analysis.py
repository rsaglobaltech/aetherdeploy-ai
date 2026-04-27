from __future__ import annotations

import dataclasses
from pathlib import Path

from ...analyzers.detector import ProjectDetector
from ...analyzers.llm_enricher import LLMAnalysisEnricher
from ...models import ApplicationTopology
from ..context import _emitter_var
from ..state import AetherState  # noqa: F401 — AetherState no crea ciclo, solo TypedDict

_detector = ProjectDetector()


def _rebuild_topology_with_hints(
    topology: ApplicationTopology, new_hints: list[str]
) -> ApplicationTopology:
    """Returns a new ApplicationTopology with replaced hints and re-scored profile.

    The merged hints (new_hints) reflect the union of signals across ALL services
    after LLM enrichment. Each service's hints are updated to include only the
    signals that were originally attributed to it plus any new global signals —
    keeping per-service signal attribution meaningful without dropping enrichment.
    """
    from ...analyzers.detector import _score_profile

    new_profile = _score_profile(new_hints, topology.service_count)
    new_hint_set = set(new_hints)

    updated_services = []
    for svc in topology.services:
        original_set = set(svc.hints)
        # Merge: keep original per-service hints + add any new global signals
        merged = list(dict.fromkeys(svc.hints + [h for h in new_hints if h not in original_set]))
        updated_services.append(
            type(svc)(
                name=svc.name,
                path_relative=svc.path_relative,
                language=svc.language,
                framework=svc.framework,
                hints=merged,
                ports=svc.ports,
                has_dockerfile=svc.has_dockerfile,
            )
        )

    return ApplicationTopology(
        project_name=topology.project_name,
        profile=new_profile,
        service_count=topology.service_count,
        services=updated_services,
        all_hints=new_hints,
        has_compose=topology.has_compose,
        compose_service_count=topology.compose_service_count,
    )


async def analysis_node(state: AetherState) -> dict:
    """Analiza el proyecto con el detector estático y enriquece con el LLM si hay ambigüedad."""
    emit = _emitter_var.get()
    project_path = state.get("project_path") or "."
    path = Path(project_path)

    emit({"type": "progress", "step": "analysis_scan", "percentage": 18})
    emit({"type": "message", "role": "assistant", "content": "Scanning project files..."})

    # Pass 1: full topology detection (static, sync, fast)
    topology = _detector.detect_topology(path)

    # Pass 2: LLM enrichment (async, gated by heuristics)
    emit({"type": "progress", "step": "analysis_llm", "percentage": 25})
    enricher = LLMAnalysisEnricher()
    enrichment = await enricher.enrich(
        static_hints=topology.all_hints,
        service_count=topology.service_count,
        project_path=path,
    )

    if enrichment.llm_used:
        topology = _rebuild_topology_with_hints(topology, enrichment.hints)
        topology = dataclasses.replace(topology, llm_reasoning=enrichment.reasoning or None)
        if enrichment.reasoning:
            emit({
                "type": "message",
                "role": "assistant",
                "content": f"LLM analysis: {enrichment.reasoning}",
            })

    emit({"type": "progress", "step": "analysis_detect_stack", "percentage": 30})

    analysis = topology.as_project_analysis()
    framework_str = f" + {', '.join(analysis.frameworks)}" if analysis.frameworks else ""
    summary = f"{analysis.primary_language}{framework_str} ({analysis.architecture})"

    emit({
        "type": "analysis_complete",
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
        "llm_used": enrichment.llm_used,
        "confidence": enrichment.confidence,
    })

    return {
        "project_analysis": analysis,
        "application_topology": topology,
        "current_step": "analysis_done",
        "messages": [
            *state.get("messages", []),
            {
                "role": "assistant",
                "content": f"Stack detected: **{summary}** (profile: {topology.profile})",
            },
        ],
    }

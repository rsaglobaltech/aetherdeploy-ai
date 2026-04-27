"""Estado del grafo LangGraph y reexportaciones de modelos."""
from __future__ import annotations

from typing import TypedDict

# Reexporta los modelos desde el módulo central (sin imports circulares)
from ..models import (  # noqa: F401
    ApplicationTopology,
    ArchitectureProposal,
    DeploymentResult,
    EnvironmentConfig,
    LanguageAnalysis,
    ProjectAnalysis,
    ServiceRecommendation,
)


class AetherState(TypedDict, total=False):
    # Input del usuario
    user_message: str
    project_path: str | None
    github_url: str | None
    target_environments: list[str]
    preferred_provider: str | None
    dry_run: bool
    requested_action: str | None  # "init" | "plan" | "deploy"

    # Resultados del análisis
    project_analysis: ProjectAnalysis | None

    # Topología completa del proyecto (nuevo — None si se usa fast path legacy)
    application_topology: ApplicationTopology | None

    # Propuesta de arquitectura
    architecture_proposal: ArchitectureProposal | None

    # HIL — confirmación del usuario
    user_approved: bool | None
    user_modifications: str | None

    # Generación IaC
    terraform_configs: dict[str, str]
    docker_compose: str | None

    # Ejecución
    deployment_result: DeploymentResult | None

    # Promoción a producción (segundo HIL, tras entorno feature)
    promoted_to_prod: bool | None          # None = sin respuesta, True/False = decisión del usuario

    # Conversación y estado interno
    messages: list[dict]
    current_step: str
    errors: list[str]

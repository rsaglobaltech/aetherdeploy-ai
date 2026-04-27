from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ArchitectureProposal, ProjectAnalysis


class CloudProvider(ABC):
    """Contrato que todos los proveedores cloud deben implementar."""

    @abstractmethod
    def recommend_architecture(self, analysis: ProjectAnalysis, environments: list[str]) -> ArchitectureProposal:
        """Mapea un ProjectAnalysis a servicios concretos del proveedor."""

    @abstractmethod
    def get_terraform_backend_config(self, env: str, project_name: str) -> dict:
        """Retorna la config del backend de Terraform para almacenar el estado remoto."""

    def verify_credentials(self) -> bool:
        """Verifica que las credenciales del proveedor están disponibles."""
        return False

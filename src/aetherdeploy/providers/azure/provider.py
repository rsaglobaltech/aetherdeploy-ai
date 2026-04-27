from __future__ import annotations

from ...models import (
    ApplicationTopology,
    ArchitectureProposal,
    EnvironmentConfig,
    ProjectAnalysis,
    ProposalMetadata,
    ServiceRecommendation,
    PROFILE_NANO, PROFILE_MICRO, PROFILE_SMALL,
    PROFILE_STANDARD, PROFILE_LARGE, PROFILE_ENTERPRISE,
)
from ..base import CloudProvider
from .services import COMPUTE_MATRIX, NETWORKING, STORAGE_MATRIX


class AzureProvider(CloudProvider):
    """Proveedor Microsoft Azure."""

    def __init__(self, region: str = "eastus") -> None:
        self.region = region

    def recommend_architecture(
        self,
        analysis: ProjectAnalysis,
        environments: list[str],
        topology: ApplicationTopology | None = None,
    ) -> ArchitectureProposal:
        if topology is not None:
            return self._propose_for_profile(topology, environments)
        return self._propose_legacy(analysis, environments)

    def _propose_legacy(
        self, analysis: ProjectAnalysis, environments: list[str]
    ) -> ArchitectureProposal:
        services: list[ServiceRecommendation] = []
        hints = set(analysis.infrastructure_hints)
        arch = analysis.architecture

        compute = self._select_compute(arch, hints)
        services.append(ServiceRecommendation(
            service_name=compute["service"],
            purpose="compute",
            justification=compute["justification"],
            estimated_monthly_cost=compute["cost"],
            terraform_resource=compute["resource"],
        ))

        services.append(ServiceRecommendation(
            service_name=NETWORKING["service"],
            purpose="networking",
            justification=NETWORKING["justification"],
            estimated_monthly_cost=NETWORKING["cost"],
            terraform_resource=NETWORKING["resource"],
        ))

        if "has-database" in hints:
            db = STORAGE_MATRIX["has-database"]
            services.append(ServiceRecommendation(
                service_name=db["service"], purpose="database",
                justification=db["justification"], estimated_monthly_cost=db["cost"],
                terraform_resource=db["resource"],
            ))

        if "has-cache" in hints:
            cache = STORAGE_MATRIX["has-cache"]
            services.append(ServiceRecommendation(
                service_name=cache["service"], purpose="cache",
                justification=cache["justification"], estimated_monthly_cost=cache["cost"],
                terraform_resource=cache["resource"],
            ))

        return ArchitectureProposal(
            provider="azure",
            region=self.region,
            services=services,
            total_estimated_cost="~$55-310/mes",
            security_notes=[
                "Managed Identity para autenticación sin credenciales en código",
                "Azure Key Vault para secrets y certificados",
                "Network Security Groups con reglas de mínimo acceso",
            ],
            scalability_notes=[
                "Container Apps KEDA scaling basado en métricas HTTP y de colas",
                "PostgreSQL Flexible con réplicas de lectura",
            ],
            environments={
                env: EnvironmentConfig(
                    name=env,
                    strategy="docker" if env == "local" else "ephemeral" if env == "feature" else "terraform",
                )
                for env in environments
            },
        )

    # ------------------------------------------------------------------
    # Profile-aware path
    # ------------------------------------------------------------------

    def _propose_for_profile(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        profile = topology.profile
        if profile == PROFILE_NANO:
            return self._propose_static_site(topology, environments)
        if profile == PROFILE_MICRO:
            return self._propose_container_apps_stateless(topology, environments)
        if profile == PROFILE_SMALL:
            return self._propose_container_apps_with_db(topology, environments)
        if profile == PROFILE_STANDARD:
            return self._propose_container_apps_standard(topology, environments)
        if profile in (PROFILE_LARGE, PROFILE_ENTERPRISE):
            return self._propose_aks(topology, environments)
        return self._propose_legacy(topology.as_project_analysis(), environments)

    def _make_envs(self, environments: list[str]) -> dict[str, EnvironmentConfig]:
        return {
            env: EnvironmentConfig(
                name=env,
                strategy="docker" if env == "local" else "ephemeral" if env == "feature" else "terraform",
            )
            for env in environments
        }

    def _make_metadata(
        self, profile: str, cost_low: int, cost_high: int, deploy_time: str
    ) -> ProposalMetadata:
        return ProposalMetadata(
            profile=profile,
            confidence=0.9,
            deploy_time_estimate=deploy_time,
            cost_low_usd=cost_low,
            cost_high_usd=cost_high,
        )

    def _propose_static_site(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        return ArchitectureProposal(
            provider="azure",
            region=self.region,
            services=[
                ServiceRecommendation(
                    service_name="Azure Blob Storage + CDN",
                    purpose="hosting",
                    justification="Static site: Blob Storage + Azure CDN global",
                    estimated_monthly_cost="~$1-10",
                    terraform_resource="azurerm_storage_account",
                )
            ],
            total_estimated_cost="~$1-10/mes",
            security_notes=["Blob Storage con acceso anónimo deshabilitado, solo vía CDN"],
            scalability_notes=["Azure CDN escala globalmente sin configuración adicional"],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(PROFILE_NANO, 1, 10, "2–3 min"),
            cloud_mappings={"is-frontend-only": "Blob Storage + Azure CDN"},
        )

    def _propose_container_apps_stateless(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        return ArchitectureProposal(
            provider="azure",
            region=self.region,
            services=[
                ServiceRecommendation(
                    service_name="Container Apps",
                    purpose="compute",
                    justification="Servicio sin estado: Container Apps con escala a cero",
                    estimated_monthly_cost="~$0-25",
                    terraform_resource="azurerm_container_app",
                )
            ],
            total_estimated_cost="~$0-25/mes",
            security_notes=[
                "Managed Identity para el Container App",
                "HTTPS con certificado gestionado automáticamente",
            ],
            scalability_notes=["Container Apps escala a cero — sin coste sin tráfico"],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(PROFILE_MICRO, 0, 25, "3–5 min"),
            cloud_mappings={"api-only": "Container Apps"},
        )

    def _propose_container_apps_with_db(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        hints = set(topology.all_hints)
        services = [
            ServiceRecommendation(
                service_name="Container Apps",
                purpose="compute",
                justification="Un servicio con DB: Container Apps serverless",
                estimated_monthly_cost="~$0-25",
                terraform_resource="azurerm_container_app",
            ),
            ServiceRecommendation(
                service_name="PostgreSQL Flexible Server",
                purpose="database",
                justification="PostgreSQL Flexible Server con pausa automática en dev",
                estimated_monthly_cost="~$15-70",
                terraform_resource="azurerm_postgresql_flexible_server",
            ),
            ServiceRecommendation(
                service_name="Key Vault",
                purpose="secrets",
                justification="Credenciales de BD en Key Vault — nunca en variables de entorno",
                estimated_monthly_cost="~$1-5",
                terraform_resource="azurerm_key_vault",
            ),
        ]
        if "has-cache" in hints:
            services.append(ServiceRecommendation(
                service_name="Azure Cache for Redis",
                purpose="cache",
                justification="Cache Redis gestionado detectado en el proyecto",
                estimated_monthly_cost="~$15-60",
                terraform_resource="azurerm_redis_cache",
            ))
        return ArchitectureProposal(
            provider="azure",
            region=self.region,
            services=services,
            total_estimated_cost="~$15-100/mes",
            security_notes=[
                "Managed Identity para Container App",
                "Key Vault para credenciales de BD",
                "Private Endpoint para PostgreSQL",
            ],
            scalability_notes=[
                "Container Apps auto-escala a demanda",
                "PostgreSQL Flexible con storage auto-grow",
            ],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(PROFILE_SMALL, 15, 100, "5–8 min"),
            cloud_mappings={"has-database": "PostgreSQL Flexible", "api-only": "Container Apps"},
        )

    def _propose_container_apps_standard(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        hints = set(topology.all_hints)
        services = [
            ServiceRecommendation(
                service_name="Container Apps (multi-service)",
                purpose="compute",
                justification="2–4 servicios en Container Apps con App Gateway",
                estimated_monthly_cost="~$30-100",
                terraform_resource="azurerm_container_app",
            ),
            ServiceRecommendation(
                service_name="Application Gateway",
                purpose="networking",
                justification="Layer-7 load balancer con WAF básico",
                estimated_monthly_cost="~$20-60",
                terraform_resource="azurerm_application_gateway",
            ),
        ]
        if "has-database" in hints:
            services.append(ServiceRecommendation(
                service_name="PostgreSQL Flexible Server",
                purpose="database",
                justification="Base de datos relacional managed",
                estimated_monthly_cost="~$30-120",
                terraform_resource="azurerm_postgresql_flexible_server",
            ))
        if "has-queue" in hints:
            services.append(ServiceRecommendation(
                service_name="Service Bus",
                purpose="queue",
                justification="Mensajería empresarial asíncrona entre servicios",
                estimated_monthly_cost="~$1-25",
                terraform_resource="azurerm_servicebus_namespace",
            ))
        return ArchitectureProposal(
            provider="azure",
            region=self.region,
            services=services,
            total_estimated_cost="~$80-305/mes",
            security_notes=[
                "Managed Identity por Container App",
                "Key Vault para todas las credenciales",
                "NSG con reglas de mínimo acceso",
            ],
            scalability_notes=[
                "Container Apps KEDA scaling por HTTP y métricas",
                "PostgreSQL Flexible con réplicas de lectura",
            ],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(PROFILE_STANDARD, 80, 305, "8–12 min"),
            cloud_mappings={"multi-service": "Container Apps", "has-database": "PostgreSQL Flexible"},
        )

    def _propose_aks(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        hints = set(topology.all_hints)
        is_enterprise = topology.profile == PROFILE_ENTERPRISE
        services = [
            ServiceRecommendation(
                service_name="AKS",
                purpose="compute",
                justification=f"{topology.service_count} servicios en AKS — Kubernetes gestionado",
                estimated_monthly_cost="~$100-400",
                terraform_resource="azurerm_kubernetes_cluster",
            ),
            ServiceRecommendation(
                service_name="Application Gateway + Ingress",
                purpose="networking",
                justification="AGIC (Application Gateway Ingress Controller) con WAF",
                estimated_monthly_cost="~$30-70",
                terraform_resource="azurerm_application_gateway",
            ),
        ]
        if "has-database" in hints:
            services.append(ServiceRecommendation(
                service_name="PostgreSQL Flexible Server",
                purpose="database",
                justification="Base de datos relacional managed con HA",
                estimated_monthly_cost="~$80-300",
                terraform_resource="azurerm_postgresql_flexible_server",
            ))
        if is_enterprise and "has-payment" in hints:
            services.append(ServiceRecommendation(
                service_name="WAF + DDoS Protection",
                purpose="security",
                justification="WAF obligatorio para proyectos con procesamiento de pagos",
                estimated_monthly_cost="~$30-80",
                terraform_resource="azurerm_web_application_firewall_policy",
            ))
        security_notes = [
            "Workload Identity para pods — sin secretos de Service Principal",
            "Key Vault CSI Driver para secrets en pods",
            "Network Policies entre namespaces",
        ]
        if is_enterprise:
            security_notes.append("WAF en Application Gateway — reglas OWASP habilitadas")
            security_notes.append("Private Endpoints para todos los servicios de datos")
        return ArchitectureProposal(
            provider="azure",
            region=self.region,
            services=services,
            total_estimated_cost="~$210-750/mes",
            security_notes=security_notes,
            scalability_notes=[
                "AKS Cluster Autoscaler gestiona nodos automáticamente",
                "KEDA para event-driven scaling en workloads de cola",
            ],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(topology.profile, 210, 750, "15–25 min"),
            cloud_mappings={
                "has-service-discovery": "AKS CoreDNS + Service",
                "has-payment": "WAF + DDoS Protection",
            },
        )

    def get_terraform_backend_config(self, env: str, project_name: str) -> dict:
        return {
            "backend": "azurerm",
            "resource_group_name": f"{project_name}-tfstate-rg",
            "storage_account_name": f"{project_name}tfstate",
            "container_name": "tfstate",
            "key": f"{project_name}/{env}/terraform.tfstate",
        }

    def verify_credentials(self) -> bool:
        try:
            from azure.identity import DefaultAzureCredential
            DefaultAzureCredential().get_token("https://management.azure.com/.default")
            return True
        except Exception:
            return False

    def _select_compute(self, arch: str, hints: set[str]) -> dict:
        for (matrix_arch, *matrix_hints), service in COMPUTE_MATRIX.items():
            if arch == matrix_arch and set(matrix_hints).intersection(hints):
                return service
        for (matrix_arch, *_), service in COMPUTE_MATRIX.items():
            if arch == matrix_arch:
                return service
        return COMPUTE_MATRIX[("monolith", "api-only")]

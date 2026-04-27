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


class GCPProvider(CloudProvider):
    """Proveedor Google Cloud Platform."""

    def __init__(self, region: str = "us-central1", project_id: str | None = None) -> None:
        self.region = region
        self.project_id = project_id

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
            provider="gcp",
            region=self.region,
            services=services,
            total_estimated_cost="~$50-300/mes",
            security_notes=[
                "Service Accounts con roles de mínimo privilegio",
                "Secret Manager para secrets (no variables de entorno)",
                "VPC Service Controls para perímetro de seguridad",
            ],
            scalability_notes=[
                "Cloud Run escala a cero automáticamente",
                "Cloud SQL con réplicas de lectura para mayor throughput",
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
            return self._propose_cloud_run_stateless(topology, environments)
        if profile == PROFILE_SMALL:
            return self._propose_cloud_run_with_db(topology, environments)
        if profile == PROFILE_STANDARD:
            return self._propose_cloud_run_standard(topology, environments)
        if profile in (PROFILE_LARGE, PROFILE_ENTERPRISE):
            return self._propose_gke_autopilot(topology, environments)
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
            provider="gcp",
            region=self.region,
            services=[
                ServiceRecommendation(
                    service_name="Cloud Storage + Cloud CDN",
                    purpose="hosting",
                    justification="Static site: Cloud Storage + CDN global",
                    estimated_monthly_cost="~$1-10",
                    terraform_resource="google_storage_bucket",
                )
            ],
            total_estimated_cost="~$1-10/mes",
            security_notes=["Bucket privado con acceso solo via Cloud CDN signed URLs"],
            scalability_notes=["Cloud CDN escala globalmente sin configuración"],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(PROFILE_NANO, 1, 10, "2–3 min"),
            cloud_mappings={"is-frontend-only": "Cloud Storage + Cloud CDN"},
        )

    def _propose_cloud_run_stateless(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        return ArchitectureProposal(
            provider="gcp",
            region=self.region,
            services=[
                ServiceRecommendation(
                    service_name="Cloud Run",
                    purpose="compute",
                    justification="Servicio sin estado: Cloud Run serverless, escala a cero",
                    estimated_monthly_cost="~$0-20",
                    terraform_resource="google_cloud_run_v2_service",
                )
            ],
            total_estimated_cost="~$0-20/mes",
            security_notes=[
                "Service Account mínimo para Cloud Run",
                "HTTPS gestionado automáticamente por Cloud Run",
            ],
            scalability_notes=["Cloud Run escala a cero — coste cero sin tráfico"],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(PROFILE_MICRO, 0, 20, "3–5 min"),
            cloud_mappings={"api-only": "Cloud Run"},
        )

    def _propose_cloud_run_with_db(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        hints = set(topology.all_hints)
        services = [
            ServiceRecommendation(
                service_name="Cloud Run",
                purpose="compute",
                justification="Un servicio con DB: Cloud Run serverless",
                estimated_monthly_cost="~$0-20",
                terraform_resource="google_cloud_run_v2_service",
            ),
            ServiceRecommendation(
                service_name="Cloud SQL (serverless)",
                purpose="database",
                justification="Cloud SQL PostgreSQL con pausa automática en entornos dev",
                estimated_monthly_cost="~$10-60",
                terraform_resource="google_sql_database_instance",
            ),
            ServiceRecommendation(
                service_name="Secret Manager",
                purpose="secrets",
                justification="Credenciales de BD inyectadas como secrets",
                estimated_monthly_cost="~$0-5",
                terraform_resource="google_secret_manager_secret",
            ),
        ]
        if "has-cache" in hints:
            services.append(ServiceRecommendation(
                service_name="Memorystore Redis",
                purpose="cache",
                justification="Cache Redis gestionado detectado en el proyecto",
                estimated_monthly_cost="~$15-60",
                terraform_resource="google_redis_instance",
            ))
        return ArchitectureProposal(
            provider="gcp",
            region=self.region,
            services=services,
            total_estimated_cost="~$10-85/mes",
            security_notes=[
                "Service Account con mínimo privilegio",
                "Secret Manager para credenciales de BD",
                "Cloud Run accede a Cloud SQL via Cloud SQL Auth Proxy",
            ],
            scalability_notes=[
                "Cloud Run auto-escala a demanda, escala a cero en dev",
                "Cloud SQL con auto-storage-increase",
            ],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(PROFILE_SMALL, 10, 85, "5–8 min"),
            cloud_mappings={"has-database": "Cloud SQL", "api-only": "Cloud Run"},
        )

    def _propose_cloud_run_standard(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        hints = set(topology.all_hints)
        services = [
            ServiceRecommendation(
                service_name="Cloud Run (multi-service)",
                purpose="compute",
                justification="2–4 servicios en Cloud Run con Load Balancer",
                estimated_monthly_cost="~$20-80",
                terraform_resource="google_cloud_run_v2_service",
            ),
            ServiceRecommendation(
                service_name="Cloud Load Balancing",
                purpose="networking",
                justification="HTTP(S) Load Balancer con backend per-service",
                estimated_monthly_cost="~$10-30",
                terraform_resource="google_compute_backend_service",
            ),
        ]
        if "has-database" in hints:
            services.append(ServiceRecommendation(
                service_name="Cloud SQL",
                purpose="database",
                justification="Base de datos relacional managed",
                estimated_monthly_cost="~$30-120",
                terraform_resource="google_sql_database_instance",
            ))
        if "has-queue" in hints:
            services.append(ServiceRecommendation(
                service_name="Pub/Sub",
                purpose="queue",
                justification="Mensajería asíncrona gestionada entre servicios",
                estimated_monthly_cost="~$1-20",
                terraform_resource="google_pubsub_topic",
            ))
        return ArchitectureProposal(
            provider="gcp",
            region=self.region,
            services=services,
            total_estimated_cost="~$60-250/mes",
            security_notes=[
                "Service Accounts con roles de mínimo privilegio por servicio",
                "Secret Manager para todas las credenciales",
                "VPC Service Controls para perímetro de seguridad",
            ],
            scalability_notes=[
                "Cloud Run escala independientemente cada servicio",
                "Cloud SQL con réplicas de lectura",
            ],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(PROFILE_STANDARD, 60, 250, "8–12 min"),
            cloud_mappings={"multi-service": "Cloud Run", "has-database": "Cloud SQL"},
        )

    def _propose_gke_autopilot(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        hints = set(topology.all_hints)
        is_enterprise = topology.profile == PROFILE_ENTERPRISE
        services = [
            ServiceRecommendation(
                service_name="GKE Autopilot",
                purpose="compute",
                justification=f"{topology.service_count} servicios en GKE Autopilot — Kubernetes gestionado",
                estimated_monthly_cost="~$100-400",
                terraform_resource="google_container_cluster",
            ),
            ServiceRecommendation(
                service_name="Cloud Load Balancing + Ingress",
                purpose="networking",
                justification="Ingress GKE con Cloud Load Balancer y cert-manager",
                estimated_monthly_cost="~$20-50",
                terraform_resource="google_compute_backend_service",
            ),
        ]
        if "has-database" in hints:
            services.append(ServiceRecommendation(
                service_name="Cloud SQL",
                purpose="database",
                justification="Base de datos relacional managed con HA",
                estimated_monthly_cost="~$80-300",
                terraform_resource="google_sql_database_instance",
            ))
        if is_enterprise and "has-payment" in hints:
            services.append(ServiceRecommendation(
                service_name="Cloud Armor",
                purpose="security",
                justification="WAF gestionado obligatorio para proyectos con pagos",
                estimated_monthly_cost="~$20-60",
                terraform_resource="google_compute_security_policy",
            ))
        security_notes = [
            "Workload Identity para autenticación sin claves de Service Account",
            "Secret Manager para todas las credenciales",
            "Network Policies entre namespaces",
        ]
        if is_enterprise:
            security_notes.append("Cloud Armor WAF — reglas OWASP habilitadas")
        return ArchitectureProposal(
            provider="gcp",
            region=self.region,
            services=services,
            total_estimated_cost="~$200-700/mes",
            security_notes=security_notes,
            scalability_notes=[
                "GKE Autopilot gestiona nodos automáticamente",
                "HPA por servicio basado en CPU/métricas custom",
            ],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(topology.profile, 200, 700, "15–25 min"),
            cloud_mappings={"has-service-discovery": "GKE Service + DNS", "has-payment": "Cloud Armor"},
        )

    def get_terraform_backend_config(self, env: str, project_name: str) -> dict:
        return {
            "backend": "gcs",
            "bucket": f"{project_name}-tfstate",
            "prefix": f"{project_name}/{env}",
        }

    def verify_credentials(self) -> bool:
        try:
            import google.auth
            google.auth.default()
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

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
from .services import CDN, COMPUTE_MATRIX, NETWORKING, STORAGE_MATRIX


class AWSProvider(CloudProvider):
    """AWS Provider — selects services using decision matrices."""

    def __init__(self, region: str = "us-east-1") -> None:
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

        # 1 — Compute
        compute = self._select_compute(analysis)
        services.append(ServiceRecommendation(
            service_name=compute["service"],
            purpose="compute",
            justification=compute["justification"],
            estimated_monthly_cost=compute["cost"],
            terraform_resource=compute["resource"],
        ))

        # 2 — Networking
        services.append(ServiceRecommendation(
            service_name=NETWORKING["service"],
            purpose="networking",
            justification=NETWORKING["justification"],
            estimated_monthly_cost=NETWORKING["cost"],
            terraform_resource=NETWORKING["resource"],
        ))

        # 3 — Database
        if "has-database" in analysis.infrastructure_hints:
            db = STORAGE_MATRIX["has-database"]
            services.append(ServiceRecommendation(
                service_name=db["service"],
                purpose="database",
                justification=db["justification"],
                estimated_monthly_cost=db["cost"],
                terraform_resource=db["resource"],
            ))

        # 4 — Cache
        if "has-cache" in analysis.infrastructure_hints:
            cache = STORAGE_MATRIX["has-cache"]
            services.append(ServiceRecommendation(
                service_name=cache["service"],
                purpose="cache",
                justification=cache["justification"],
                estimated_monthly_cost=cache["cost"],
                terraform_resource=cache["resource"],
            ))

        # 5 — CDN (fullstack or if there are assets)
        if "fullstack" in analysis.infrastructure_hints or analysis.has_dockerfile:
            services.append(ServiceRecommendation(
                service_name=CDN["service"],
                purpose="cdn",
                justification=CDN["justification"],
                estimated_monthly_cost=CDN["cost"],
                terraform_resource=CDN["resource"],
            ))

        total = self._estimate_total(services)

        return ArchitectureProposal(
            provider="aws",
            region=self.region,
            services=services,
            total_estimated_cost=total,
            security_notes=[
                "IAM roles with minimum privilege per service",
                "Secrets Manager for credentials (no plaintext environment variables)",
                "Restrictive Security Groups — only necessary ports exposed",
                "ALB with SSL/TLS certificate managed by ACM",
            ],
            scalability_notes=[
                "ECS Service auto-scaling based on CPU and memory",
                "Aurora Serverless automatically scales between 0.5 and 128 ACU",
                "CloudFront caches responses at the edge — reduces load on the origin",
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
    # Profile-aware path (used when ApplicationTopology is available)
    # ------------------------------------------------------------------

    def _propose_for_profile(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        profile = topology.profile
        if profile == PROFILE_NANO:
            return self._propose_static_site(topology, environments)
        if profile == PROFILE_MICRO:
            return self._propose_ecs_stateless(topology, environments)
        if profile == PROFILE_SMALL:
            return self._propose_ecs_with_db(topology, environments)
        if profile == PROFILE_STANDARD:
            return self._propose_ecs_standard(topology, environments)
        if profile in (PROFILE_LARGE, PROFILE_ENTERPRISE):
            return self._propose_ecs_multi_service(topology, environments)
        # Unknown profile — fall back to legacy with derived analysis
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
        self,
        profile: str,
        cost_low: int,
        cost_high: int,
        deploy_time: str,
        confidence: float = 0.9,
    ) -> ProposalMetadata:
        return ProposalMetadata(
            profile=profile,
            confidence=confidence,
            deploy_time_estimate=deploy_time,
            cost_low_usd=cost_low,
            cost_high_usd=cost_high,
        )

    def _propose_static_site(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        services = [
            ServiceRecommendation(
                service_name="S3 + CloudFront",
                purpose="hosting",
                justification="Static site: S3 for files + CloudFront global CDN",
                estimated_monthly_cost="~$1-10",
                terraform_resource="aws_s3_bucket",
            ),
        ]
        return ArchitectureProposal(
            provider="aws",
            region=self.region,
            services=services,
            total_estimated_cost="~$1-10/month",
            security_notes=["Private S3 bucket with access only via CloudFront OAC"],
            scalability_notes=["CloudFront scales globally without additional configuration"],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(PROFILE_NANO, 1, 10, "2–3 min"),
            cloud_mappings={"is-frontend-only": "S3 + CloudFront"},
        )

    def _propose_ecs_stateless(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        services = [
            ServiceRecommendation(
                service_name="ECS Fargate",
                purpose="compute",
                justification="Stateless container service: Fargate with VPC and ALB, no EC2 management",
                estimated_monthly_cost="~$20-60",
                terraform_resource="aws_ecs_cluster",
            ),
            ServiceRecommendation(
                service_name="VPC + ALB",
                purpose="networking",
                justification="Public ALB for traffic ingress; ECS tasks on public subnets",
                estimated_monthly_cost="~$5-20",
                terraform_resource="aws_vpc",
            ),
        ]
        return ArchitectureProposal(
            provider="aws",
            region=self.region,
            services=services,
            total_estimated_cost="~$25-80/month",
            security_notes=[
                "Minimum IAM execution role for ECS tasks",
                "ALB security group restricts access to port 80/443 only",
            ],
            scalability_notes=[
                "ECS Service auto-scales based on CPU/memory",
                "ALB distributes traffic across task replicas",
            ],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(PROFILE_MICRO, 25, 80, "4–6 min"),
            cloud_mappings={"api-only": "ECS Fargate"},
        )

    def _propose_ecs_with_db(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        hints = set(topology.all_hints)
        services = [
            ServiceRecommendation(
                service_name="ECS Fargate",
                purpose="compute",
                justification="Container service with VPC access to Aurora and Secrets Manager",
                estimated_monthly_cost="~$20-60",
                terraform_resource="aws_ecs_cluster",
            ),
            ServiceRecommendation(
                service_name="VPC + ALB",
                purpose="networking",
                justification="Private subnets for DB; public ALB for ingress",
                estimated_monthly_cost="~$5-20",
                terraform_resource="aws_vpc",
            ),
            ServiceRecommendation(
                service_name="RDS Aurora Serverless v2",
                purpose="database",
                justification="Aurora Serverless v2 scales to 0 in dev, no always-on instance",
                estimated_monthly_cost="~$20-80",
                terraform_resource="aws_rds_cluster",
            ),
            ServiceRecommendation(
                service_name="Secrets Manager",
                purpose="secrets",
                justification="DB credentials injected as secrets — never in environment variables",
                estimated_monthly_cost="~$1-5",
                terraform_resource="aws_secretsmanager_secret",
            ),
        ]
        if "has-cache" in hints:
            services.append(ServiceRecommendation(
                service_name="ElastiCache Redis",
                purpose="cache",
                justification="Redis cache detected in the project",
                estimated_monthly_cost="~$15-60",
                terraform_resource="aws_elasticache_cluster",
            ))
        total = self._estimate_total(services)
        return ArchitectureProposal(
            provider="aws",
            region=self.region,
            services=services,
            total_estimated_cost=total,
            security_notes=[
                "IAM execution role with minimum privilege for ECS tasks",
                "Secrets Manager for DB credentials — injected at runtime",
                "Aurora in private subnets — no direct internet access",
            ],
            scalability_notes=[
                "ECS Service auto-scales on demand",
                "Aurora Serverless v2 scales between 0.5 and 8 ACU automatically",
            ],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(PROFILE_SMALL, 46, 165, "6–10 min"),
            cloud_mappings={"has-database": "RDS Aurora Serverless v2", "api-only": "ECS Fargate"},
        )

    def _propose_ecs_standard(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        hints = set(topology.all_hints)
        services = [
            ServiceRecommendation(
                service_name="ECS Fargate",
                purpose="compute",
                justification="2–4 services: ECS Fargate multi-task with Service Connect",
                estimated_monthly_cost="~$40-120",
                terraform_resource="aws_ecs_cluster",
            ),
            ServiceRecommendation(
                service_name="VPC + ALB",
                purpose="networking",
                justification="VPC private with ALB and target groups per service",
                estimated_monthly_cost="~$20-40",
                terraform_resource="aws_vpc",
            ),
        ]
        if "has-database" in hints:
            services.append(ServiceRecommendation(
                service_name="RDS Aurora Serverless v2",
                purpose="database",
                justification="Relational database managed with auto-scaling",
                estimated_monthly_cost="~$50-200",
                terraform_resource="aws_rds_cluster",
            ))
        if "has-cache" in hints:
            services.append(ServiceRecommendation(
                service_name="ElastiCache Redis",
                purpose="cache",
                justification="Redis cache detected in the project",
                estimated_monthly_cost="~$15-80",
                terraform_resource="aws_elasticache_cluster",
            ))
        if "has-queue" in hints:
            services.append(ServiceRecommendation(
                service_name="SQS",
                purpose="queue",
                justification="Queue for messages to decouple services",
                estimated_monthly_cost="~$1-20",
                terraform_resource="aws_sqs_queue",
            ))
        total = self._estimate_total(services)
        return ArchitectureProposal(
            provider="aws",
            region=self.region,
            services=services,
            total_estimated_cost=total,
            security_notes=[
                "IAM roles with minimum privilege per task ECS",
                "Secrets Manager for credentials",
                "Security Groups restrictively between services",
                "ALB with HTTPS and ACM certificate",
            ],
            scalability_notes=[
                "ECS Service auto-scaling by CPU/memoria",
                "ALB distributes traffic between replicas",
                "Aurora Serverless scales automatically",
            ],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(PROFILE_STANDARD, 80, 300, "8–12 min"),
            cloud_mappings={"multi-service": "ECS Fargate", "has-database": "RDS Aurora"},
        )

    def _propose_ecs_multi_service(
        self, topology: ApplicationTopology, environments: list[str]
    ) -> ArchitectureProposal:
        hints = set(topology.all_hints)
        is_enterprise = topology.profile == PROFILE_ENTERPRISE
        services = [
            ServiceRecommendation(
                service_name="ECS Fargate (multi-service)",
                purpose="compute",
                justification=f"{topology.service_count} services in ECS Fargate with Service Connect",
                estimated_monthly_cost="~$100-400",
                terraform_resource="aws_ecs_cluster",
            ),
            ServiceRecommendation(
                service_name="VPC + ALB + Service Connect",
                purpose="networking",
                justification="VPC with private subnets, ALB public, Service Connect for service mesh",
                estimated_monthly_cost="~$30-60",
                terraform_resource="aws_vpc",
            ),
        ]
        if "has-database" in hints:
            services.append(ServiceRecommendation(
                service_name="RDS Aurora Serverless v2",
                purpose="database",
                justification="Relational database managed",
                estimated_monthly_cost="~$80-300",
                terraform_resource="aws_rds_cluster",
            ))
        if "has-cache" in hints:
            services.append(ServiceRecommendation(
                service_name="ElastiCache Redis",
                purpose="cache",
                justification="Distributed cache for sessions and intermediate results",
                estimated_monthly_cost="~$30-100",
                terraform_resource="aws_elasticache_replication_group",
            ))
        if "has-queue" in hints:
            services.append(ServiceRecommendation(
                service_name="SQS + SNS",
                purpose="queue",
                justification="Asynchronous messaging between microservices",
                estimated_monthly_cost="~$5-50",
                terraform_resource="aws_sqs_queue",
            ))
        if is_enterprise and "has-payment" in hints:
            services.append(ServiceRecommendation(
                service_name="WAF + Shield Standard",
                purpose="security",
                justification="WAF mandatory for projects with payment processing (PCI-DSS)",
                estimated_monthly_cost="~$20-60",
                terraform_resource="aws_wafv2_web_acl",
            ))
        total = self._estimate_total(services)
        security_notes = [
            "IAM roles with minimum privilege per task ECS",
            "Secrets Manager for all credentials",
            "Private subnets for DB and cache — no direct internet access",
        ]
        if is_enterprise:
            security_notes.append("WAF active — OWASP Top 10 rules enabled")
            security_notes.append("VPC Flow Logs + CloudTrail for complete auditing")
        return ArchitectureProposal(
            provider="aws",
            region=self.region,
            services=services,
            total_estimated_cost=total,
            security_notes=security_notes,
            scalability_notes=[
                "ECS Service auto-scaling independently per service",
                "Service Connect manages discovery and circuit breaking",
                "Aurora Serverless v2 scales between 0.5 and 128 ACU",
            ],
            environments=self._make_envs(environments),
            metadata=self._make_metadata(
                topology.profile,
                200, 700, "12–20 min",
                confidence=0.85,
            ),
            cloud_mappings={
                "has-service-discovery": "ECS Service Connect",
                "has-payment": "WAF + Shield",
                "multi-service": "ECS Fargate multi-task",
            },
        )

    def get_terraform_backend_config(self, env: str, project_name: str) -> dict:
        bucket = f"{project_name}-tfstate-{env}"
        return {
            "backend": "s3",
            "bucket": bucket,
            "key": f"{project_name}/{env}/terraform.tfstate",
            "region": self.region,
            "encrypt": True,
            "dynamodb_table": f"{project_name}-tflock",
        }

    def verify_credentials(self) -> bool:
        try:
            import boto3
            boto3.Session().client("sts").get_caller_identity()
            return True
        except Exception:
            return False

    def _select_compute(self, analysis: ProjectAnalysis) -> dict:
        hints = set(analysis.infrastructure_hints)
        arch = analysis.architecture

        # Busca la mejor coincidencia en la matriz
        for (matrix_arch, *matrix_hints), service in COMPUTE_MATRIX.items():
            if arch == matrix_arch and set(matrix_hints).intersection(hints):
                return service

        # Fallback: la primera entrada que coincida en arquitectura
        for (matrix_arch, *_), service in COMPUTE_MATRIX.items():
            if arch == matrix_arch:
                return service

        # Default seguro
        return COMPUTE_MATRIX[("monolith", "api-only")]

    def _estimate_total(self, services: list[ServiceRecommendation]) -> str:
        low, high = 0, 0
        for svc in services:
            cost = svc.estimated_monthly_cost.replace("~$", "").replace("/mes", "")
            if "-" in cost:
                parts = cost.split("-")
                low += int(parts[0])
                high += int(parts[1])
            else:
                try:
                    v = int(cost)
                    low += v
                    high += v
                except ValueError:
                    pass
        return f"~${low}-{high}/mes"

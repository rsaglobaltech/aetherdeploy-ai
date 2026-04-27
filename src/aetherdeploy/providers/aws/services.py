"""AWS service selection matrices and AI-optimizable alternatives catalog.

Architecture
------------
Service selection has two layers:

1. **Matrix baseline** (deterministic)
   COMPUTE_MATRIX / STORAGE_MATRIX / NETWORKING / CDN select the default service
   for each purpose based on project architecture and detected hints.  These run
   instantly with no LLM dependency.

2. **Alternatives catalog** (AI-optimizable)
   SERVICE_ALTERNATIVES maps each purpose to an ordered list of ServiceOption
   entries, sorted cheapest-first.  The LLM receives this catalog and the
   project's detected hints, then returns service_overrides to replace the
   matrix-default with the cheapest valid option.

   Validity rules applied by the LLM:
   - An option is EXCLUDED if any hint in its ``excludes`` list is present.
   - An option is EXCLUDED if its ``requires`` list is non-empty and none of
     those hints are present in the project.
   - Among valid options, the LLM selects the one with the lowest ``cost_high``.

Adding a new alternative
------------------------
1. Add a ServiceOption dict to the relevant list in SERVICE_ALTERNATIVES.
2. Ensure the ``resource`` matches a Terraform resource type handled by at
   least one template (or add the template support first).
3. Set ``cost_low`` / ``cost_high`` to realistic USD/month bounds.
4. Populate ``requires`` / ``excludes`` to prevent the option from being
   chosen for incompatible projects.
"""
from __future__ import annotations

from typing import TypedDict


# ---------------------------------------------------------------------------
# Typed definitions
# ---------------------------------------------------------------------------

class ServiceMatrix(TypedDict, total=False):
    """Single entry in a deterministic selection matrix."""
    service: str
    resource: str
    justification: str
    cost: str


class ServiceOption(TypedDict):
    """One selectable alternative inside SERVICE_ALTERNATIVES.

    Attributes
    ----------
    service:
        Human-readable name shown in the ProposalPanel and summary.
    resource:
        Terraform resource type used by the Jinja2 templates (e.g.
        ``"aws_ecs_cluster"``).  Must match a ``compute_resource`` check in
        the relevant template.
    justification:
        One-line explanation of when/why this option is best.  The LLM
        enriches this before presenting it to the user.
    cost_low / cost_high:
        Estimated monthly USD bounds.  Used by the LLM to rank options and
        compute the ``saving_vs_default`` field.
    requires:
        Project hint keys that MUST appear for this option to be valid.
        Empty list means no prerequisite.
    excludes:
        Project hint keys that FORBID this option.  If any of these hints
        is detected in the project, this option is skipped.
    """
    service: str
    resource: str
    justification: str
    cost_low: int
    cost_high: int
    requires: list[str]
    excludes: list[str]


# ---------------------------------------------------------------------------
# Matrix-based defaults (fast, deterministic, no LLM)
# ---------------------------------------------------------------------------

COMPUTE_MATRIX: dict[tuple, ServiceMatrix] = {
    ("monolith", "api-only"): {
        "service": "ECS Fargate",
        "resource": "aws_ecs_cluster",
        "justification": "Serverless containers without EC2 management, ideal for stateless APIs",
        "cost": "~$30-80",
    },
    ("monolith", "fullstack"): {
        "service": "ECS Fargate + CloudFront",
        "resource": "aws_ecs_cluster",
        "justification": "Compute in Fargate with global CDN for static assets",
        "cost": "~$45-120",
    },
    ("monolith", "has-worker"): {
        "service": "ECS Fargate (multi-task)",
        "resource": "aws_ecs_cluster",
        "justification": "One task definition per service (API + worker) in the same cluster",
        "cost": "~$50-150",
    },
    ("microservices", "multi-service"): {
        "service": "ECS Fargate (multi-service)",
        "resource": "aws_ecs_cluster",
        "justification": "Native container orchestration for independent services",
        "cost": "~$80-300",
    },
    ("serverless", "api-only"): {
        "service": "Lambda + API Gateway",
        "resource": "aws_lambda_function",
        "justification": "Zero cost at idle, scales to zero automatically, ideal for variable traffic",
        "cost": "~$0-50",
    },
}

STORAGE_MATRIX: dict[str, ServiceMatrix] = {
    "has-database": {
        "service": "RDS Aurora Serverless v2",
        "resource": "aws_rds_cluster",
        "justification": "Managed database with auto-scaling, compatible with PostgreSQL/MySQL",
        "cost": "~$50-200",
    },
    "has-cache": {
        "service": "ElastiCache Redis",
        "resource": "aws_elasticache_cluster",
        "justification": "Managed in-memory cache with low latency",
        "cost": "~$15-80",
    },
    "static-assets": {
        "service": "S3 + CloudFront",
        "resource": "aws_s3_bucket",
        "justification": "Object storage with global CDN and edge caching",
        "cost": "~$5-30",
    },
}

NETWORKING: ServiceMatrix = {
    "service": "VPC + ALB",
    "resource": "aws_vpc",
    "justification": "Private network with managed load balancer and SSL termination",
    "cost": "~$20-40",
}

CDN: ServiceMatrix = {
    "service": "CloudFront",
    "resource": "aws_cloudfront_distribution",
    "justification": "Global CDN in over 400 edge locations for optimal user experience",
    "cost": "~$5-50",
}


# ---------------------------------------------------------------------------
# Alternatives catalog — the LLM chooses from these to minimize cost
# ---------------------------------------------------------------------------
# Each list is ordered cheapest-first so the LLM's default tie-breaker is
# natural: pick the first option that passes requires/excludes validation.
#
# cost_low / cost_high are in USD/month and deliberately conservative:
# real costs depend on traffic, storage, and region.  They are used for
# relative comparisons and user-facing estimates only.

SERVICE_ALTERNATIVES: dict[str, list[ServiceOption]] = {

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    "compute": [
        {
            "service": "Lambda + API Gateway",
            "resource": "aws_lambda_function",
            "justification": (
                "Scales to zero; best for bursty or low-traffic APIs "
                "with short-lived, stateless request handlers."
            ),
            "cost_low": 0,
            "cost_high": 50,
            "requires": [],
            # Long-running workers, WebSocket connections, and fullstack
            # apps with streaming or server-sent events don't fit Lambda's
            # 15-minute max execution and connection limits.
            "excludes": ["has-worker", "has-websocket", "fullstack"],
        },
        {
            "service": "ECS Fargate",
            "resource": "aws_ecs_cluster",
            "justification": (
                "Serverless containers; ideal for predictable throughput, "
                "long-running processes, and background workers."
            ),
            "cost_low": 20,
            "cost_high": 120,
            "requires": [],
            "excludes": [],
        },
        {
            "service": "ECS Fargate (multi-service)",
            "resource": "aws_ecs_cluster",
            "justification": (
                "Multiple independent containers sharing a cluster with "
                "Service Connect for internal service discovery."
            ),
            "cost_low": 60,
            "cost_high": 300,
            "requires": ["multi-service"],
            "excludes": [],
        },
        {
            "service": "EKS Fargate",
            "resource": "aws_eks_cluster",
            "justification": (
                "Kubernetes for complex service meshes or teams with "
                "existing K8s expertise and tooling."
            ),
            "cost_low": 150,
            "cost_high": 500,
            # Only appropriate when the project already relies on K8s
            # primitives (Helm charts, CRDs, service mesh).
            "requires": ["multi-service", "has-service-discovery"],
            "excludes": [],
        },
    ],

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    "database": [
        {
            "service": "DynamoDB On-Demand",
            "resource": "aws_dynamodb_table",
            "justification": (
                "Pay-per-request NoSQL; scales to zero, zero maintenance, "
                "ideal for key-value or document access patterns."
            ),
            "cost_low": 0,
            "cost_high": 25,
            "requires": [],
            # Projects that use relational schemas (foreign keys, JOINs,
            # migrations) cannot be served by DynamoDB without major
            # application rewrites.
            "excludes": ["has-relational-schema"],
        },
        {
            "service": "RDS PostgreSQL (t4g.micro)",
            "resource": "aws_db_instance",
            "justification": (
                "Classic single-AZ RDS; lower cost than Aurora for light, "
                "steady workloads that don't need auto-scaling."
            ),
            "cost_low": 15,
            "cost_high": 60,
            "requires": ["has-database"],
            "excludes": [],
        },
        {
            "service": "RDS Aurora Serverless v2",
            "resource": "aws_rds_cluster",
            "justification": (
                "Full SQL with auto-scaling from 0.5 ACU; ideal for "
                "variable load and strict SQL/relational requirements."
            ),
            "cost_low": 25,
            "cost_high": 200,
            "requires": ["has-database"],
            "excludes": [],
        },
    ],

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------
    "cache": [
        {
            "service": "ElastiCache Serverless (Redis)",
            "resource": "aws_elasticache_serverless_cache",
            "justification": (
                "Serverless Redis; scales to zero, no cluster management, "
                "pay-per-ECU — best for intermittent cache traffic."
            ),
            "cost_low": 0,
            "cost_high": 60,
            "requires": ["has-cache"],
            "excludes": [],
        },
        {
            "service": "ElastiCache Redis (t4g.micro)",
            "resource": "aws_elasticache_cluster",
            "justification": (
                "Fixed-size Redis cluster; predictable cost for constant, "
                "high-frequency cache traffic where serverless pricing is less efficient."
            ),
            "cost_low": 12,
            "cost_high": 80,
            "requires": ["has-cache"],
            "excludes": [],
        },
    ],

    # ------------------------------------------------------------------
    # Networking
    # ------------------------------------------------------------------
    "networking": [
        {
            "service": "API Gateway (HTTP API)",
            "resource": "aws_apigatewayv2_api",
            "justification": (
                "Serverless HTTP gateway; no VPC or ALB needed, "
                "pay-per-request — pairs naturally with Lambda."
            ),
            "cost_low": 0,
            "cost_high": 15,
            # Only valid when compute is Lambda; containers need ALB for
            # health checks and long-lived TCP connections.
            "requires": [],
            "excludes": ["has-worker", "fullstack"],
        },
        {
            "service": "VPC + ALB",
            "resource": "aws_vpc",
            "justification": (
                "Private VPC with public ALB; standard for container-based "
                "services needing persistent connections and health checks."
            ),
            "cost_low": 16,
            "cost_high": 40,
            "requires": [],
            "excludes": [],
        },
    ],

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------
    "storage": [
        {
            "service": "S3 Standard",
            "resource": "aws_s3_bucket",
            "justification": (
                "Object storage for files, assets, backups — "
                "lowest per-GB cost with no idle overhead."
            ),
            "cost_low": 0,
            "cost_high": 20,
            "requires": [],
            "excludes": [],
        },
    ],

    # ------------------------------------------------------------------
    # Queue / Messaging
    # ------------------------------------------------------------------
    "queue": [
        {
            "service": "SQS",
            "resource": "aws_sqs_queue",
            "justification": (
                "Simple queue; pay-per-message, no idle cost, "
                "best for task/job queues between services."
            ),
            "cost_low": 0,
            "cost_high": 20,
            "requires": ["has-queue"],
            "excludes": [],
        },
        {
            "service": "EventBridge",
            "resource": "aws_cloudwatch_event_bus",
            "justification": (
                "Event bus for event-driven architectures; "
                "integrates natively with many AWS services via rules."
            ),
            "cost_low": 0,
            "cost_high": 15,
            "requires": ["has-queue"],
            "excludes": [],
        },
    ],

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    "security": [
        {
            "service": "WAF + Shield Standard",
            "resource": "aws_wafv2_web_acl",
            "justification": (
                "Web application firewall with OWASP Top 10 managed rules; "
                "required for PCI-DSS and payment processing workloads."
            ),
            "cost_low": 20,
            "cost_high": 60,
            "requires": ["has-payment"],
            "excludes": [],
        },
    ],
}

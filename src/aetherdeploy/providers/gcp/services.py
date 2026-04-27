"""Matrices de selección de servicios GCP por tipo de proyecto."""
from __future__ import annotations

from ..._types import ServiceMatrix

COMPUTE_MATRIX: ServiceMatrix = {
    ("monolith", "api-only"): {
        "service": "Cloud Run",
        "resource": "google_cloud_run_service",
        "justification": "Contenedores serverless gestionados, escala a cero, sin infraestructura que administrar",
        "cost": "~$0-60",
    },
    ("monolith", "fullstack"): {
        "service": "Cloud Run + Cloud CDN",
        "resource": "google_cloud_run_service",
        "justification": "Cómputo serverless con CDN global de Google",
        "cost": "~$10-80",
    },
    ("monolith", "has-worker"): {
        "service": "Cloud Run Jobs",
        "resource": "google_cloud_run_v2_job",
        "justification": "Cloud Run para la API + Cloud Run Jobs para workers batch",
        "cost": "~$20-100",
    },
    ("microservices", "multi-service"): {
        "service": "GKE Autopilot",
        "resource": "google_container_cluster",
        "justification": "Kubernetes gestionado con aprovisionamiento automático de nodos",
        "cost": "~$100-400",
    },
}

STORAGE_MATRIX: ServiceMatrix = {
    "has-database": {
        "service": "Cloud SQL (PostgreSQL)",
        "resource": "google_sql_database_instance",
        "justification": "Base de datos PostgreSQL totalmente gestionada con backups automáticos",
        "cost": "~$30-150",
    },
    "has-cache": {
        "service": "Memorystore (Redis)",
        "resource": "google_redis_instance",
        "justification": "Redis gestionado de alta disponibilidad en la red de Google",
        "cost": "~$10-60",
    },
}

NETWORKING = {
    "service": "Cloud Load Balancing + VPC",
    "resource": "google_compute_network",
    "justification": "Balanceo de carga global con anycast IP y terminación SSL",
    "cost": "~$10-30",
}

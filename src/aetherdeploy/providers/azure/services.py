"""Matrices de selección de servicios Azure por tipo de proyecto."""
from __future__ import annotations

from ..._types import ServiceMatrix

COMPUTE_MATRIX: ServiceMatrix = {
    ("monolith", "api-only"): {
        "service": "Azure Container Apps",
        "resource": "azurerm_container_app",
        "justification": "Serverless containers gestionados, escala a cero, sin Kubernetes que administrar",
        "cost": "~$20-80",
    },
    ("monolith", "fullstack"): {
        "service": "Container Apps + Azure CDN",
        "resource": "azurerm_container_app",
        "justification": "Cómputo serverless con CDN global de Microsoft",
        "cost": "~$30-100",
    },
    ("monolith", "has-worker"): {
        "service": "Container Apps + Azure Service Bus",
        "resource": "azurerm_container_app",
        "justification": "Container Apps para la API + Service Bus para cola de mensajes del worker",
        "cost": "~$40-130",
    },
    ("microservices", "multi-service"): {
        "service": "AKS (Azure Kubernetes Service)",
        "resource": "azurerm_kubernetes_cluster",
        "justification": "Kubernetes gestionado con integración nativa en Azure AD",
        "cost": "~$120-450",
    },
}

STORAGE_MATRIX: ServiceMatrix = {
    "has-database": {
        "service": "Azure Database for PostgreSQL Flexible",
        "resource": "azurerm_postgresql_flexible_server",
        "justification": "PostgreSQL managed con alta disponibilidad y backups automáticos",
        "cost": "~$25-150",
    },
    "has-cache": {
        "service": "Azure Cache for Redis",
        "resource": "azurerm_redis_cache",
        "justification": "Redis Enterprise managed con geo-replicación opcional",
        "cost": "~$15-80",
    },
}

NETWORKING = {
    "service": "Azure Load Balancer + VNet",
    "resource": "azurerm_virtual_network",
    "justification": "Red privada con balanceador de carga Layer-4/7 y terminación SSL",
    "cost": "~$10-30",
}

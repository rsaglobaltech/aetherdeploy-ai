from __future__ import annotations

from .aws.provider import AWSProvider
from .azure.provider import AzureProvider
from .base import CloudProvider
from .gcp.provider import GCPProvider


def get_provider(name: str, **kwargs) -> CloudProvider:
    """Retorna el provider cloud por nombre."""
    providers: dict[str, type[CloudProvider]] = {
        "aws": AWSProvider,
        "gcp": GCPProvider,
        "azure": AzureProvider,
    }
    cls = providers.get(name.lower())
    if cls is None:
        raise ValueError(f"Proveedor desconocido: '{name}'. Opciones: {', '.join(providers)}")
    return cls(**kwargs)


def detect_available_providers() -> list[str]:
    """Detecta qué proveedores tienen credenciales configuradas."""
    available = []
    for name, cls in [("aws", AWSProvider), ("gcp", GCPProvider), ("azure", AzureProvider)]:
        try:
            if cls().verify_credentials():
                available.append(name)
        except Exception:
            pass
    return available


__all__ = ["CloudProvider", "AWSProvider", "GCPProvider", "AzureProvider", "get_provider", "detect_available_providers"]

"""Application secrets detection and IaC (MEJORAS.md §3.3)."""
from .detector import (
    SecretSpec,
    detect_secrets,
    looks_like_secret,
)
from .generator import (
    generate_aws_secrets_tf,
    generate_secrets_tf,
)
from .provisioner import (
    SecretProvisionResult,
    provision_aws_secret_values,
)

__all__ = [
    "SecretProvisionResult",
    "SecretSpec",
    "detect_secrets",
    "generate_aws_secrets_tf",
    "generate_secrets_tf",
    "looks_like_secret",
    "provision_aws_secret_values",
]

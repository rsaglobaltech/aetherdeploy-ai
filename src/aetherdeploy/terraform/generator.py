from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import jinja2

from ..models import ArchitectureProposal, PROFILE_LARGE, PROFILE_ENTERPRISE

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Maps profile values to template tier subdirectory names
_PROFILE_TO_TIER: dict[str, str] = {
    "nano":       "nano",
    "micro":      "micro",
    "small":      "small",
    "standard":   "standard",
    "large":      "large",
    "enterprise": "large",   # enterprise uses the large template (adds WAF via has_service)
}


class TerraformGenerator:
    """Genera ficheros HCL a partir de una ArchitectureProposal usando Jinja2.

    El LLM decide QUÉ servicios usar (a través del provider).
    Jinja2 genera el HCL de forma determinista y verificable.
    """

    def __init__(self) -> None:
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
            undefined=jinja2.StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def generate(
        self,
        proposal: ArchitectureProposal,
        project_name: str,
        environment: str = "prod",
        backend_config: dict | None = None,
    ) -> dict[str, str]:
        """Retorna {filename: hcl_content} para todos los ficheros a escribir."""
        service_names = {svc.purpose for svc in proposal.services}
        compute_svc = next((s for s in proposal.services if s.purpose == "compute"), None)
        exposed_port = 8080

        ctx = {
            "proposal": proposal,
            "project_name": project_name,
            "environment": environment,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "backend": backend_config,
            "exposed_port": exposed_port,
            "compute_resource": compute_svc.terraform_resource if compute_svc else "",
            "has_service": lambda purpose: purpose in service_names,
        }

        # Determine which template to use based on metadata profile
        template_name = self._resolve_template(proposal)
        try:
            template = self._env.get_template(template_name)
        except jinja2.TemplateNotFound:
            # Graceful fallback to the standard template
            fallback = f"{proposal.provider}/main.tf.j2"
            try:
                template = self._env.get_template(fallback)
            except jinja2.TemplateNotFound:
                raise ValueError(f"No hay template para el proveedor '{proposal.provider}'")

        main_tf = template.render(**ctx)

        return {
            "main.tf": main_tf,
            ".terraform-version": "1.9.0\n",
        }

    def _resolve_template(self, proposal: ArchitectureProposal) -> str:
        """Returns the template path to use for this proposal."""
        profile = proposal.metadata.profile if proposal.metadata else None
        if profile is not None:
            tier = _PROFILE_TO_TIER.get(profile)
            if tier:
                tiered_path = f"{proposal.provider}/tiers/{tier}/main.tf.j2"
                # Check that the tiered template file actually exists
                full_path = _TEMPLATES_DIR / proposal.provider / "tiers" / tier / "main.tf.j2"
                if full_path.exists():
                    return tiered_path
        # No metadata or no matching tier — fall back to legacy provider template
        return f"{proposal.provider}/main.tf.j2"

    def write(
        self,
        configs: dict[str, str],
        output_dir: Path,
    ) -> None:
        """Escribe los ficheros generados en output_dir."""
        output_dir.mkdir(parents=True, exist_ok=True)
        for filename, content in configs.items():
            (output_dir / filename).write_text(content, encoding="utf-8")

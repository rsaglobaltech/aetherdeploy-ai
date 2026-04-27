from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..agent.state import ArchitectureProposal, DeploymentResult, ProjectAnalysis

console = Console()


class Renderer:
    """Centralizes all Rich output for the classic CLI mode."""

    def render_analysis(self, analysis: ProjectAnalysis) -> None:
        table = Table(title="Project Analysis", show_header=True)
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Primary Language", analysis.primary_language)
        table.add_row("Frameworks", ", ".join(analysis.frameworks) or "—")
        table.add_row("Architecture", analysis.architecture)
        table.add_row("Ports", ", ".join(str(p) for p in analysis.exposed_ports) or "—")
        table.add_row("Dockerfile", "✓" if analysis.has_dockerfile else "✗")
        table.add_row("Tests", "✓" if analysis.has_tests else "✗")
        console.print(table)

    def render_proposal(self, proposal: ArchitectureProposal) -> None:
        table = Table(title=f"Proposal: [bold magenta]{proposal.provider.upper()}[/] ({proposal.region})")
        table.add_column("Service", style="white bold")
        table.add_column("Purpose", style="cyan")
        table.add_column("Justification", style="dim")
        table.add_column("Cost/month", style="green")
        for svc in proposal.services:
            table.add_row(svc.service_name, svc.purpose, svc.justification, svc.estimated_monthly_cost)
        console.print(table)
        console.print(f"  Total Estimated: [bold green]{proposal.total_estimated_cost}[/]")

    def render_deployment_result(self, result: DeploymentResult) -> None:
        status = "[bold green]✓ Completed[/]" if result.success else "[bold red]✗ Failed[/]"
        lines = [f"Status: {status}"]
        for endpoint in result.endpoints:
            lines.append(f"  URL: [link]{endpoint}[/link]")
        console.print(Panel("\n".join(lines), title="Deployment Result", border_style="green" if result.success else "red"))

    def render_message(self, role: str, content: str) -> None:
        if role == "assistant":
            console.print(f"[magenta]◈[/] {content}")
        elif role == "user":
            console.print(f"[cyan]▸[/] {content}")

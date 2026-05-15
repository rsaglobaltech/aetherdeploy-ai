"""Render the decision graph of an ArchitectureProposal in human form.

MEJORAS.md §17.1: every service recommendation should be traceable to the
signals that drove it. The output of :func:`render_markdown` is suitable as an
Architecture Decision Record (ADR) — users can commit it next to their IaC.
"""
from __future__ import annotations

from .models import ArchitectureProposal, Decision


def render_markdown(proposal: ArchitectureProposal) -> str:
    """Return a markdown document explaining every decision in the proposal."""
    lines: list[str] = []
    lines.append(f"# Architecture decision record — {proposal.provider}/{proposal.region}")
    lines.append("")
    if proposal.metadata:
        lines.append(
            f"**Profile:** {proposal.metadata.profile}  "
            f"**Confidence:** {proposal.metadata.confidence:.2f}  "
            f"**Deploy ETA:** {proposal.metadata.deploy_time_estimate}"
        )
        lines.append(
            f"**Cost range:** ${proposal.metadata.cost_low_usd}–${proposal.metadata.cost_high_usd}/month"
        )
        lines.append("")
    if proposal.total_estimated_cost:
        lines.append(f"**Total estimated cost:** {proposal.total_estimated_cost}")
        lines.append("")

    if not proposal.decisions:
        lines.append("_No decision metadata recorded for this proposal._")
        return "\n".join(lines)

    lines.append("## Decisions")
    lines.append("")
    for decision in proposal.decisions:
        lines.extend(_render_decision(decision))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_decision(d: Decision) -> list[str]:
    out = [f"### {d.purpose} → **{d.chosen_service}** (confidence {d.confidence:.2f})"]
    if d.signals_used:
        out.append("- **Signals used:** " + ", ".join(f"`{s}`" for s in d.signals_used))
    if d.constraints_applied:
        out.append("- **Constraints applied:** " + ", ".join(f"`{c}`" for c in d.constraints_applied))
    if d.alternatives_considered:
        out.append("- **Alternatives considered:**")
        for name, score, reason in d.alternatives_considered:
            out.append(f"  - `{name}` (score {score:.2f}) — {reason}")
    return out


def render_text(proposal: ArchitectureProposal) -> str:
    """Compact plain-text rendering for terminal output (no markdown)."""
    lines: list[str] = [f"Proposal — {proposal.provider}/{proposal.region}"]
    if proposal.total_estimated_cost:
        lines.append(f"  cost: {proposal.total_estimated_cost}")
    if not proposal.decisions:
        lines.append("  (no decision metadata available)")
        return "\n".join(lines)
    for d in proposal.decisions:
        lines.append("")
        lines.append(f"  {d.purpose} = {d.chosen_service}  (confidence {d.confidence:.2f})")
        if d.signals_used:
            lines.append(f"    signals    : {', '.join(d.signals_used)}")
        if d.constraints_applied:
            lines.append(f"    constraints: {', '.join(d.constraints_applied)}")
        for name, score, reason in d.alternatives_considered:
            lines.append(f"    - alt {name:30s} score {score:.2f}  ({reason})")
    return "\n".join(lines)

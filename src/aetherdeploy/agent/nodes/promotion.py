"""Production promotion node — second HIL after a feature/LocalStack deployment.

The graph interrupts *before* this node (interrupt_before=["promotion"]) so
that the caller (CLI or SDK) can ask the user:

    "Your app works in the feature environment (LocalStack).
     Promote to real production? (y/n)"

If the user approves → current_step = "promote_approved" → the graph routes
to execution_node for the prod environment.

If the user rejects → current_step = "promote_rejected" → the graph ends.
"""
from __future__ import annotations

from ..state import AetherState


async def promotion_node(state: AetherState) -> dict:
    """Processes the promotion decision the user made after the interrupt.

    This node runs *after* the interrupt, once the caller has
    updated the state with ``promoted_to_prod=True/False``.
    """
    promoted = state.get("promoted_to_prod")

    if promoted is True:
        return {
            "current_step": "promote_approved",
            "target_environments": ["prod"],
            "messages": [
                *state.get("messages", []),
                {
                    "role": "assistant",
                    "content": (
                        "Promotion to production approved. "
                        "Starting deployment with Terraform against real AWS..."
                    ),
                },
            ],
        }

    # Rejected or no response → terminate
    return {
        "current_step": "promote_rejected",
        "messages": [
            *state.get("messages", []),
            {
                "role": "assistant",
                "content": (
                    "Promotion canceled. The feature environment (LocalStack) remains active. "
                    "Run 'aetherdeploy promote' when you are ready to move to production."
                ),
            },
        ],
    }

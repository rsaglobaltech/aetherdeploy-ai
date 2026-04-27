from __future__ import annotations

from ..state import AetherState


async def confirmation_node(state: AetherState) -> dict:
    """Processes the user's response after the HIL interrupt.

    This node runs AFTER the user has responded.
    The response arrives via graph.update_state() from the CLI or SDK.
    """
    user_approved = state.get("user_approved")
    user_modifications = state.get("user_modifications")

    if user_modifications:
        return {
            "current_step": "needs_revision",
            "user_approved": False,
            "messages": [
                *state.get("messages", []),
                {"role": "assistant", "content": f"Understood, reviewing the proposal: {user_modifications}"},
            ],
        }

    if user_approved:
        return {
            "current_step": "confirmed",
            "messages": [
                *state.get("messages", []),
                {"role": "assistant", "content": "Plan approved. Generating infrastructure configuration…"},
            ],
        }

    return {
        "current_step": "rejected",
        "messages": [
            *state.get("messages", []),
            {"role": "assistant", "content": "Plan canceled. You can describe a new deployment whenever you want."},
        ],
    }

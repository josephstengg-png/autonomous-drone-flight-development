"""Which tools the assistant may use, decided by code, not by the model.

Phase B (advise only): "read" and "draft" tools. "act" tools (ARM ALL, START ALL, stop) arrive in Phase C, and then
only inside one run the operator has authorised and delegated; stopping will be allowed without delegation.
A 4B model will sometimes ask for a tool that does not exist, or one it may not use: that request is refused, logged
and reported back to the model as a refusal, and it never reaches the console.
"""
from __future__ import annotations

from artex.assistant import tools

PHASE_KINDS = {"B": {"read", "draft"}}
MAX_STEPS = 6                      # tool rounds per question
MAX_CALLS_PER_STEP = 4


def allowed_kinds(phase: str = "B") -> set[str]:
    return PHASE_KINDS.get(phase, {"read"})


def check(name: str, phase: str = "B") -> str | None:
    """None if the call may go ahead, else the refusal text."""
    if name not in tools.TOOLS:
        return f"refused: there is no tool called {name!r}"
    kind = tools.TOOLS[name][0]
    if kind not in allowed_kinds(phase):
        return f"refused: {name} is an '{kind}' tool and the assistant is advise-only (Phase {phase})"
    return None

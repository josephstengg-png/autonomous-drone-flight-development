"""Which tools the assistant may use, decided by code, not by the model.

Phase B (advise only): "read" and "draft" tools. "act" tools (ARM ALL, START ALL, stop) arrive in Phase C, and then
only inside one run the operator has authorised and delegated; stopping will be allowed without delegation.
A 4B model will sometimes ask for a tool that does not exist, or one it may not use: that request is refused, logged
and reported back to the model as a refusal, and it never reaches the console.
"""
from __future__ import annotations

from artex.assistant import tools

PHASE_KINDS = {"B": {"read", "draft"}, "C": {"read", "draft", "stop"}}   # "act" is added per question, only with a delegation
MAX_STEPS = 6                      # tool rounds per question
MAX_CALLS_PER_STEP = 4


def allowed_kinds(phase: str = "B", act_ok: bool = False) -> set[str]:
    kinds = set(PHASE_KINDS.get(phase, {"read"}))
    if phase == "C" and act_ok:
        kinds.add("act")
    return kinds


def check(name: str, phase: str = "B", available: dict | None = None, act_ok: bool = False) -> str | None:
    """None if the call may go ahead, else the refusal text."""
    registry = available if available is not None else tools.TOOLS
    if name not in registry:
        return f"refused: there is no tool called {name!r}"
    kind = registry[name][0]
    if kind not in allowed_kinds(phase, act_ok):
        if kind == "act":
            return f"refused: {name} needs the operator's delegated authorisation for this run"
        return f"refused: {name} is an '{kind}' tool and the assistant is advise-only (Phase {phase})"
    return None

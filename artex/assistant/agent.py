"""The assistant's loop: question -> model -> (allowed) tool calls -> results -> ... -> answer.

Everything the model asks for is checked by artex.assistant.policy and logged in the reply, so the operator can see
exactly what it looked at and what it refused. Proposals (drafted profiles, planner settings) are returned for the
console to offer as "apply to the form"; nothing is saved, started or sent to an aircraft from here.
"""
from __future__ import annotations

import json
import re
import time

from artex.assistant import policy, tools
from artex.assistant.llm import LLMError, OllamaChat

SYSTEM = """You are the ARTEX ground-station assistant for a search-and-rescue drone team.
You help the operator understand runs, missions and profiles, and you draft profiles and planner settings.
Rules:
- You are ADVISE-ONLY. You cannot arm, start, stop, upload, save or change anything. Never say you did. If asked to
  act, say what the operator should do in the console instead, and offer a draft if one helps.
- Use the tools to look things up; do not guess run ids, profile names, areas or numbers. Quote run ids you used.
- Drafts (propose_*) are proposals: tell the operator to review and apply them in the console.
- The pilots' handsets are the authority (ch5 OFF takes over, ch8 kill held 5 s stops). Never suggest bypassing a
  BLOCK, a preflight, a pilot, or the confirmation.
- Be brief: a few sentences or a short list.
Facts about control (state them exactly, never improvise others):
- Each aircraft has one named pilot with its own handset. ch5 OFF = the pilot takes over. ch8 kill HELD 5 s = stop the
  motors (a shorter press leaves it armed). There is no cockpit; pilots stand on the ground.
- Arming and starting are done in the console after the operator's authorisation (press-and-hold ARM / ARM ALL,
  START ALL). Do not describe handset arming procedures."""


SYSTEM_C = """You are the ARTEX ground-station assistant for a search-and-rescue drone team.
You can look things up, draft, STOP aircraft, and, only when the operator has delegated an authorised run to you,
start that run with execute_authorised_run (the console then presses each gate when it is due).
Rules:
- Use execute_authorised_run only when the operator asks you to fly / execute / start the authorised run. If it is
  refused, tell the operator why in one sentence; never try a workaround.
- If the operator says stop / abort / hold / come back / land, call stop_aircraft at once (rtl for "come back").
- Never say something happened unless a tool result says so. Quote the run id.
- The pilots' handsets are the authority (ch5 OFF takes over, ch8 kill held 5 s stops).
- Be brief.
Facts about control (state them exactly, never improvise others):
- Each aircraft has one named pilot with its own handset. ch5 OFF = the pilot takes over. ch8 kill HELD 5 s = stop the
  motors (a shorter press leaves it armed). There is no cockpit; pilots stand on the ground.
- Arming and starting are done in the console after the operator's authorisation (press-and-hold ARM / ARM ALL,
  START ALL). Do not describe handset arming procedures."""


# Stopping must not depend on the model getting its output format right (12 Nov 2026 evaluation: Qwen3-4B wrote
# `stop_aircraft({...})` as text instead of calling it). A clear stop request goes straight to the stop tool in code.
STOP_WORDS = [(re.compile(r"\b(come back|come home|go home|return( to (launch|home|base))?|rtl|abort)\b", re.I), "rtl"),
              (re.compile(r"\b(land( (now|here|them|it|both|all))?)\b", re.I), "land"),
              (re.compile(r"\b(stop|hold|halt|freeze|pause)\b", re.I), "hold")]
QUESTION = re.compile(r"\?\s*$|^\s*(why|what|how|when|where|which|who|did|does|do|is|are|was|were|can|could|should|would|will)\b", re.I)
NEGATION = re.compile(r"\b(don'?t|do not|never|no need to|not yet|without|cancel the|unless)\b", re.I)
TEXT_CALL = re.compile(r"^\s*`*\s*([a-z_]+)\s*\(\s*(\{.*\})?\s*\)\s*`*\s*$", re.S)


def stop_intent(question: str) -> str | None:
    """'rtl' | 'land' | 'hold' for a clear stop request, else None (negated or ambiguous requests go to the model)."""
    if NEGATION.search(question) or QUESTION.search(question):
        return None          # a question about stopping, or a negated request: the model reads it (and may still stop)
    for pat, action in STOP_WORDS:
        if pat.search(question):
            return action
    return None


def text_tool_call(content: str, registry: dict) -> dict | None:
    """A tool call the model wrote as plain text, e.g. `stop_aircraft({"action": "rtl"})`, for a tool that exists."""
    m = TEXT_CALL.match(content or "")
    if not m or m.group(1) not in registry:
        return None
    try:
        args = json.loads(m.group(2)) if m.group(2) else {}
    except ValueError:
        return None
    return dict(name=m.group(1), arguments=args if isinstance(args, dict) else {})


def ask(question: str, context: dict | None = None, llm=None, phase: str = "B", extra_tools: dict | None = None,
        act_ok: bool = False, delegation: dict | None = None) -> dict:
    llm = llm or OllamaChat()
    registry = dict(tools.TOOLS, **(extra_tools or {}))
    action = stop_intent(question) if phase == "C" and "stop_aircraft" in registry else None
    if action:
        result = tools.run_tool("stop_aircraft", dict(action=action), registry)
        out = json.loads(result)
        said = (f"{action.upper()} sent to " + ", ".join(f"ARTEX{s}" for s in out.get("sent") or []) if out.get("sent")
                else out.get("error") or f"{action.upper()} sent")
        return dict(answer=f"{said}. (Stop requests go straight to the aircraft; they do not wait for the model.)",
                    calls=[dict(tool="stop_aircraft", arguments=dict(action=action), refused=None, result=result[:400],
                                direct=True)], proposals=[], steps=0, seconds=0.0, model="(direct stop)")
    t0 = time.time()
    msgs = [dict(role="system", content=SYSTEM_C if phase == "C" else SYSTEM)]
    if context:
        msgs.append(dict(role="system", content="Console context: " + json.dumps(context, default=str)[:1500]))
    if phase == "C":
        msgs.append(dict(role="system", content=(
            f"Delegation: VALID. The operator ({delegation.get('operator')}) authorised AND delegated the {delegation.get('kind')} "
            f"run '{delegation.get('profile', {}).get('name')}' to you. If they ask you to fly, execute, start or run it, call "
            "execute_authorised_run now." if act_ok and delegation else
            "Delegation: NONE. You cannot start any run; the operator must authorise with 'delegate the gates to the "
            "assistant' first. You can still stop aircraft.")))
    msgs.append(dict(role="user", content=question))
    schemas = tools.schemas(policy.allowed_kinds(phase, act_ok), registry)
    calls, proposals = [], []
    try:
        for step in range(policy.MAX_STEPS):
            out = llm.chat(msgs, schemas)
            if not out["tool_calls"]:
                written = text_tool_call(out["content"], registry)
                if written:
                    out = dict(content="", tool_calls=[written])
            if not out["tool_calls"]:
                return dict(answer=out["content"] or "(no answer)", calls=calls, proposals=proposals,
                            steps=step + 1, seconds=round(time.time() - t0, 1), model=getattr(llm, "model", "?"))
            msgs.append(dict(role="assistant", content=out["content"] or "",
                             tool_calls=[dict(function=dict(name=c["name"], arguments=c["arguments"]))
                                         for c in out["tool_calls"]]))
            for c in out["tool_calls"][:policy.MAX_CALLS_PER_STEP]:
                refusal = policy.check(c["name"], phase, registry, act_ok)
                result = json.dumps(dict(error=refusal)) if refusal else tools.run_tool(c["name"], c["arguments"], registry)
                calls.append(dict(tool=c["name"], arguments=c["arguments"], refused=refusal,
                                  result=result[:400] + ("..." if len(result) > 400 else "")))
                if not refusal and registry[c["name"]][0] == "draft":
                    try:
                        proposals.append(json.loads(result))
                    except ValueError:
                        pass
                msgs.append(dict(role="tool", content=result, tool_name=c["name"]))
        return dict(answer="I stopped after the maximum number of lookups. Please ask a narrower question.",
                    calls=calls, proposals=proposals, steps=policy.MAX_STEPS, seconds=round(time.time() - t0, 1),
                    model=getattr(llm, "model", "?"))
    except LLMError as exc:
        return dict(answer=None, error=str(exc), calls=calls, proposals=proposals, seconds=round(time.time() - t0, 1),
                    model=getattr(llm, "model", "?"))

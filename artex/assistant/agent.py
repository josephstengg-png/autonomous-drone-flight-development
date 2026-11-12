"""The assistant's loop: question -> model -> (allowed) tool calls -> results -> ... -> answer.

Everything the model asks for is checked by artex.assistant.policy and logged in the reply, so the operator can see
exactly what it looked at and what it refused. Proposals (drafted profiles, planner settings) are returned for the
console to offer as "apply to the form"; nothing is saved, started or sent to an aircraft from here.
"""
from __future__ import annotations

import json
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
- Be brief: a few sentences or a short list."""


def ask(question: str, context: dict | None = None, llm=None, phase: str = "B") -> dict:
    llm = llm or OllamaChat()
    t0 = time.time()
    msgs = [dict(role="system", content=SYSTEM)]
    if context:
        msgs.append(dict(role="system", content="Console context: " + json.dumps(context, default=str)[:1500]))
    msgs.append(dict(role="user", content=question))
    schemas = tools.schemas(policy.allowed_kinds(phase))
    calls, proposals = [], []
    try:
        for step in range(policy.MAX_STEPS):
            out = llm.chat(msgs, schemas)
            if not out["tool_calls"]:
                return dict(answer=out["content"] or "(no answer)", calls=calls, proposals=proposals,
                            steps=step + 1, seconds=round(time.time() - t0, 1), model=getattr(llm, "model", "?"))
            msgs.append(dict(role="assistant", content=out["content"] or "",
                             tool_calls=[dict(function=dict(name=c["name"], arguments=c["arguments"]))
                                         for c in out["tool_calls"]]))
            for c in out["tool_calls"][:policy.MAX_CALLS_PER_STEP]:
                refusal = policy.check(c["name"], phase)
                result = json.dumps(dict(error=refusal)) if refusal else tools.run_tool(c["name"], c["arguments"])
                calls.append(dict(tool=c["name"], arguments=c["arguments"], refused=refusal,
                                  result=result[:400] + ("..." if len(result) > 400 else "")))
                if not refusal and tools.TOOLS[c["name"]][0] == "draft":
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

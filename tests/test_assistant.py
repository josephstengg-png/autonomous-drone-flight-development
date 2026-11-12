"""Phase B assistant: the loop, the policy and the tools, with a scripted stand-in for the model (no model needed)."""
import json

import pytest

from artex.assistant import agent, policy, tools
from artex.assistant.llm import LLMError, OllamaChat


class Scripted:
    """Plays back model turns: each item is either a final answer (str) or a list of tool calls [(name, args)]."""
    model = "scripted"

    def __init__(self, turns):
        self.turns, self.seen = list(turns), []

    def chat(self, messages, tools_):
        self.seen.append((messages, tools_))
        t = self.turns.pop(0)
        if isinstance(t, str):
            return dict(content=t, tool_calls=[])
        return dict(content="", tool_calls=[dict(name=n, arguments=a) for n, a in t])


def test_read_tools_run_and_their_results_go_back_to_the_model():
    llm = Scripted([[("list_areas", {})], "There are several saved areas, including seletar-west-camp."])
    r = agent.ask("What areas do we have?", llm=llm)
    assert r["answer"].startswith("There are") and r["calls"][0]["tool"] == "list_areas" and not r["calls"][0]["refused"]
    tool_msg = [m for m in llm.seen[1][0] if m["role"] == "tool"][0]
    assert "seletar-west-camp" in tool_msg["content"]


def test_the_model_is_only_offered_read_and_draft_tools():
    llm = Scripted(["ok"])
    agent.ask("hi", llm=llm)
    offered = {t["function"]["name"] for t in llm.seen[0][1]}
    assert offered == {n for n, t in tools.TOOLS.items() if t[0] in ("read", "draft")}
    assert not any(t[0] == "act" for t in tools.TOOLS.values())          # no act tools exist in Phase B


def test_a_tool_it_may_not_use_is_refused_logged_and_never_runs():
    llm = Scripted([[("arm_all", {"token": "x"}), ("start_all", {})], "I cannot arm; press ARM ALL in the Fleet page."])
    r = agent.ask("Arm both drones now", llm=llm)
    assert [c["refused"] for c in r["calls"]] == ["refused: there is no tool called 'arm_all'",
                                                   "refused: there is no tool called 'start_all'"]
    assert policy.check("propose_fleet_profile") is None
    assert "'draft' tool" in policy.check("propose_fleet_profile", phase="read-only")   # unknown phase = read only


def test_a_directive_becomes_a_validated_planner_proposal_and_bad_numbers_are_flagged():
    llm = Scripted([[("propose_planner_settings", dict(kml="ops/seletar-west-camp.kml", drones=2, altitude_m=30, scan="lawnmower"))],
                    "Here are the settings: 2 drones at 30 m."])
    r = agent.ask("Survey Seletar with 2 drones at 30 m", llm=llm)
    p = r["proposals"][0]
    assert p["proposal"] == "planner_settings" and p["errors"] == [] and p["drones"] == 2
    bad = json.loads(tools.run_tool("propose_planner_settings", dict(kml="ops/seletar-west-camp.kml", drones=2, altitude_m=500)))
    assert any("Altitude" in e for e in bad["errors"])


def test_drafted_profiles_are_validated_but_not_saved(tmp_path, monkeypatch):
    from artex.console import gs_fleet as fleet
    monkeypatch.setattr(fleet, "FLEET_DIR", tmp_path / "fleet")
    out = json.loads(tools.run_tool("propose_fleet_profile", dict(
        name="Seletar pair upload", mode="upload",
        aircraft=[dict(sysid=113, plan="missions/seletar/mission_drones/drone-2.plan"),
                  dict(sysid=115, plan="missions/seletar/mission_drones/drone-1.plan")])))
    assert out["errors"] == [] and not (tmp_path / "fleet").exists()
    assert json.loads(tools.run_tool("propose_bench_profile", dict(name="b", sysids=[113, 115], stage="takeoff")))["profile"]["sysids"] == [113, 115]


def test_bad_arguments_and_runaway_loops_end_safely():
    assert "bad arguments" in tools.run_tool("get_run", {"nope": 1})
    r = agent.ask("loop", llm=Scripted([[("list_missions", {})]] * (policy.MAX_STEPS + 1)))
    assert "maximum number of lookups" in r["answer"] and len(r["calls"]) == policy.MAX_STEPS


def test_no_model_server_is_reported_not_raised():
    r = agent.ask("hello", llm=OllamaChat(url="http://127.0.0.1:9", timeout_s=1))
    assert r["answer"] is None and "did not answer" in r["error"]
    assert OllamaChat(url="http://127.0.0.1:9").status()["ok"] is False


def test_docs_search_finds_the_safety_model():
    hits = tools.search_docs("handset authority pilot takeover")
    assert hits and any("handset" in h["text"].lower() for h in hits)

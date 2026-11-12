"""Phase C, DEMO only: the assistant executes a delegated run and stops aircraft; fault injection.

The console runs in-process in DEMO mode (simulated agents); a scripted stand-in plays the model's choices, so these
tests pin what the CODE allows, whatever a model asks for.
"""
import time

import pytest

from artex.assistant import actions, agent
from artex.console import gs_core as core
from artex.console import gs_fleet as fleet
from artex.console import gs_server as srv
from artex.console import sessions


class Scripted:
    model = "scripted"

    def __init__(self, turns):
        self.turns = list(turns)

    def chat(self, messages, tools_):
        t = self.turns.pop(0)
        if isinstance(t, str):
            return dict(content=t, tool_calls=[])
        return dict(content="", tool_calls=[dict(name=n, arguments=a) for n, a in t])


MISSION = dict(name="Sample pair", mode="mission", flight_type="props_on",
               aircraft=[dict(sysid=113, plan="missions/sample/mission_drones/drone-2.plan", link="radio"),
                         dict(sysid=115, plan="missions/sample/mission_drones/drone-1.plan", link="radio")])
BENCH = dict(name="Pair bench arm", sysids=[113, 115], flight_type="bench", link="radio", stage="arm", wait_s=5)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PROFILE_DIR", tmp_path / "profiles")
    monkeypatch.setattr(core, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(fleet, "FLEET_DIR", tmp_path / "profiles" / "fleet")
    monkeypatch.setenv("DEMO_SPEED", "40")
    a = srv.App(demo=True)
    for s in (113, 115):
        a.preflight(s)
    a.ses = a.sessions.get(a.sessions.open("Joseph"))
    sessions.Sessions.set_crew(a.ses, {"113": "Ann", "115": "Ben"})
    yield a
    for r in (a.fleet, a.run):
        if r and r.active:
            if hasattr(r, "cancel_evt"):
                r.cancel_evt.set()
            else:
                r.send("ABORT")


def authorise(app, kind, raw, delegate=True):
    p = fleet.normalise(raw) if kind == "fleet" else core.normalise(raw)
    items = (fleet.assess(p, {m["sysid"]: app.ctx(m["sysid"]) for m in p["aircraft"]}) if kind == "fleet"
             else core.assess(p, app.ctx(p["sysid"])))
    sessions.Sessions.accept(app.ses, core.profile_hash(p), [i["id"] for i in items if i["level"] == "accept"])
    code, out = app.quick_authorise(kind, p, app.ses, dict(confirm="AUTHORISE", delegate=delegate))
    assert code == 200, out
    return out["token"]


def say(app, token, turns, question="fly it"):
    return agent.ask(question, llm=Scripted(turns), phase="C", extra_tools=actions.tools_for(app, token),
                     act_ok=actions.delegation_problem(app, token) is None)


def until(cond, t=30):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_without_delegation_the_assistant_cannot_start_anything(app):
    tok = authorise(app, "fleet", MISSION, delegate=False)
    r = say(app, tok, [[("execute_authorised_run", {})], "I can't: the run was not delegated to me."])
    assert "needs the operator's delegated authorisation" in r["calls"][0]["refused"]
    assert app.fleet is None and app.run is None


def test_a_delegated_mission_runs_both_gates_and_a_takeover_holds_the_other(app):
    tok = authorise(app, "fleet", MISSION)
    r = say(app, tok, [[("execute_authorised_run", {})], "Started; the console will press ARM ALL and START ALL when due."])
    assert not r["calls"][0]["refused"] and '"started"' in r["calls"][0]["result"]
    assert until(lambda: app.fleet.state == "flying"), app.fleet.state
    acts = [x["text"] for x in app.fleet.rec["assistant_actions"]]
    assert any("ARM ALL" in a for a in acts) and any("START ALL" in a for a in acts)
    app.fleet.word("DEMO_TAKEOVER", "115")
    assert until(lambda: app.fleet.members[113].v["held"])
    # "come back": stopping needs no delegation, and the token is used up by now anyway
    r2 = say(app, None, [[("stop_aircraft", dict(action="rtl"))], "Both are returning to launch."], "come back")
    assert not r2["calls"][0]["refused"]
    assert until(lambda: not app.fleet.active, 40)
    assert "ASSISTANT" in " ".join(f.get("text", "") for f in app.fleet.feed)


def test_one_aircraft_not_ready_means_the_sequencer_presses_nothing(app, monkeypatch):
    real = fleet.agent_argv
    monkeypatch.setattr(fleet, "agent_argv", lambda p, m, rid: real(p, m, rid) + (["--demo-not-ready"] if m["sysid"] == 115 else []))
    tok = authorise(app, "fleet", MISSION)
    say(app, tok, [[("execute_authorised_run", {})], "Started."])
    assert until(lambda: not app.fleet.active, 40)
    assert "NOBODY armed" in app.fleet.result["outcome"]
    assert not any(m.v["armed"] for m in app.fleet.members.values())


def test_an_expired_or_spent_authorisation_is_refused(app):
    tok = authorise(app, "fleet", MISSION)
    app.tokens.revoke(tok)
    r = say(app, tok, [[("execute_authorised_run", {})], "Refused."])
    assert "refused" in (r["calls"][0]["refused"] or "")         # act not offered: the delegation is no longer valid
    assert app.fleet is None


def test_a_delegated_two_aircraft_bench_run_is_armed_through_the_same_arm_path(app):
    tok = authorise(app, "single", BENCH)
    say(app, tok, [[("execute_authorised_run", {})], "Started."])
    assert until(lambda: not app.run.active, 40)
    assert app.run.arm_sent and any("pressed ARM ALL" in x["text"] for x in app.run.rec["assistant_actions"])
    assert all(m["armed"] for m in app.run.snapshot(0)["members"])


def test_stop_with_nothing_running_is_a_clear_answer_not_a_crash(app):
    r = say(app, None, [[("stop_aircraft", dict(action="land"))], "There is no active run."], "land them")
    assert "no active run" in r["calls"][0]["result"]


def test_live_aircraft_refuse_delegation_by_default(monkeypatch):
    monkeypatch.delenv("ARTEX_ASSISTANT_LIVE", raising=False)
    a = srv.App(demo=False)
    prof = fleet.normalise(MISSION)
    tok = a.tokens.issue(prof, "J", {})                       # a genuine, unexpired authorisation
    a.delegations[tok] = dict(kind="fleet", profile=prof, operator="J")
    assert "switched off for real aircraft" in actions.delegation_problem(a, tok)
    monkeypatch.setenv("ARTEX_ASSISTANT_LIVE", "enabled")
    assert actions.delegation_problem(a, tok) is None


# ---- the fixes from the 12 Nov live evaluation ----------------------------------------------

def test_a_clear_stop_request_goes_straight_to_the_aircraft_without_the_model(app):
    tok = authorise(app, "fleet", MISSION, delegate=False)
    app.start_fleet(fleet.normalise(MISSION), tok)
    assert until(lambda: app.fleet.state == "ready")

    class Never:
        model = "never"

        def chat(self, *a):
            raise AssertionError("the model must not be consulted for a clear stop request")
    r = agent.ask("Come back now, both of them!", llm=Never(), phase="C", extra_tools=actions.tools_for(app, None))
    assert r["calls"][0]["tool"] == "stop_aircraft" and r["calls"][0]["direct"] and r["calls"][0]["arguments"] == {"action": "rtl"}


def test_questions_and_negations_about_stopping_are_not_treated_as_commands():
    assert agent.stop_intent("Why did it stop?") is None
    assert agent.stop_intent("don't land yet") is None
    assert agent.stop_intent("land them") == "land" and agent.stop_intent("STOP") == "hold" and agent.stop_intent("abort!") == "rtl"


def test_a_tool_call_written_as_text_is_still_executed_but_only_for_real_tools(app):
    reg = actions.tools_for(app, None)
    assert agent.text_tool_call('stop_aircraft({"action": "rtl", "target": "all"})', reg) == dict(name="stop_aircraft", arguments={"action": "rtl", "target": "all"})
    assert agent.text_tool_call('arm_all({"x": 1})', reg) is None
    assert agent.text_tool_call("I will stop the aircraft.", reg) is None


def test_the_model_is_told_whether_it_holds_a_delegation(app):
    seen = []

    class Spy:
        model = "spy"

        def chat(self, messages, tools_):
            seen.append(messages)
            return dict(content="ok", tool_calls=[])
    tok = authorise(app, "fleet", MISSION)
    agent.ask("fly it", llm=Spy(), phase="C", extra_tools=actions.tools_for(app, tok), act_ok=True, delegation=app.delegations[tok])
    agent.ask("fly it", llm=Spy(), phase="C", extra_tools=actions.tools_for(app, None), act_ok=False)
    assert "Delegation: VALID" in " ".join(m["content"] for m in seen[0]) and "Sample pair" in " ".join(m["content"] for m in seen[0])
    assert "Delegation: NONE" in " ".join(m["content"] for m in seen[1])


def test_an_upload_run_has_no_gates_for_the_operator_or_the_assistant(app):
    up = dict(MISSION, name="Sample upload", mode="upload", flight_type="bench")
    tok = authorise(app, "fleet", up)
    say(app, tok, [[("execute_authorised_run", {})], "Started."])
    assert app.fleet.p["mode"] == "upload"
    assert app.fleet_gate("arm", tok, "ARM ALL")[0] == 409
    assert until(lambda: not app.fleet.active, 40)
    assert not any("ARM ALL" in x["text"] for x in app.fleet.rec.get("assistant_actions", []))

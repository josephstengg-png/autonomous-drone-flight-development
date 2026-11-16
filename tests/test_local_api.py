"""The real local API (artex.console.local_api) in DEMO mode: the same script as the mock's, against the real console,
meeting the real checks (docs/local-app-plan.md, step 2)."""
import pytest

from artex.console import gs_core as core
from artex.console import gs_fleet as fleet
from artex.console import gs_server as srv
from artex.console import sessions
from artex.console.local_api import ApiError, Console

P1 = "missions/sample/mission_drones/drone-1.plan"
P2 = "missions/sample/mission_drones/drone-2.plan"
PROFILE = dict(name="Sample pair", mode="mission", flight_type="props_on",
               aircraft=[dict(sysid=113, plan=P2, link="radio"), dict(sysid=115, plan=P1, link="radio")])


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PROFILE_DIR", tmp_path / "profiles")
    monkeypatch.setattr(core, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(fleet, "FLEET_DIR", tmp_path / "profiles" / "fleet")
    monkeypatch.setattr(sessions, "OPERATORS_FILE", tmp_path / "operators.json")
    monkeypatch.setattr(sessions, "PBKDF2_ROUNDS", 1000)
    monkeypatch.setenv("DEMO_SPEED", "40")
    return Console(srv.App(demo=True, require_signin=True))


def _signed_in(c):
    c.first_operator("Ops Lead", "482916")
    c.set_crew({113: "Pilot A", 115: "Pilot B"})
    for s in (113, 115):
        assert c.preflight(s)["go"]
    return c


def test_nothing_acts_before_sign_in(c):
    with pytest.raises(ApiError) as e:
        c.preflight(113)
    assert e.value.status == 401
    assert c.state()["demo"] is True                     # reading needs no session


def test_authorise_needs_preflight_risks_pilots_and_the_word(c):
    c.first_operator("Ops Lead", "482916")
    with pytest.raises(ApiError) as e:
        c.authorise("fleet", PROFILE, confirm="AUTHORISE")
    assert e.value.status == 403 and "Preflight" in e.value.message     # a block nobody can tick away
    for s in (113, 115):
        c.preflight(s)
    with pytest.raises(ApiError) as e:
        c.authorise("fleet", PROFILE, confirm="AUTHORISE")
    assert e.value.status == 409 and "risks" in e.value.message
    c.accept_risks("fleet", PROFILE)
    with pytest.raises(ApiError) as e:
        c.authorise("fleet", PROFILE, confirm="AUTHORISE")
    assert e.value.status == 409 and "pilot" in e.value.message
    c.set_crew({113: "Pilot A", 115: "Pilot B"})
    with pytest.raises(ApiError) as e:
        c.authorise("fleet", PROFILE, confirm="yes please")
    assert e.value.status == 403


def test_a_full_fleet_mission_through_both_gates(c):
    _signed_in(c)
    c.accept_risks("fleet", PROFILE)
    tok = c.authorise("fleet", PROFILE, confirm="AUTHORISE")["token"]
    assert c.start("fleet", PROFILE, tok)["id"]
    c.wait_for("ready", 30)
    with pytest.raises(ApiError) as e:
        c.start_all(tok, confirm="START ALL")             # gate 2 before gate 1
    assert e.value.status == 409
    c.arm_all(tok, confirm="ARM ALL")
    c.wait_for("armed", 30)
    c.start_all(tok, confirm="START ALL")
    s = c.wait_for(("done", "ended"), 60)
    assert s["result"]["passed"] is True and "completed" in s["result"]["outcome"]
    import time
    end = time.time() + 10
    while not any(r["id"] == s["id"] for r in c.list_runs()) and time.time() < end:
        time.sleep(0.2)                                   # the record is written as the conductor finishes
    assert any(r["id"] == s["id"] for r in c.list_runs())


def test_an_observer_may_stop_but_not_arm(c):
    _signed_in(c)
    c.add_operator("Watcher", "1111", "observer")
    c.accept_risks("fleet", PROFILE)
    tok = c.authorise("fleet", PROFILE, confirm="AUTHORISE")["token"]
    c.start("fleet", PROFILE, tok)
    c.wait_for("ready", 30)
    w = Console(c.app)
    w.sign_in("Watcher", "1111")
    with pytest.raises(ApiError) as e:
        w.arm_all(tok, confirm="ARM ALL")
    assert e.value.status == 403
    assert w.cancel()["ok"] is True                       # an observer may end it
    c.wait_for(("ended", "done", "not_ready"), 30)


def test_a_wrong_pin_is_refused_and_counted(c):
    c.first_operator("Ops Lead", "482916")
    c.sign_out()
    with pytest.raises(ApiError) as e:
        c.sign_in("Ops Lead", "000000")
    assert e.value.status == 403

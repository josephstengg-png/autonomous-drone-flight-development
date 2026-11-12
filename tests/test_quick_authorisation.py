"""Simplified authorisation (Phase A2): sign in once, accept a profile's risks once, then one tick + one hold per run."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from artex.console import gs_core as core
from artex.console import gs_fleet as fleet
from artex.console import gs_server as srv
from artex.console import sessions


@pytest.fixture()
def gs(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PROFILE_DIR", tmp_path / "profiles")
    monkeypatch.setattr(core, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(fleet, "FLEET_DIR", tmp_path / "profiles" / "fleet")
    monkeypatch.setattr(sessions, "OPERATORS_FILE", tmp_path / "operators.json")
    monkeypatch.setattr(sessions, "PBKDF2_ROUNDS", 1000)
    monkeypatch.setenv("DEMO_SPEED", "40")
    app = srv.App(demo=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    state = {}

    def call(method, path, b=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        h = {"Host": f"127.0.0.1:{port}"}
        if state.get("tok"):
            h["X-GS-Session"] = state["tok"]
        if method == "POST":
            h.update({"X-GS-Client": "ui", "Content-Type": "application/json"})
        c.request(method, path, json.dumps(b) if b is not None else None, h)
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    call.state, call.app = state, app
    yield call
    httpd.shutdown()


BENCH = dict(name="Pair bench arm", sysids=[113, 115], flight_type="bench", link="radio", stage="arm", wait_s=5)


def test_first_operator_then_sign_in_with_a_pin_only_stored_as_a_hash(gs, tmp_path):
    assert gs("GET", "/api/session")[1] == dict(signed_in=False, has_operators=False, operator=None, pilots=None, role=None,
                                                require_signin=False)
    s, r = gs("POST", "/api/session/first_operator", dict(name="Joseph", pin="2468"))
    assert s == 200
    assert "2468" not in (tmp_path / "operators.json").read_text()
    assert gs("POST", "/api/session/first_operator", dict(name="Mallory", pin="1111"))[0] == 403
    assert gs("POST", "/api/session/signin", dict(name="joseph", pin="0000"))[0] == 403
    s, r = gs("POST", "/api/session/signin", dict(name="joseph", pin="2468"))
    gs.state["tok"] = r["token"]
    assert gs("GET", "/api/session")[1]["operator"] == "Joseph"


def test_five_wrong_pins_lock_the_name_for_a_minute(gs):
    gs("POST", "/api/session/first_operator", dict(name="Joseph", pin="2468"))
    for _ in range(5):
        assert gs("POST", "/api/session/signin", dict(name="Joseph", pin="9999"))[0] == 403
    assert gs("POST", "/api/session/signin", dict(name="Joseph", pin="2468"))[0] == 429


def signed_in(gs, pilots=None):
    s, r = gs("POST", "/api/session/first_operator", dict(name="Joseph", pin="2468"))
    gs.state["tok"] = r["token"]
    if pilots:
        assert gs("POST", "/api/session/crew", dict(pilots=pilots))[0] == 200


def test_quick_authorisation_needs_a_session_pilots_and_one_confirmation(gs):
    assert gs("POST", "/api/run/authorise_quick", dict(profile=BENCH, confirm="AUTHORISE"))[0] == 401
    signed_in(gs)
    s, r = gs("POST", "/api/run/authorise_quick", dict(profile=BENCH, confirm="AUTHORISE"))
    assert s == 409 and r["need_pilots"] == [113, 115]
    assert gs("POST", "/api/session/crew", dict(pilots={"113": "Ann", "115": "ann"}))[0] == 400     # same person twice
    gs("POST", "/api/session/crew", dict(pilots={"113": "Ann", "115": "Ben"}))
    assert gs("POST", "/api/run/authorise_quick", dict(profile=BENCH))[0] == 403                    # no confirmation
    d = gs("POST", "/api/run/authorise_quick", dict(profile=BENCH, confirm="AUTHORISE", delegate=True))
    assert d[0] == 200 and d[1]["delegated"] and gs.app.delegations[d[1]["token"]]["operator"] == "Joseph"   # DEMO allows it
    s, r = gs("POST", "/api/run/authorise_quick", dict(profile=BENCH, confirm="AUTHORISE"))
    assert s == 200 and r["token"]
    tok = gs.app.tokens.get(r["token"], core.normalise(BENCH))
    assert tok["record"]["pilots"] == {"113": "Ann", "115": "Ben"} and tok["record"]["quick"]


def test_risks_are_accepted_once_per_profile_and_asked_again_when_it_changes(gs):
    signed_in(gs, {"113": "Ann", "115": "Ben"})
    fp = dict(name="Sample pair", mode="mission", flight_type="props_on",
              aircraft=[dict(sysid=113, plan="missions/sample/mission_drones/drone-2.plan", link="radio"),
                        dict(sysid=115, plan="missions/sample/mission_drones/drone-1.plan", link="radio")])
    for sid in (113, 115):
        gs("POST", "/api/preflight", dict(sysid=sid))
    s, r = gs("POST", "/api/fleet/authorise_quick", dict(profile=fp, confirm="AUTHORISE"))
    assert s == 409 and "separation" in r["need_accept"]
    assert gs("POST", "/api/session/accept", dict(kind="fleet", profile=fp))[0] == 200
    assert gs("POST", "/api/fleet/authorise_quick", dict(profile=fp, confirm="AUTHORISE"))[0] == 200
    changed = dict(fp, description="edited")
    assert gs("POST", "/api/fleet/authorise_quick", dict(profile=changed, confirm="AUTHORISE"))[0] == 409


def test_a_block_cannot_be_authorised_away(gs):
    signed_in(gs, {"113": "Ann", "115": "Ben"})
    props_on_radio = dict(name="Hover radio", sysid=113, flight_type="tether", link="radio", stage="disarm")
    s, r = gs("POST", "/api/run/authorise_quick", dict(profile=props_on_radio, confirm="AUTHORISE"))
    assert s == 403 and "Preflight" in r["error"]


def test_delegation_is_refused_for_real_aircraft_unless_the_site_switches_it_on(monkeypatch):
    app = srv.App(demo=False)
    ses = app.sessions.get(app.sessions.open("Joseph"))
    sessions.Sessions.set_crew(ses, {"113": "Ann", "115": "Ben"})
    p = core.normalise(BENCH)
    monkeypatch.delenv("ARTEX_ASSISTANT_LIVE", raising=False)
    code, out = app.quick_authorise("single", p, ses, dict(confirm="AUTHORISE", delegate=True))
    assert code == 403 and "switched off for real aircraft" in out["error"] and not app.delegations
    monkeypatch.setenv("ARTEX_ASSISTANT_LIVE", "enabled")
    code, out = app.quick_authorise("single", p, ses, dict(confirm="AUTHORISE", delegate=True))
    assert code == 200 and out["delegated"]

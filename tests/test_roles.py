"""Roles (admin / operator / observer) when the console requires sign-in, as the product runs it."""
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
    app = srv.App(demo=True, require_signin=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def call(method, path, b=None, tok=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        h = {"Host": f"127.0.0.1:{port}"}
        if tok:
            h["X-GS-Session"] = tok
        if method == "POST":
            h.update({"X-GS-Client": "ui", "Content-Type": "application/json"})
        c.request(method, path, json.dumps(b) if b is not None else None, h)
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data
    yield call
    httpd.shutdown()


PROF = dict(name="Bench one", sysid=113, flight_type="bench", link="radio", stage="arm")


def test_the_first_operator_is_admin_and_only_an_admin_adds_people(gs):
    admin = gs("POST", "/api/session/first_operator", dict(name="Joseph", pin="2468"))[1]["token"]
    assert gs("GET", "/api/session", tok=admin)[1]["role"] == "admin"
    assert gs("POST", "/api/session/add_operator", dict(name="Olive", pin="1357", role="observer"), tok=admin)[0] == 200
    assert gs("POST", "/api/session/add_operator", dict(name="Oscar", pin="1111", role="operator"), tok=admin)[0] == 200
    obs = gs("POST", "/api/session/signin", dict(name="Olive", pin="1357"))[1]["token"]
    op = gs("POST", "/api/session/signin", dict(name="Oscar", pin="1111"))[1]["token"]
    assert gs("POST", "/api/session/add_operator", dict(name="Eve", pin="0000", role="admin"), tok=op)[0] == 403


def test_nothing_happens_without_signing_in(gs):
    assert gs("POST", "/api/profiles", PROF)[0] == 401
    assert gs("POST", "/api/run/start", dict(profile=PROF, token="x"))[0] == 401
    assert gs("GET", "/api/profiles")[0] == 200                     # reading the screens stays open on this machine


def test_an_observer_may_watch_and_stop_but_not_authorise_save_or_plan(gs):
    admin = gs("POST", "/api/session/first_operator", dict(name="Joseph", pin="2468"))[1]["token"]
    gs("POST", "/api/session/add_operator", dict(name="Olive", pin="1357", role="observer"), tok=admin)
    obs = gs("POST", "/api/session/signin", dict(name="Olive", pin="1357"))[1]["token"]
    for path, body in [("/api/profiles", PROF), ("/api/run/authorise_quick", dict(profile=PROF, confirm="AUTHORISE")),
                       ("/api/planner/run", dict(kml="ops/x.kml")), ("/api/session/crew", dict(pilots={}))]:
        s, r = gs("POST", path, body, tok=obs)
        assert s == 403 and "observer" in r["error"], path
    assert gs("POST", "/api/fleet/word", dict(word="HOLD"), tok=obs)[0] == 409       # allowed through; no run to stop
    assert gs("POST", "/api/profiles/preview", PROF, tok=obs)[0] == 200


def test_an_operator_runs_things(gs):
    admin = gs("POST", "/api/session/first_operator", dict(name="Joseph", pin="2468"))[1]["token"]
    gs("POST", "/api/session/add_operator", dict(name="Oscar", pin="1111"), tok=admin)
    op = gs("POST", "/api/session/signin", dict(name="Oscar", pin="1111"))[1]["token"]
    assert gs("POST", "/api/profiles", PROF, tok=op)[0] == 200

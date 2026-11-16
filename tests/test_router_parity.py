"""The web server and a direct `dispatch` give the same answers (docs/local-app-plan.md, step 1): one router, so the desktop
window, the local Python API and the web server cannot drift apart."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from artex.console import gs_core as core
from artex.console import gs_fleet as fleet
from artex.console import gs_server as srv
from artex.console import sessions

PROFILE = dict(name="parity hover", sysid=113, link="usb", flight_type="bench", stage="arm")


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PROFILE_DIR", tmp_path / "profiles")
    monkeypatch.setattr(core, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(fleet, "FLEET_DIR", tmp_path / "profiles" / "fleet")
    monkeypatch.setattr(sessions, "PBKDF2_ROUNDS", 1000)
    return tmp_path, monkeypatch


def _http(app):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def call(method, path, body=None, session=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = {"Host": f"127.0.0.1:{port}"}
        if method == "POST":
            h.update({"X-GS-Client": "ui", "Content-Type": "application/json"})
        if session:
            h["X-GS-Session"] = session
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        out = (r.status, json.loads(r.read()))
        c.close()
        return out
    return call, httpd


def _script(call):
    """The same conversation, recorded as (status, answer) with session tokens masked."""
    out = []
    rec = lambda r: out.append((r[0], r[1])) or r   # noqa: E731
    rec(call("GET", "/api/session"))
    rec(call("POST", "/api/preflight", dict(sysid=113)))                 # not signed in -> 401
    tok = call("POST", "/api/session/first_operator", dict(name="Ops Lead", pin="482916"))[1]["token"]
    out.append((200, "<first operator token>"))
    rec(call("GET", "/api/session", session=tok))
    rec(call("POST", "/api/session/crew", dict(pilots={"113": "Pilot A"}), session=tok))
    rec(call("POST", "/api/session/signin", dict(name="Ops Lead", pin="000000")))   # wrong PIN -> 403
    rec(call("POST", "/api/profiles", PROFILE, session=tok))
    rec(call("GET", "/api/profiles"))
    prev = call("POST", "/api/profiles/preview", PROFILE, session=tok)
    rec((prev[0], {k: prev[1][k] for k in ("errors", "session", "assessment")}))
    rec(call("POST", "/api/session/accept", dict(kind="single", profile=PROFILE), session=tok))
    rec(call("POST", "/api/run/arm", dict(token="nope", confirm="ARM"), session=tok))   # no run -> refused
    rec(call("GET", "/api/planner/kml"))
    rec(call("GET", "/api/runs"))
    rec(call("GET", "/api/run?since=0"))
    rec(call("GET", "/api/nope"))
    rec(call("POST", "/api/nope", {}, session=tok))
    return out


def test_web_server_and_dispatch_answer_identically(iso):
    tmp, mp = iso
    mp.setattr(sessions, "OPERATORS_FILE", tmp / "ops-http.json")
    app_http = srv.App(demo=True, require_signin=True)
    call, httpd = _http(app_http)
    try:
        via_http = _script(call)
    finally:
        httpd.shutdown()
    for f in (tmp / "profiles").glob("*.json"):
        f.unlink()
    mp.setattr(sessions, "OPERATORS_FILE", tmp / "ops-direct.json")
    app_direct = srv.App(demo=True, require_signin=True)
    via_dispatch = _script(lambda m, p, b=None, session=None: srv.dispatch(app_direct, m, p, b, session))
    assert len(via_http) == len(via_dispatch)
    for i, (a, b) in enumerate(zip(via_http, via_dispatch)):
        assert a[0] == b[0] and a[1] == json.loads(json.dumps(b[1], default=str)), f"step {i}: {a} != {b}"
    assert [s for s, _ in via_http][:2] == [200, 401]


def test_dispatch_rejects_unknown_methods(iso):
    assert srv.dispatch(srv.App(demo=True), "DELETE", "/api/state")[0] == 405

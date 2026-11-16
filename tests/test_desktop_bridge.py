"""The desktop window's bridge, without a window (docs/local-app-plan.md, step 3). The window itself is checked with
`artex-console --check` (needs a display) and by hand."""
import threading

import pytest

from artex.console import gs_core as core
from artex.console import gs_fleet as fleet
from artex.console import gs_server as srv
from artex.console import sessions
from artex.console.desktop import Bridge, run_active


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PROFILE_DIR", tmp_path / "profiles")
    monkeypatch.setattr(core, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setattr(fleet, "FLEET_DIR", tmp_path / "profiles" / "fleet")
    monkeypatch.setattr(sessions, "OPERATORS_FILE", tmp_path / "operators.json")
    monkeypatch.setattr(sessions, "PBKDF2_ROUNDS", 1000)
    return srv.App(demo=True, require_signin=True)


def test_the_bridge_only_reaches_api_paths(app):
    b = Bridge(app)
    for path in ("/static/index.html", "../../etc/passwd", "file:///etc/passwd", 42):
        assert b.call("GET", path)["status"] == 404


def test_the_bridge_answers_like_dispatch_and_carries_the_session(app):
    b = Bridge(app)
    assert b.call("GET", "/api/state")["body"]["aircraft"] == [113, 115]
    assert b.call("POST", "/api/preflight", {"sysid": 113})["status"] == 401          # not signed in
    tok = b.call("POST", "/api/session/first_operator", {"name": "Ops Lead", "pin": "482916"})["body"]["token"]
    assert b.call("GET", "/api/session", None, tok)["body"]["operator"] == "Ops Lead"
    assert b.call("POST", "/api/preflight", {"sysid": 113}, tok)["status"] == 200
    assert b.call("POST", "/api/preflight", "not a dict", tok)["status"] == 400          # bad body -> refused, not crashed


def test_run_active_and_a_confirmed_close(app):
    assert run_active(app) is False
    closed = threading.Event()

    class W:
        def destroy(self):
            closed.set()
    b = Bridge(app)
    b._window = W()
    assert b.force_close() == {"ok": True}
    assert closed.wait(2) and b._force is True

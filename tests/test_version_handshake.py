"""The console refuses to drive a companion whose agent code differs from its own (10 Nov 2026 incident)."""
from artex.common import layout, version
from artex.console import gs_server as srv


def test_the_fingerprint_covers_the_companion_payload_and_changes_with_it(tmp_path):
    for rel in layout.COMPANION_PAYLOAD:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(rel)
    a = version.fingerprint(tmp_path)
    (tmp_path / "artex/agent/drone_agent.py").write_text("older agent")
    assert version.fingerprint(tmp_path) != a and len(a) == 16


def test_a_stale_or_missing_companion_blocks_a_live_run(monkeypatch):
    app = srv.App(demo=False)
    mine = version.report()
    answers = {113: dict(mine), 115: dict(version="0.0.9", fingerprint="0123456789abcdef")}
    monkeypatch.setattr(app, "remote_version", lambda s: answers.get(s))
    assert app.version_problem([113]) is None
    msg = app.version_problem([113, 115])
    assert "ARTEX115" in msg and "ARTEX113" not in msg and "provision" in msg
    answers.pop(115)
    assert "no answer" in app.version_problem([115])


def test_demo_contacts_nothing():
    assert srv.App(demo=True).version_problem([113, 115]) is None

"""Several aircraft in one props-off bench profile (ARM ALL), and the KML -> planner step (10 Nov 2026)."""
import http.client
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from artex.console import gs_core as core  # noqa: E402
from artex.planner import gs_planner as planner  # noqa: E402
from artex.console import gs_server as srv  # noqa: E402


def group(**kw):
    base = dict(name="Pair bench arm", sysids=[113, 115], flight_type="bench", link="radio", stage="arm", wait_s=5)
    base.update(kw)
    return core.normalise(base)


# ---- profiles ---------------------------------------------------------------------------

def test_a_two_aircraft_bench_profile_runs_one_agent_each():
    p = group()
    assert core.validate(p) == [] and p["sysid"] == 113 and p["sysids"] == [113, 115]
    a, b = core.agent_argv(p, "/tmp/t.csv", 113), core.agent_argv(p, "/tmp/t.csv", 115)
    assert a[a.index("--expect-sysid") + 1] == "113" and b[b.index("--expect-sysid") + 1] == "115"
    assert "--allow-kill" not in a + b
    assert "ARTEX113 and ARTEX115" in core.confirmations(p)[0]["text"]
    assert any(i["id"] == "group" for i in core.assess(p, {}))


def test_an_old_single_aircraft_profile_still_reads_as_one_aircraft():
    p = core.normalise(dict(name="Old", sysid=115, flight_type="bench", stage="arm"))
    assert p["sysids"] == [115] and core.validate(p) == []


def test_several_aircraft_only_with_props_off():
    assert any("props-OFF bench" in e for e in core.validate(group(flight_type="tether")))
    assert any("not known" in e for e in core.validate(group(sysids=[113, 999])))


def test_every_aircraft_must_be_typed():
    p = group()
    body = dict(operator="J Leong", phrase=core.PHRASE, accepted=[], confirmed=[c["id"] for c in core.confirmations(p)])
    assert core.authorise(p, {}, dict(body, aircraft="ARTEX115 ARTEX113")) == (True, "")
    assert "ARTEX115" in core.authorise(p, {}, dict(body, aircraft="ARTEX113"))[1]


# ---- the server, DEMO -------------------------------------------------------------------

@pytest.fixture()
def gs(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PROFILE_DIR", tmp_path / "profiles")
    monkeypatch.setattr(core, "RUN_DIR", tmp_path / "runs")
    monkeypatch.setenv("DEMO_SPEED", "40")
    app = srv.App(demo=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def call(method, path, b=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        h = {"Host": f"127.0.0.1:{port}"}
        if method == "POST":
            h.update({"X-GS-Client": "ui", "Content-Type": "application/json"})
        c.request(method, path, json.dumps(b) if b is not None else None, h)
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    yield call
    httpd.shutdown()


def wait(call, states, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        r = call("GET", "/api/run")[1]
        if r.get("state") in states:
            return r
        time.sleep(0.1)
    raise AssertionError(f"never reached {states}: {call('GET', '/api/run')[1].get('state')}")


def test_arm_all_arms_every_aircraft_and_each_keeps_its_own_record(gs):
    p = group()
    body = dict(profile=p, operator="J Leong", aircraft="ARTEX113, ARTEX115", phrase=core.PHRASE, accepted=[],
                confirmed=[c["id"] for c in core.confirmations(p)])
    s, a = gs("POST", "/api/run/authorise", body)
    assert s == 200, a
    tok = a["token"]
    assert gs("POST", "/api/run/start", dict(profile=p, token=tok))[0] == 200
    r = wait(gs, ("ready",))
    assert r["group"] and {m["sysid"] for m in r["members"]} == {113, 115}
    assert gs("POST", "/api/run/arm", dict(token=tok, confirm="ARM"))[0] == 200
    r = wait(gs, ("done", "ended"))
    assert all(m["armed"] for m in r["members"]), r["members"]
    rec = core.load_run(r["id"])
    assert rec["kind"] == "group" and len(rec["members"]) == 2 and rec.get("arm_spread_s") is not None
    for mid in rec["members"]:
        m = core.load_run(mid)
        assert m and m["group"] == r["id"] and all(f.get("sysid") in (None, m["profile"]["sysid"]) for f in m["feed"])


# ---- KML and the planner ----------------------------------------------------------------

@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "OPS_DIR", tmp_path / "ops")
    monkeypatch.setattr(planner, "MISSIONS_DIR", tmp_path / "missions")
    return tmp_path


SELETAR = (core.ROOT / "ops" / "seletar-west-camp.kml")


def test_a_kml_the_planner_cannot_read_is_refused_and_nothing_is_overwritten(dirs):
    with pytest.raises(ValueError, match="needs placemarks"):
        planner.save_kml("bad.kml", "<kml><Document><Placemark><name>x</name></Placemark></Document></kml>")
    with pytest.raises(ValueError, match="DOCTYPE"):
        planner.save_kml("evil.kml", '<!DOCTYPE x [<!ENTITY a "b">]><kml/>')
    a = planner.save_kml("Seletar West.kml", SELETAR.read_text())
    b = planner.save_kml("Seletar West.kml", SELETAR.read_text())
    assert a["path"] == "ops/seletar-west.kml" and b["path"] == "ops/seletar-west-2.kml"
    assert abs(a["gcs"][0] - 1.4172) < 0.01 and a["fence_points"] >= 3
    assert not list((dirs / "ops").glob(".upload-*"))


def test_the_planner_writes_one_plan_per_drone_into_a_new_folder(dirs):
    k = planner.save_kml("seletar.kml", SELETAR.read_text())
    r = planner.run_planner(k["path"], "Seletar test", 2, 30.0, 25.0, "lawnmower")
    assert r["ok"], r
    assert len(r["plans"]) == 2 and all(p["items"] > 3 for p in r["plans"])
    assert r["folder"].endswith("seletar-test-" + time.strftime("%Y-%m-%d"))
    again = planner.run_planner(k["path"], "Seletar test", 2, 30.0, 25.0, "lawnmower")
    assert again["folder"] != r["folder"]                                    # never written over
    assert "Number of drones" in planner.run_planner(k["path"], "x", 0, 30.0, 25.0, "lawnmower")["error"]


# ---- the mission map ---------------------------------------------------------------------

def test_the_map_gets_each_drones_path_the_fence_and_home():
    v = planner.mission_view("missions/seletar")
    assert [d["name"] for d in v["drones"]] == ["drone-1", "drone-2"]
    d1 = v["drones"][0]
    assert d1["items"] == 13 and d1["top_alt_m"] == 30.0 and d1["km"] > 0.1
    assert abs(d1["points"][0][0] - 1.4172) < 0.01 and v["fence"] and abs(v["home"][0] - 1.4172) < 0.001
    assert "missions/seletar" in [m["folder"] for m in planner.list_missions()]
    with pytest.raises(ValueError):
        planner.mission_view("../etc")


def test_on_board_comes_from_a_fleet_runs_read_back(tmp_path):
    ok = dict(name="mission_uploaded", ok=True, detail="11 items on board, 11 in the plan")
    rec = dict(id="20261110-145105", kind="fleet", started="2026-11-10 14:51:05", mode="live",
               result=dict(aircraft={"113": dict(plan="missions/seletar/mission_drones/drone-2.plan", checks=[ok]),
                                     "115": dict(plan="missions/seletar/mission_drones/drone-1.plan",
                                                 checks=[dict(ok, ok=False)])}))
    (tmp_path / "20261110-145105.json").write_text(json.dumps(rec))
    found = planner.onboard_records(tmp_path)
    assert found["missions/seletar/mission_drones/drone-2.plan"]["sysid"] == 113
    assert "missions/seletar/mission_drones/drone-1.plan" not in found      # its read-back did not match


def test_a_newly_planned_folder_remembers_its_kml(dirs):
    k = planner.save_kml("seletar.kml", SELETAR.read_text())
    r = planner.run_planner(k["path"], "map test", 2, 30.0, 25.0, "lawnmower")
    src = json.loads((planner.MISSIONS_DIR / Path(r["folder"]).name / "source.json").read_text())
    assert src["kml"] == k["path"] and src["drones"] == 2

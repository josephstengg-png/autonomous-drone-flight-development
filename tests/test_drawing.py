"""Drawing the area by hand (Phase A2): checks while drawing, and the KML round-trip into the planner."""
import pytest

from artex.planner import gs_planner as planner
from artex.planner.kml_parser import parse_kml

FENCE = [[1.403557, 103.949161], [1.403557, 103.952761], [1.407177, 103.952761], [1.407177, 103.949161]]
AO = [[1.405447, 103.950061], [1.405447, 103.951861], [1.406887, 103.951861], [1.406887, 103.950061]]
LAUNCH = [1.405367, 103.950961]


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "OPS_DIR", tmp_path / "ops")
    monkeypatch.setattr(planner, "MISSIONS_DIR", tmp_path / "missions")
    return tmp_path


def test_a_good_drawing_passes_and_bad_ones_say_why():
    assert planner.check_drawing(FENCE, AO, LAUNCH) == []
    assert "inside the geofence" in planner.check_drawing(FENCE, [[p[0] + 0.01, p[1]] for p in AO], LAUNCH)[0]
    assert "launch point must be inside" in planner.check_drawing(FENCE, AO, [1.5, 103.9])[0]
    assert "at least 3" in planner.check_drawing(FENCE[:2], AO, LAUNCH)[0]
    bow = [FENCE[0], FENCE[2], FENCE[1], FENCE[3]]                  # a figure of eight
    assert "cross" in planner.check_drawing(bow, AO, LAUNCH)[0]
    assert "Place the launch point" in planner.check_drawing(FENCE, AO, None)[0]


def test_a_drawn_area_is_the_same_to_the_planner_as_the_kml_it_came_from(dirs):
    r = planner.save_drawing("Seletar drawn", FENCE, AO, LAUNCH)
    assert r["path"] == "ops/seletar-drawn.kml"
    m = parse_kml(dirs / "ops" / "seletar-drawn.kml")
    assert len(m.geo_fence.coordinates) == 5 and len(m.ops_area.coordinates) == 5      # closed rings
    assert abs(m.gcs_location.latitude - 1.405367) < 1e-6
    plan = planner.run_planner(r["path"], "drawn", 2, 30.0, 25.0, "lawnmower")
    assert plan["ok"] and len(plan["plans"]) == 2
    back = planner.read_area(r["path"])
    assert len(back["fence"]) == 4 and len(back["ao"]) == 4 and abs(back["launch"][1] - 103.950961) < 1e-6


def test_a_bad_drawing_writes_nothing(dirs):
    with pytest.raises(ValueError):
        planner.save_drawing("bad", FENCE, [[1.5, 103.9], [1.5, 103.91], [1.51, 103.91]], LAUNCH)
    assert not (dirs / "ops").exists() or not list((dirs / "ops").glob("*.kml"))

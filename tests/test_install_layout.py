"""Installed-app layout: data apart from code, a starter set on first run, the site's own aircraft."""
from artex.common import layout
from artex.console import gs_core as core


def test_first_run_copies_the_starter_set_and_never_overwrites(tmp_path, monkeypatch):
    data = tmp_path / "artex-data"
    monkeypatch.setattr(layout, "DATA", data)
    layout.ensure_data()
    assert (data / "ops" / "sample-camp.kml").exists() and (data / "missions" / "sample" / "mission_drones" / "drone-1.plan").exists()
    assert (data / "site-examples" / "aircraft.example.yaml").exists() and (data / "config.example.json").exists()
    (data / "ops" / "sample-camp.kml").write_text("mine")
    layout.ensure_data()
    assert (data / "ops" / "sample-camp.kml").read_text() == "mine"


def test_a_checkout_uses_itself_as_the_data_root():
    assert layout.DATA == layout.ROOT and core.ROOT == layout.DATA


def test_a_site_aircraft_file_replaces_the_lab_aircraft(tmp_path, monkeypatch):
    (tmp_path / "aircraft.yaml").write_text("aircraft:\n  201:\n    host: edge-a\n    fleet: fleet-a.yaml\n")
    monkeypatch.setattr(core, "ROOT", tmp_path)
    saved = dict(core.AIRCRAFT)
    try:
        core._site_aircraft()
        assert list(core.AIRCRAFT) == [201] and core.AIRCRAFT[201]["host"] == "edge-a"
        assert core.AIRCRAFT[201]["radio"].startswith("udpout://")
    finally:
        core.AIRCRAFT.clear(); core.AIRCRAFT.update(saved)


def test_the_wheel_ships_the_bundled_map_and_3d_libraries():
    """16 Nov 2026: package-data had only "static/*", so installed copies had no Leaflet / three.js (no maps, no 3D)."""
    from pathlib import Path
    toml = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert '"static/**/*"' in toml
    vendor = Path(__file__).resolve().parents[1] / "artex" / "console" / "static" / "vendor"
    assert (vendor / "leaflet" / "leaflet.js").exists() and (vendor / "three" / "three.module.min.js").exists()

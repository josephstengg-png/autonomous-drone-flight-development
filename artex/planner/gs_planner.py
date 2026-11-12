"""The ground station's planner step (10 Nov 2026): upload a KML, run the existing planner, get one plan per drone.

    save_kml(name, text)       -> ops/<name>.kml, after checking the planner can read it (GCSlocation, GeoFence, OpsArea)
    run_planner(kml, site, n, altitude, overlap, scan)
                               -> missions/<site>-<date>/mission.plan and mission_drones/drone-N.plan, or the reason not

It runs `python -m flight_planner.cli` exactly as on the command line, in the repo's venv, so the guardrail
(legs inside the fence, survey inside the OpsArea, no two drones able to meet) decides as it always has. A refusal is
reported, never worked around. Nothing is overwritten: a KML name or a mission folder that exists gets a new name.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from artex.console import gs_core as core

OPS_DIR = core.ROOT / "ops"
MISSIONS_DIR = core.ROOT / "missions"
KML_MAX_BYTES = 5 * 1024 * 1024
PLAN_TIMEOUT_S = 180
SCANS = ("lawnmower", "voronoi")


def python() -> str:
    from artex.common.layout import ROOT as code_root
    venv = code_root / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def _env() -> dict:
    import os
    from artex.common.layout import ROOT as code_root
    return {**os.environ, "PYTHONPATH": str(code_root)}


def kml_summary(path: Path) -> dict:
    """What the planner sees in a KML, or the planner's own complaint. Runs in the venv, like the planner."""
    code = ("import json,sys; from artex.planner.kml_parser import parse_kml; m=parse_kml(sys.argv[1]); "
            "print(json.dumps(dict(gcs=[m.gcs_location.latitude, m.gcs_location.longitude], "
            "fence_points=len(m.geo_fence.coordinates), ops_points=len(m.ops_area.coordinates))))")
    r = subprocess.run([python(), "-c", code, str(path)], capture_output=True, text=True, timeout=30, env=_env(),
                       cwd=str(core.ROOT))
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()
        return dict(ok=False, error=tail[-1] if tail else "the planner could not read this KML")
    try:
        return dict(ok=True, **json.loads(r.stdout.strip().splitlines()[-1]))
    except (ValueError, IndexError):
        return dict(ok=False, error="unexpected reply from the KML reader")


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(core.ROOT))
    except ValueError:
        return str(p)


def list_kml() -> list[dict]:
    return [dict(path=f"ops/{f.name}", name=f.stem) for f in sorted(OPS_DIR.glob("*.kml"))]


def _free(base: Path, suffix: str = "") -> Path:
    p = base.with_name(base.name + suffix)
    n = 2
    while p.exists():
        p = base.with_name(f"{base.name}-{n}{suffix}")
        n += 1
    return p


def save_kml(name: str, text: str) -> dict:
    """Store an uploaded KML under ops/ if the planner can read it. Never overwrites."""
    data = (text or "").encode()
    if not data.strip():
        raise ValueError("The file is empty.")
    if len(data) > KML_MAX_BYTES:
        raise ValueError(f"The file is over {KML_MAX_BYTES // (1024 * 1024)} MB; a sector KML is a few kB.")
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.I):
        raise ValueError("The KML carries a DOCTYPE/ENTITY declaration; export it again without one.")
    stem = core.slug(re.sub(r"\.kml$", "", name or "", flags=re.I)) or "sector"
    OPS_DIR.mkdir(exist_ok=True)
    tmp = OPS_DIR / f".upload-{int(time.time() * 1000)}.kml"
    tmp.write_bytes(data)
    try:
        info = kml_summary(tmp)
        if not info["ok"]:
            raise ValueError(f"The planner cannot use this KML: {info['error']}. It needs placemarks named "
                             "GCSlocation (a point), GeoFence and OpsArea (polygons).")
        dest = _free(OPS_DIR / stem, ".kml")
        tmp.rename(dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return dict(path=f"ops/{dest.name}", name=dest.stem, **{k: v for k, v in info.items() if k != "ok"})


def validate_request(kml: str, n: int, altitude: float, overlap: float, scan: str) -> list[str]:
    e = []
    if not re.fullmatch(r"ops/[A-Za-z0-9._ -]+\.kml", kml or "") or not (OPS_DIR / Path(kml).name).is_file():
        e.append("Choose a KML from ops/ (upload one first).")
    if not (isinstance(n, int) and 1 <= n <= 8):
        e.append("Number of drones must be 1 to 8.")
    if not (altitude == altitude and 10 <= altitude <= 120):
        e.append("Altitude must be 10 to 120 m (the camera model needs a real survey height).")
    if not (overlap == overlap and 0 <= overlap <= 80):
        e.append("Overlap must be 0 to 80 %.")
    if scan not in SCANS:
        e.append("Scan must be lawnmower or voronoi.")
    return e


def run_planner(kml: str, site: str, n: int, altitude: float, overlap: float, scan: str) -> dict:
    errs = validate_request(kml, n, altitude, overlap, scan)
    if errs:
        return dict(ok=False, error=" ".join(errs))
    folder = _free(MISSIONS_DIR / f"{core.slug(site) or core.slug(Path(kml).stem)}-{time.strftime('%Y-%m-%d')}")
    folder.mkdir(parents=True)
    base = core.ROOT / "config.example.json"
    if not base.exists():
        base = Path(__file__).resolve().parents[1] / "starter" / "config.example.json"
    cfg = dict(json.loads(base.read_text()),
               number_of_drones=n, scan_altitude_m=altitude, scan_overlap_percent=overlap, scan_type=scan)
    cfg.setdefault("ollama", {})["enabled"] = False
    (folder / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    (folder / "source.json").write_text(json.dumps(dict(kml=f"ops/{Path(kml).name}", drones=n, altitude_m=altitude,
                                                        overlap_pct=overlap, scan=scan,
                                                        planned=time.strftime("%Y-%m-%d %H:%M:%S")), indent=1) + "\n")
    argv = [python(), "-m", "artex.planner.cli", str(OPS_DIR / Path(kml).name), "--config", str(folder / "config.json"),
            "--output", str(folder / "mission.plan"), "--drone-plans-dir", str(folder / "mission_drones")]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=PLAN_TIMEOUT_S, env=_env(), cwd=str(core.ROOT))
    except subprocess.TimeoutExpired:
        return dict(ok=False, folder=_rel(folder), error=f"the planner took over {PLAN_TIMEOUT_S} s")
    out = (r.stdout or "") + (r.stderr or "")
    plans = []
    for f in sorted((folder / "mission_drones").glob("drone-*.plan")):
        rel = _rel(f)
        try:
            items = json.loads(f.read_text())["mission"]["items"]
            top = max([float((it.get("params") or [0] * 7)[6] or 0) for it in items
                       if int(it.get("command", 0)) in (16, 22)] or [0.0])
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            items, top = [], None
        plans.append(dict(path=rel, items=len(items), top_alt_m=round(top, 1) if top is not None else None))
    refusal = next((ln for ln in out.splitlines() if "Guardrail rejected" in ln), None)
    ok = r.returncode == 0 and len(plans) == n
    return dict(ok=ok, folder=_rel(folder), plans=plans,
                camera=[ln for ln in out.splitlines() if ln.startswith(("[camera]", "[layers]"))],
                warnings=[ln[len("warning: "):] for ln in out.splitlines() if ln.startswith("warning: ")],
                error=None if ok else (refusal or (out.strip().splitlines() or ["the planner failed"])[-1]),
                command=" ".join(_rel(Path(a)) if a.startswith("/") else a for a in argv[1:]))


# ---- seeing a mission: the map on the Profiles page ---------------------------------------

NAV_WITH_POSITION = (16, 17, 18, 19, 21, 22)       # waypoint, loiters, land, takeoff
RTL = 20


def list_missions() -> list[dict]:
    """Every planning run under missions/ that has per-drone plans."""
    out = []
    for d in sorted({p.parent.parent for p in MISSIONS_DIR.glob("**/mission_drones/drone-*.plan")}):
        src = d / "source.json"
        out.append(dict(folder=_rel(d), name=_rel(d).split("missions/", 1)[-1],
                        drones=len(list((d / "mission_drones").glob("drone-*.plan"))), source=src.exists()))
    return out


def _km(pts: list[list[float]]) -> float:
    import math
    tot = 0.0
    for (a, b), (c, d) in zip(pts, pts[1:]):
        dy = (c - a) * 111.32
        dx = (d - b) * 111.32 * math.cos(math.radians((a + c) / 2))
        tot += math.hypot(dx, dy)
    return round(tot, 2)


def kml_polygons(path: Path) -> dict:
    code = ("import json,sys; from artex.planner.kml_parser import parse_kml; m=parse_kml(sys.argv[1]); "
            "f=lambda r:[[c.latitude,c.longitude] for c in r.coordinates]; "
            "print(json.dumps(dict(fence=f(m.geo_fence), ops=f(m.ops_area), gcs=[m.gcs_location.latitude, m.gcs_location.longitude])))")
    r = subprocess.run([python(), "-c", code, str(path)], capture_output=True, text=True, timeout=30, env=_env(),
                       cwd=str(core.ROOT))
    try:
        return json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else {}
    except (ValueError, IndexError):
        return {}


def onboard_records(run_dir: Path | None = None) -> dict[str, dict]:
    """plan path -> the newest fleet run in which an agent read that mission back from an aircraft."""
    found: dict[str, dict] = {}
    for f in sorted((run_dir or core.RUN_DIR).glob("*.json"), reverse=True):
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if rec.get("kind") != "fleet":
            continue
        for sid, v in ((rec.get("result") or {}).get("aircraft") or {}).items():
            plan = v.get("plan")
            chk = next((c for c in v.get("checks") or [] if c.get("name") == "mission_uploaded"), None)
            if plan and chk and chk.get("ok") and plan not in found:
                found[plan] = dict(sysid=int(sid), run=rec.get("id"), when=rec.get("started"), detail=chk.get("detail"),
                                   mode=rec.get("mode"))
    return found


def mission_view(folder: str) -> dict:
    """Everything the map needs for one planning run: fence, OpsArea (if the KML is known), home, each drone's path."""
    if not re.fullmatch(r"missions/[A-Za-z0-9._/ -]+", folder or "") or ".." in folder:
        raise ValueError("Choose a mission folder under missions/.")
    d = MISSIONS_DIR / folder.split("missions/", 1)[1]
    plans = sorted((d / "mission_drones").glob("drone-*.plan"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    if not plans:
        raise ValueError(f"No drone plans in {folder}.")
    onboard = onboard_records()
    out = dict(folder=folder, drones=[], fence=[], ops=[], home=None, source=None)
    for p in plans:
        plan = json.loads(p.read_text())
        mi = plan["mission"]
        home = (mi.get("plannedHomePosition") or [None, None])[:2]
        out["home"] = out["home"] or home
        if not out["fence"]:
            out["fence"] = [poly["polygon"] for poly in (plan.get("geoFence") or {}).get("polygons", [])
                            if poly.get("inclusion", True)]
        pts, top = [], 0.0
        for it in mi["items"]:
            cmd, prm = int(it.get("command", 0)), it.get("params") or []
            if cmd in NAV_WITH_POSITION and len(prm) >= 7 and prm[4] is not None and prm[5] is not None:
                pts.append([float(prm[4]), float(prm[5]), float(prm[6] or 0)])
                top = max(top, float(prm[6] or 0))
            elif cmd == RTL and home[0] is not None:
                pts.append([home[0], home[1], 0.0])
        rel = _rel(p)
        out["drones"].append(dict(path=rel, name=p.stem, items=len(mi["items"]), waypoints=len(pts), top_alt_m=round(top, 1),
                                  km=_km([q[:2] for q in pts]), cruise_m_s=mi.get("cruiseSpeed"), points=pts,
                                  onboard=onboard.get(rel)))
    src = d / "source.json"
    if src.exists():
        try:
            out["source"] = json.loads(src.read_text())
            k = OPS_DIR / Path(out["source"]["kml"]).name
            if k.exists():
                poly = kml_polygons(k)
                out["ops"] = [poly.get("ops", [])] if poly.get("ops") else []
                if poly.get("fence") and not out["fence"]:
                    out["fence"] = [poly["fence"]]
        except (OSError, ValueError, KeyError):
            pass
    return out


# ---- drawing the area by hand (Phase A2, 12 Nov 2026) --------------------------------------

def _inside(pt: list[float], poly: list[list[float]]) -> bool:
    """Ray casting on (lat, lon); fine at sector scale."""
    y, x = pt
    hit = False
    for (y1, x1), (y2, x2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-15) + x1:
            hit = not hit
    return hit


def _crosses(a, b, c, d) -> bool:
    def orient(p, q, r):
        return (q[1] - p[1]) * (r[0] - p[0]) - (q[0] - p[0]) * (r[1] - p[1])
    return (orient(a, b, c) > 0) != (orient(a, b, d) > 0) and (orient(c, d, a) > 0) != (orient(c, d, b) > 0)


def _self_intersects(poly: list[list[float]]) -> bool:
    edges = list(zip(poly, poly[1:] + poly[:1]))
    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges):
            if abs(i - j) > 1 and {i, j} != {0, len(edges) - 1} and _crosses(a, b, c, d):
                return True
    return False


def check_drawing(fence: list, ao: list, launch: list | None) -> list[str]:
    """Reasons a drawn area cannot be planned. Coordinates are [lat, lon]."""
    e = []
    def ok_poly(p, name):
        if not isinstance(p, list) or len(p) < 3:
            e.append(f"Draw the {name} with at least 3 corners.")
            return False
        try:
            pts = [[float(a), float(b)] for a, b in p]
        except (TypeError, ValueError):
            e.append(f"The {name} has a corner that is not a coordinate.")
            return False
        if any(not (-90 <= a <= 90 and -180 <= b <= 180) for a, b in pts):
            e.append(f"The {name} has a corner off the map.")
            return False
        if _self_intersects(pts):
            e.append(f"The {name}'s edges cross each other; redraw it without a figure of eight.")
            return False
        return True
    f_ok, a_ok = ok_poly(fence, "geofence"), ok_poly(ao, "area of operation")
    if f_ok and a_ok:
        F = [[float(a), float(b)] for a, b in fence]
        A = [[float(a), float(b)] for a, b in ao]
        if not all(_inside(p, F) for p in A) or any(_crosses(a, b, c, d) for a, b in zip(A, A[1:] + A[:1])
                                                    for c, d in zip(F, F[1:] + F[:1])):
            e.append("The area of operation must lie entirely inside the geofence.")
        if not launch or len(launch) != 2:
            e.append("Place the launch point.")
        elif not _inside([float(launch[0]), float(launch[1])], F):
            e.append("The launch point must be inside the geofence.")
    return e


def drawing_kml(name: str, fence: list, ao: list, launch: list) -> str:
    ring = lambda pts: " ".join(f"{float(lon):.8f},{float(lat):.8f},0" for lat, lon in list(pts) + [pts[0]])  # noqa: E731
    esc = lambda t: (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))  # noqa: E731
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>{esc(name)}</name>
  <Placemark><name>GCSlocation</name>
    <description>Launch point, drawn in the ARTEX console</description>
    <Point><coordinates>{float(launch[1]):.8f},{float(launch[0]):.8f},0</coordinates></Point></Placemark>
  <Placemark><name>GeoFence</name>
    <description>Drawn in the ARTEX console</description>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(fence)}
    </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
  <Placemark><name>OpsArea</name>
    <description>Area of operation, drawn in the ARTEX console</description>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(ao)}
    </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
</Document>
</kml>
"""


def save_drawing(name: str, fence: list, ao: list, launch: list) -> dict:
    """A hand-drawn area becomes a KML under ops/, through the same checked path as an upload."""
    errs = check_drawing(fence, ao, launch)
    if errs:
        raise ValueError(" ".join(errs))
    return save_kml(name or "drawn-area", drawing_kml(name or "Drawn area", fence, ao, launch))


def read_area(kml: str) -> dict:
    """An existing sector KML as polygons, so the editor can show or modify it."""
    if not re.fullmatch(r"ops/[A-Za-z0-9._ -]+\.kml", kml or "") or not (OPS_DIR / Path(kml).name).is_file():
        raise ValueError("Choose a KML from ops/.")
    poly = kml_polygons(OPS_DIR / Path(kml).name)
    if not poly:
        raise ValueError("The planner could not read that KML.")
    return dict(kml=kml, fence=poly["fence"][:-1] if poly["fence"][:1] == poly["fence"][-1:] else poly["fence"],
                ao=poly["ops"][:-1] if poly["ops"][:1] == poly["ops"][-1:] else poly["ops"], launch=poly["gcs"])

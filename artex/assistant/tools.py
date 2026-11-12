"""What the assistant may call. Phase B: READ and DRAFT only. Nothing here changes anything: drafts come back as
proposals the operator applies (or not) in the console, where the usual validation, risks and authorisation apply.

Each tool: a JSON schema for the model, a Python function, and a kind ("read" | "draft" | "act"). The policy
(artex.assistant.policy) decides which kinds are allowed; "act" tools do not exist until Phase C.
"""
from __future__ import annotations

import json
from pathlib import Path

from artex.common.layout import DATA, ROOT
from artex.console import gs_core as core
from artex.console import gs_fleet as fleet
from artex.planner import gs_planner as planner
from artex.planner.rag import TemplateStore, _split_sections

MAX_RESULT_CHARS = 3500


def _docs_store() -> TemplateStore:
    chunks = []
    seen = set()
    for p in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), DATA / "README.md", *sorted((DATA / "docs").glob("*.md"))]:
        if p.name in seen:
            continue
        seen.add(p.name)
        if p.exists():
            chunks.extend(_split_sections(p))
    return TemplateStore(chunks)


_STORE: TemplateStore | None = None


def _run_summary(r: dict) -> dict:
    res = r.get("result") or {}
    keep = ("outcome", "passed", "error", "takeover_source", "takeover_after_s", "arm_commands_sent", "downlink",
            "rx_msgs_per_s", "stale_arm", "aircraft")
    p = r.get("profile") or {}
    return dict(id=r.get("id"), kind=r.get("kind", "single"), started=r.get("started"), mode=r.get("mode"),
                profile=p.get("name"), aircraft=p.get("sysids") or ([m["sysid"] for m in p.get("aircraft", [])] or [p.get("sysid")]),
                operator=r.get("operator"), pilots=r.get("pilots"), state=r.get("state"),
                result={k: res[k] for k in keep if k in res},
                vehicle_text=(res.get("vehicle_text") or [])[:20])


# ---- read -------------------------------------------------------------------------------------

def list_profiles(kind: str = "single") -> list[dict]:
    if kind == "fleet":
        return [dict(id=p["id"], name=p["name"], mode=p["mode"], flight_type=p["flight_type"],
                     aircraft=[m["sysid"] for m in p["aircraft"]]) for p in fleet.list_profiles()]
    return [dict(id=p["id"], name=p["name"], aircraft=p["sysids"], flight_type=p["flight_type"], link=p["link"],
                 stage=p["stage"]) for p in core.list_profiles()]


def get_profile(id: str, kind: str = "single") -> dict:
    src = fleet.list_profiles() if kind == "fleet" else core.list_profiles()
    p = next((x for x in src if x["id"] == id or x["name"] == id), None)
    if not p:
        return dict(error=f"no {kind} profile {id!r}")
    errs = fleet.validate(p) if kind == "fleet" else core.validate(p)
    return dict(profile=p, errors=errs)


def list_runs(limit: int = 10) -> list[dict]:
    return core.list_runs()[: max(1, min(int(limit), 30))]


def get_run(id: str) -> dict:
    r = core.load_run(id)
    return _run_summary(r) if r else dict(error=f"no run {id!r}")


def list_missions() -> list[dict]:
    return planner.list_missions()


def get_mission(folder: str) -> dict:
    v = planner.mission_view(folder)
    return dict(folder=v["folder"], source=v["source"],
                drones=[{k: d[k] for k in ("name", "path", "items", "top_alt_m", "km", "onboard")} for d in v["drones"]])


def list_areas() -> list[dict]:
    return planner.list_kml()


def search_docs(query: str) -> list[dict]:
    global _STORE
    _STORE = _STORE or _docs_store()
    return [dict(source=Path(h.source).name, heading=h.heading, text=h.text[:900]) for h in _STORE.retrieve(query, top_k=4)]


# ---- draft (validated proposals; nothing is saved or started) -----------------------------------

def propose_planner_settings(kml: str, drones: int, altitude_m: float = 30.0, overlap_pct: float = 25.0,
                             scan: str = "lawnmower", site: str = "") -> dict:
    errs = planner.validate_request(kml, int(drones), float(altitude_m), float(overlap_pct), scan)
    return dict(proposal="planner_settings", kml=kml, drones=int(drones), altitude_m=float(altitude_m),
                overlap_pct=float(overlap_pct), scan=scan, site=site or Path(kml).stem, errors=errs,
                note="A proposal for the planner form. The operator runs the planner; its guardrail decides.")


def propose_fleet_profile(name: str, aircraft: list[dict], mode: str = "upload", flight_type: str = "bench") -> dict:
    p = fleet.normalise(dict(name=name, aircraft=aircraft, mode=mode, flight_type=flight_type))
    return dict(proposal="fleet_profile", profile=p, errors=fleet.validate(p),
                note="A draft. The operator opens it in the Fleet editor, checks it and saves it.")


def propose_bench_profile(name: str, sysids: list[int], stage: str = "arm", link: str = "radio") -> dict:
    p = core.normalise(dict(name=name, sysids=sysids, flight_type="bench", link=link, stage=stage))
    return dict(proposal="bench_profile", profile=p, errors=core.validate(p),
                note="A props-off bench draft. The operator opens it in the profile editor, checks it and saves it.")


def _fn(name, desc, props, required=()):
    return dict(type="function", function=dict(name=name, description=desc,
                                               parameters=dict(type="object", properties=props, required=list(required))))


S, I, N = dict(type="string"), dict(type="integer"), dict(type="number")
TOOLS = {
    "list_profiles": ("read", list_profiles, _fn("list_profiles", "List saved profiles. kind = single (offboard/bench tests) or fleet (multi-aircraft waypoint runs).",
                                                 dict(kind=dict(type="string", enum=["single", "fleet"])))),
    "get_profile": ("read", get_profile, _fn("get_profile", "One profile by id or name, with any validation errors.", dict(id=S, kind=dict(type="string", enum=["single", "fleet"])), ["id"])),
    "list_runs": ("read", list_runs, _fn("list_runs", "The most recent run records, newest first.", dict(limit=I))),
    "get_run": ("read", get_run, _fn("get_run", "One run record summarised: outcome, errors, takeover, the aircraft's own messages.", dict(id=S), ["id"])),
    "list_missions": ("read", list_missions, _fn("list_missions", "Planned missions (folders under missions/).", {})),
    "get_mission": ("read", get_mission, _fn("get_mission", "One planned mission: each drone's plan, items, top altitude, path length, and whether it is confirmed on board an aircraft.", dict(folder=S), ["folder"])),
    "list_areas": ("read", list_areas, _fn("list_areas", "Saved operating areas (sector KMLs under ops/).", {})),
    "search_docs": ("read", search_docs, _fn("search_docs", "Search the product documentation and safety notes.", dict(query=S), ["query"])),
    "propose_planner_settings": ("draft", propose_planner_settings, _fn(
        "propose_planner_settings", "Turn the operator's directive into planner settings for them to review. Does not run the planner.",
        dict(kml=dict(type="string", description="an area from list_areas, e.g. ops/seletar-west-camp.kml"), drones=I,
             altitude_m=N, overlap_pct=N, scan=dict(type="string", enum=["lawnmower", "voronoi"]), site=S), ["kml", "drones"])),
    "propose_fleet_profile": ("draft", propose_fleet_profile, _fn(
        "propose_fleet_profile", "Draft a fleet profile for the operator to review and save. aircraft = [{sysid, plan, link}].",
        dict(name=S, aircraft=dict(type="array", items=dict(type="object")), mode=dict(type="string", enum=["upload", "arm", "mission"]),
             flight_type=dict(type="string", enum=["bench", "props_on"])), ["name", "aircraft"])),
    "propose_bench_profile": ("draft", propose_bench_profile, _fn(
        "propose_bench_profile", "Draft a props-off bench test on one or more aircraft for the operator to review and save.",
        dict(name=S, sysids=dict(type="array", items=I), stage=dict(type="string", enum=list(core.STAGES)), link=dict(type="string", enum=["radio", "usb"])),
        ["name", "sysids"])),
}


def schemas(kinds: set[str], registry: dict | None = None) -> list[dict]:
    return [t[2] for t in (registry or TOOLS).values() if t[0] in kinds]


def run_tool(name: str, args: dict, registry: dict | None = None) -> str:
    """Call a tool and return compact JSON for the model (truncated)."""
    fn = (registry or TOOLS)[name][1]
    try:
        out = fn(**{k: v for k, v in (args or {}).items() if not k.startswith("_")})
    except TypeError as exc:
        out = dict(error=f"bad arguments for {name}: {exc}")
    except (ValueError, OSError, KeyError) as exc:
        out = dict(error=str(exc))
    text = json.dumps(out, default=str)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + '..."(truncated)"'

"""MOCK of the planned local API (docs/local-app-plan.md). Same method names, arguments, results and refusals as the real
`artex.console.local_api.Console` will have, but nothing real behind it: simulated aircraft, in memory, no files, no ssh,
no radio. For trying the interface, writing scripts and UI work before the real API exists.

    python -m artex.console.local_api_mock          # a scripted two-aircraft fleet run, printed step by step

    from artex.console.local_api_mock import MockConsole, ApiError
    c = MockConsole()
    c.first_operator("Ops Lead", "482916"); c.sign_in("Ops Lead", "482916")
    ...

The rules it enforces are the console's rules, deliberately: sign-in, one pilot per aircraft, risks accepted per profile,
a 15 min run token bound to the profile, READY before ARM ALL, START ALL within 8 s of the first arm, stop always allowed.
Time is simulated: each status call advances the run one step, so a script runs instantly and deterministically.
"""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field

TOKEN_TTL_S = 15 * 60
START_WINDOW_S = 8.0
ROLE_RANK = {"observer": 0, "operator": 1, "admin": 2}
STOP_WORDS = ("HOLD", "RTL", "LAND")


class ApiError(Exception):
    """A refusal. `status` follows the HTTP API (400 bad request, 401 not signed in, 403 not allowed / not confirmed,
    409 wrong state) so scripts, the window bridge and the web server report the same thing."""

    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status, self.message = status, message


@dataclass
class _Aircraft:
    sysid: int
    host: str
    armed: bool = False
    mode: str = "AUTO LOITER (HOLD)"
    alt_m: float = 0.0
    waypoint: int = 0
    items: int = 11
    battery_pct: int = 100


@dataclass
class _Run:
    id: str
    kind: str
    profile: dict
    state: str = "preparing"           # preparing -> ready -> armed -> launching -> flying -> done | fault | not_ready
    feed: list = field(default_factory=list)
    t_armed: float | None = None
    step: int = 0
    result: dict | None = None


def _hash(p: dict) -> str:
    return hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()[:16]


class MockConsole:
    """Every method the real local API will have. Times are simulated (`self.now`)."""

    def __init__(self, aircraft: dict[int, str] | None = None, *, fault_on: int | None = None):
        self.now = 1_800_000_000.0
        self.ac = {s: _Aircraft(s, h) for s, h in (aircraft or {113: "jetson-2", 115: "jetson-1"}).items()}
        self.fault_on = fault_on                     # sysid that faults mid-mission (to exercise HOLD-the-others)
        self.operators: dict[str, tuple[str, str]] = {}   # name -> (pin, role)
        self.session: dict | None = None
        self.tokens: dict[str, dict] = {}
        self.profiles: dict[str, dict] = {"single": {}, "fleet": {}}
        self.runs: dict[str, _Run] = {}
        self.active: _Run | None = None
        self.areas = {"ops/sample-camp.kml": dict(fence_points=5, ops_points=5, gcs=[1.4172, 91.8636])}
        self.missions: dict[str, dict] = {}

    # ---- helpers -------------------------------------------------------------------------------------------------
    def _need(self, role: str) -> dict:
        if not self.session:
            raise ApiError(401, "Sign in first.")
        if ROLE_RANK[self.session["role"]] < ROLE_RANK[role]:
            raise ApiError(403, f"Needs the {role} role; you are {self.session['role']}.")
        return self.session

    def _log(self, run: _Run, text: str, sysid: int | None = None, bad: bool = False) -> None:
        run.feed.append(dict(n=len(run.feed), t=self.now, sysid=sysid, text=text, bad=bad))

    # ---- system ------------------------------------------------------------------------------------------------
    def state(self) -> dict:
        return dict(demo=True, mock=True, aircraft=sorted(self.ac), run_active=bool(self.active and self._live()))

    def version(self) -> dict:
        return dict(version="0.1.0-mock", fingerprint="mock000000000000")

    # ---- sessions ----------------------------------------------------------------------------------------------
    def first_operator(self, name: str, pin: str) -> dict:
        if self.operators:
            raise ApiError(409, "Operators already exist; an admin adds people.")
        self._check_pin(pin)
        self.operators[name] = (pin, "admin")
        return dict(ok=True, role="admin")

    def add_operator(self, name: str, pin: str, role: str = "operator") -> dict:
        self._need("admin")
        if role not in ROLE_RANK:
            raise ApiError(400, "Role must be observer, operator or admin.")
        self._check_pin(pin)
        self.operators[name] = (pin, role)
        return dict(ok=True)

    @staticmethod
    def _check_pin(pin: str) -> None:
        if not (pin.isdigit() and 4 <= len(pin) <= 8):
            raise ApiError(400, "PIN must be 4 to 8 digits.")

    def sign_in(self, name: str, pin: str) -> dict:
        if self.operators.get(name, (None,))[0] != pin:
            raise ApiError(401, "Name or PIN not recognised.")
        self.session = dict(token=secrets.token_urlsafe(12), operator=name, role=self.operators[name][1],
                            pilots={}, accepted={})
        return dict(session=self.session["token"], operator=name, role=self.session["role"])

    def sign_out(self) -> dict:
        self.session = None
        return dict(ok=True)

    def session_info(self) -> dict:
        s = self.session
        return dict(signed_in=bool(s), operator=s and s["operator"], role=s and s["role"], pilots=s and s["pilots"])

    def set_crew(self, pilots: dict[int, str]) -> dict:
        s = self._need("operator")
        names = list(pilots.values())
        if len(set(names)) != len(names):
            raise ApiError(400, "One person cannot pilot two aircraft.")
        unknown = [k for k in pilots if int(k) not in self.ac]
        if unknown:
            raise ApiError(400, f"Unknown aircraft {unknown}.")
        s["pilots"] = {int(k): v for k, v in pilots.items()}
        return dict(pilots=s["pilots"])

    # ---- preflight ---------------------------------------------------------------------------------------------
    def preflight(self, sysid: int) -> dict:
        self._need("operator")
        a = self.ac.get(sysid) or self._bad(400, f"Unknown aircraft {sysid}.")
        go = not a.armed and a.battery_pct >= 30
        self.session.setdefault("preflight", {})[sysid] = self.now
        return dict(go=go, sysid=sysid, host=a.host, blockers=[] if go else ["aircraft is ALREADY ARMED"],
                    warnings=["MOCK: simulated result"], battery_pct=a.battery_pct, gps=dict(fix=3, sats=10))

    @staticmethod
    def _bad(status: int, msg: str):
        raise ApiError(status, msg)

    # ---- planner -----------------------------------------------------------------------------------------------
    def list_areas(self) -> list[dict]:
        return [dict(path=k, **v) for k, v in self.areas.items()]

    def draw_area(self, name: str, fence: list, ao: list, launch: list) -> dict:
        self._need("operator")
        if len(fence) < 3 or len(ao) < 3 or len(launch) != 2:
            raise ApiError(400, "Draw the fence and the area of operation with at least 3 corners, and place the launch point.")
        path = f"ops/{name.lower().replace(' ', '-')}.kml"
        self.areas[path] = dict(fence_points=len(fence) + 1, ops_points=len(ao) + 1, gcs=list(launch))
        return dict(path=path, name=name, gcs=list(launch))

    def plan_mission(self, kml: str, site: str, drones: int = 2, altitude_m: float = 30.0, overlap_pct: float = 25.0,
                     scan: str = "lawnmower") -> dict:
        self._need("operator")
        if kml not in self.areas:
            raise ApiError(400, f"No area {kml}; choose one from list_areas().")
        folder = f"missions/{site.lower().replace(' ', '-')}"
        plans = [dict(path=f"{folder}/mission_drones/drone-{i}.plan", items=11, top_alt_m=altitude_m + 2 * (i - 1))
                 for i in range(1, drones + 1)]
        self.missions[folder] = dict(plans=plans, kml=kml)
        return dict(ok=True, folder=folder, plans=plans, warnings=["MOCK: no real planning"])

    def list_missions(self) -> list[dict]:
        return [dict(folder=k, drones=len(v["plans"])) for k, v in self.missions.items()]

    # ---- profiles ----------------------------------------------------------------------------------------------
    def save_profile(self, kind: str, profile: dict) -> dict:
        self._need("operator")
        if kind == "fleet" and len(profile.get("aircraft", [])) < 2:
            raise ApiError(400, "A fleet run needs at least two aircraft.")
        pid = profile.get("name", "").lower().replace(" ", "-") or self._bad(400, "Give the profile a name.")
        self.profiles[kind][pid] = copy.deepcopy(profile)
        return dict(id=pid, **profile)

    def list_profiles(self, kind: str = "fleet") -> list[dict]:
        return [dict(id=k, **v) for k, v in self.profiles[kind].items()]

    def preview(self, kind: str, profile: dict) -> dict:
        risks = ["multi_untested", "separation", "motors"] if kind == "fleet" else ["drift", "untethered"]
        acc = (self.session or {}).get("accepted", {}).get(_hash(profile), [])
        sysids = [m["sysid"] for m in profile.get("aircraft", [])] or [profile.get("sysid")]
        return dict(errors=[], risks=risks, missing_accept=[r for r in risks if r not in acc],
                    missing_pilots=[s for s in sysids if s not in (self.session or {}).get("pilots", {})])

    def accept_risks(self, kind: str, profile: dict) -> dict:
        s = self._need("operator")
        s["accepted"][_hash(profile)] = self.preview(kind, profile)["risks"]
        return dict(accepted=s["accepted"][_hash(profile)])

    # ---- runs --------------------------------------------------------------------------------------------------
    def authorise(self, kind: str, profile: dict, confirm: str, delegate: bool = False) -> dict:
        """The press-and-hold, for scripts: `confirm` must be the word "AUTHORISE"."""
        self._need("operator")
        pv = self.preview(kind, profile)            # same order and codes as the console: preflight block (403),
        done = (self.session or {}).get("preflight", {})   # risks, pilots (409), then the word (403)
        no_pf = [s for s in ([m["sysid"] for m in profile.get("aircraft", [])] or [profile.get("sysid")]) if s not in done]
        if no_pf:
            raise ApiError(403, f"Blocked: Preflight GO needed on every aircraft (missing {no_pf}).")
        if pv["missing_accept"]:
            raise ApiError(409, f"Accept the risks first: {pv['missing_accept']} (accept_risks).")
        if pv["missing_pilots"]:
            raise ApiError(409, f"Name a pilot for {pv['missing_pilots']} (set_crew).")
        if confirm != "AUTHORISE":
            raise ApiError(403, 'Confirm with confirm="AUTHORISE" (props, area and pilots checked).')
        tok = secrets.token_urlsafe(16)
        self.tokens[tok] = dict(hash=_hash(profile), at=self.now, kind=kind, delegate=delegate)
        return dict(token=tok, ttl_s=TOKEN_TTL_S, delegated=delegate)

    def _token(self, token: str | None, profile: dict | None = None) -> dict:
        t = self.tokens.get(token or "")
        if not t or self.now - t["at"] > TOKEN_TTL_S:
            raise ApiError(403, "Authorisation expired or missing. Authorise again.")
        if profile is not None and t["hash"] != _hash(profile):
            raise ApiError(403, "The profile changed after authorising. Authorise again.")
        return t

    def _live(self) -> bool:
        return bool(self.active) and self.active.state not in ("done", "fault_done", "not_ready", "ended")

    def start(self, kind: str, profile: dict, token: str) -> dict:
        self._need("operator")
        self._token(token, profile)
        if self._live():
            raise ApiError(409, "A run is already active.")
        run = _Run(id=time.strftime("%Y%m%d-%H%M%S", time.gmtime(self.now)), kind=kind, profile=copy.deepcopy(profile))
        self.runs[run.id], self.active = run, run
        self._log(run, "version handshake: every companion runs this console's code")
        return dict(id=run.id)

    def status(self, since: int = 0) -> dict:
        """One poll. In the mock each call also advances the simulated run one step."""
        run = self.active
        if not run:
            return dict(state="none")
        self._advance(run)
        return dict(id=run.id, kind=run.kind, state=run.state, feed=run.feed[since:], result=run.result,
                    aircraft=[dict(sysid=a.sysid, armed=a.armed, mode=a.mode, alt_m=a.alt_m, waypoint=a.waypoint)
                              for a in self._members(run)],
                    window_s=(max(0.0, START_WINDOW_S - (self.now - run.t_armed))
                              if run.state == "armed" and run.t_armed else None))

    def _members(self, run: _Run) -> list[_Aircraft]:
        ids = [m["sysid"] for m in run.profile.get("aircraft", [])] or [run.profile.get("sysid")]
        return [self.ac[s] for s in ids]

    def arm_all(self, token: str, confirm: str) -> dict:
        self._need("operator")
        self._token(token)
        run = self.active
        if not run or run.state != "ready":
            raise ApiError(409, "Not every aircraft is READY.")
        if confirm != "ARM ALL":
            raise ApiError(403, 'Confirm with confirm="ARM ALL".')
        for a in self._members(run):
            a.mode, a.armed = "AUTO LOITER (HOLD)", True       # armed in Hold: arming never launches
        run.state, run.t_armed = "armed", self.now
        self._log(run, f"ALL ARMED in Hold. START ALL within {START_WINDOW_S:g} s of the first arm.")
        return dict(ok=True)

    def start_all(self, token: str, confirm: str) -> dict:
        self._need("operator")
        self._token(token)
        run = self.active
        if not run or run.state != "armed" or run.profile.get("mode") != "mission":
            raise ApiError(409, "Not every aircraft is armed, or this is not a mission run.")
        if confirm != "START ALL":
            raise ApiError(403, 'Confirm with confirm="START ALL".')
        if self.now - run.t_armed > START_WINDOW_S:
            self._disarm_all(run, "START ALL came after the window")
            raise ApiError(409, "Too late: the start window closed and everyone was disarmed.")
        run.state = "launching"
        self.tokens.pop(token, None)
        self._log(run, "START sent to all for one shared instant.")
        return dict(ok=True)

    def stop(self, word: str, target: int | str = "all") -> dict:
        """HOLD / RTL / LAND. Any signed-in role, including observer; never needs a token."""
        self._need("observer")
        word = word.upper()
        if word not in STOP_WORDS:
            raise ApiError(400, f"Stop word must be one of {STOP_WORDS}.")
        run = self.active
        if not run:
            raise ApiError(409, "No active run.")
        hit = [a for a in self._members(run) if target == "all" or a.sysid == int(target)]
        for a in hit:
            a.mode = {"HOLD": "AUTO LOITER (HOLD)", "RTL": "AUTO RTL", "LAND": "AUTO LAND"}[word]
        self._log(run, f"{word} sent to {[a.sysid for a in hit]}")
        return dict(sent=[a.sysid for a in hit])

    def cancel(self) -> dict:
        self._need("observer")
        run = self.active
        if not run:
            raise ApiError(409, "No active run.")
        if run.state in ("ready", "armed"):
            self._disarm_all(run, "operator cancelled")
        return dict(ok=True, state=run.state)

    def _disarm_all(self, run: _Run, why: str) -> None:
        for a in self._members(run):
            a.armed = False
        run.state, run.result = "ended", dict(outcome=f"not started -- {why}; disarmed", passed=False)
        self._log(run, f"DISARM ALL: {why}", bad=True)

    def list_runs(self) -> list[dict]:
        return [dict(id=r.id, kind=r.kind, state=r.state, outcome=(r.result or {}).get("outcome")) for r in self.runs.values()]

    # ---- the simulated aircraft ----------------------------------------------------------------------------------
    def _advance(self, run: _Run) -> None:
        self.now += 2.0
        run.step += 1
        ms = self._members(run)
        if run.state == "preparing" and run.step >= 2:
            run.state = "ready"
            for a in ms:
                self._log(run, f"ARTEX{a.sysid}: READY ({a.items} items on board, in Hold)", a.sysid)
        elif run.state == "armed" and run.t_armed and self.now - run.t_armed > START_WINDOW_S:
            self._disarm_all(run, "START ALL not pressed inside the window")
        elif run.state == "launching":
            run.state = "flying"
            for a in ms:
                a.mode = "AUTO MISSION"
        elif run.state in ("flying", "fault"):
            for a in ms:
                if not a.armed:
                    continue
                if a.mode == "AUTO MISSION":
                    a.alt_m, a.waypoint = 30.0, min(a.items - 1, a.waypoint + 2)
                    if self.fault_on == a.sysid and a.waypoint >= 3 and run.state != "fault":
                        run.state = "fault"
                        a.mode = "AUTO LAND"
                        self._log(run, f"ARTEX{a.sysid}: FAULT -- the companion's guard commanded LAND", a.sysid, True)
                        for o in ms:
                            if o is not a and o.armed:
                                o.mode = "AUTO LOITER (HOLD)"
                        self._log(run, "Others put in HOLD. Decide: RTL, LAND or take over by handset.", bad=True)
                    elif a.waypoint >= a.items - 1:
                        a.mode = "AUTO RTL"
                elif a.mode in ("AUTO RTL", "AUTO LAND"):
                    a.alt_m = max(0.0, a.alt_m - 15.0)
                    if a.alt_m == 0.0:
                        a.armed = False
                        self._log(run, f"ARTEX{a.sysid}: on the ground, disarmed", a.sysid)
            if not any(a.armed for a in ms):
                ok = run.state == "flying"
                run.result = dict(outcome="every aircraft completed its mission" if ok else "landed, with faults", passed=ok)
                run.state = "done" if ok else "fault_done"
                self._log(run, f"RUN ENDED: {run.result['outcome']}", bad=not ok)


def _demo() -> int:
    c = MockConsole()
    say = lambda *a: print(" ", *a)  # noqa: E731
    print("MOCK local API: a two-aircraft fleet mission, scripted")
    c.first_operator("Ops Lead", "482916")
    say("sign_in      ->", c.sign_in("Ops Lead", "482916"))
    say("set_crew     ->", c.set_crew({113: "Pilot A", 115: "Pilot B"}))
    for s in (113, 115):
        say(f"preflight({s}) ->", {k: v for k, v in c.preflight(s).items() if k in ("go", "blockers")})
    mission = c.plan_mission("ops/sample-camp.kml", "Sample survey", drones=2, altitude_m=30)
    prof = dict(name="Sample survey", mode="mission", flight_type="props_on", downlink="mission",
                aircraft=[dict(sysid=113, plan=mission["plans"][0]["path"], link="radio"),
                          dict(sysid=115, plan=mission["plans"][1]["path"], link="radio")])
    c.save_profile("fleet", prof)
    try:
        c.authorise("fleet", prof, confirm="AUTHORISE")
    except ApiError as e:
        say("authorise    -> refused:", e.message)
    say("accept_risks ->", c.accept_risks("fleet", prof))
    tok = c.authorise("fleet", prof, confirm="AUTHORISE")["token"]
    say("authorise    -> token issued, 15 min")
    say("start        ->", c.start("fleet", prof, tok))
    while c.status()["state"] != "ready":
        pass
    say("status       -> ready")
    say("arm_all      ->", c.arm_all(tok, confirm="ARM ALL"))
    say("start_all    ->", c.start_all(tok, confirm="START ALL"))
    since = 0
    while True:
        s = c.status(since)
        for f in s["feed"]:
            say(f"  feed: {f['text']}")
        since += len(s["feed"])
        if s["result"]:
            say("result       ->", s["result"])
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())

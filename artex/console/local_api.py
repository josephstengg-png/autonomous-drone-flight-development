"""The local API: drive the console from Python, in the same process, with no web server and no port.

    from artex.console.local_api import Console, ApiError
    c = Console.open(demo=True)                       # or Console(app) inside the desktop app
    c.sign_in("Ops Lead", "482916")
    c.set_crew({113: "Pilot A", 115: "Pilot B"})
    ...

Every method is one call to `gs_server.dispatch`, the router the desktop window and the web server use too, so a script
meets exactly the checks a click meets: sign-in and role, accepted risks, named pilots, a 15 min token bound to the
profile, the confirmation words ("AUTHORISE", "ARM ALL", "START ALL"), READY before ARM ALL, the START window. Stopping
(HOLD / RTL / LAND) never needs a token. Refusals raise ApiError with the HTTP API's status codes (docs/api.md).
The interface matches artex.console.local_api_mock, which runs without aircraft (docs/local-app-plan.md).
"""
from __future__ import annotations

import time
from urllib.parse import quote

from artex.common import layout
from artex.console import gs_server

KINDS = ("single", "fleet")


class ApiError(Exception):
    """A refusal: `status` (400 bad request, 401 not signed in, 403 not allowed or not confirmed, 404, 409 wrong state,
    429 too many PIN attempts) and the console's own message."""

    def __init__(self, status: int, message: str, body: dict | None = None):
        super().__init__(f"{status}: {message}")
        self.status, self.message, self.body = status, message, body or {}


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise ApiError(400, f"kind must be one of {KINDS}")
    return kind


class Console:
    """One signed-in operator's handle on the console. Not thread-safe; give each script its own."""

    def __init__(self, app: gs_server.App, session: str | None = None):
        self.app, self.session = app, session

    @classmethod
    def open(cls, demo: bool = False, require_signin: bool = True) -> "Console":
        """A console of its own (no window, no server): for scripts and tests."""
        layout.ensure_data()
        return cls(gs_server.App(demo, require_signin=require_signin))

    # ---- transport ---------------------------------------------------------------------------------------------
    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        status, out = gs_server.dispatch(self.app, method, path, body, self.session)
        if status >= 400:
            msg = out.get("error", f"HTTP {status}") if isinstance(out, dict) else str(out)
            raise ApiError(status, msg, out if isinstance(out, dict) else None)
        return out

    def _get(self, path: str):
        return self._call("GET", path)

    def _post(self, path: str, body: dict | None = None):
        return self._call("POST", path, body or {})

    # ---- system ------------------------------------------------------------------------------------------------
    def state(self) -> dict:
        return self._get("/api/state")

    def version(self) -> dict:
        return self._get("/api/version")

    # ---- sessions ----------------------------------------------------------------------------------------------
    def first_operator(self, name: str, pin: str) -> dict:
        """Only on an empty installation: creates the first person (admin) and signs them in."""
        self.session = self._post("/api/session/first_operator", dict(name=name, pin=pin))["token"]
        return dict(ok=True, role="admin")

    def sign_in(self, name: str, pin: str) -> dict:
        self.session = self._post("/api/session/signin", dict(name=name, pin=pin))["token"]
        info = self.session_info()
        return dict(session=self.session, operator=info["operator"], role=info["role"])

    def sign_out(self) -> dict:
        out = self._post("/api/session/signout")
        self.session = None
        return out

    def session_info(self) -> dict:
        return self._get("/api/session")

    def add_operator(self, name: str, pin: str, role: str = "operator") -> dict:
        return self._post("/api/session/add_operator", dict(name=name, pin=pin, role=role))

    def set_crew(self, pilots: dict) -> dict:
        """{sysid: pilot name}, one pilot per aircraft."""
        return self._post("/api/session/crew", dict(pilots={str(k): v for k, v in pilots.items()}))

    # ---- preflight ---------------------------------------------------------------------------------------------
    def preflight(self, sysid: int) -> dict:
        return self._post("/api/preflight", dict(sysid=int(sysid)))

    # ---- planner -----------------------------------------------------------------------------------------------
    def list_areas(self) -> list:
        return self._get("/api/planner/kml")

    def area(self, kml: str) -> dict:
        return self._post("/api/planner/area", dict(kml=kml))

    def draw_area(self, name: str, fence: list, ao: list, launch: list) -> dict:
        return self._post("/api/planner/draw", dict(name=name, fence=fence, ao=ao, launch=launch))

    def upload_kml(self, name: str, text: str) -> dict:
        return self._post("/api/planner/upload", dict(name=name, text=text))

    def plan_mission(self, kml: str, site: str, drones: int = 2, altitude_m: float = 30.0, overlap_pct: float = 25.0,
                     scan: str = "lawnmower") -> dict:
        out = self._post("/api/planner/run", dict(kml=kml, site=site, drones=drones, altitude=altitude_m,
                                                  overlap=overlap_pct, scan=scan))
        if not out.get("ok"):
            raise ApiError(400, out.get("error") or "the planner failed", out)
        return out

    def list_missions(self) -> list:
        return self._get("/api/planner/missions")

    def mission(self, folder: str) -> dict:
        return self._get("/api/planner/mission?folder=" + quote(folder))

    # ---- profiles ----------------------------------------------------------------------------------------------
    def _base(self, kind: str) -> str:
        return "/api/fleet" if _kind(kind) == "fleet" else "/api/run"

    def list_profiles(self, kind: str = "fleet") -> list:
        return self._get("/api/fleet/profiles" if _kind(kind) == "fleet" else "/api/profiles")

    def save_profile(self, kind: str, profile: dict) -> dict:
        return self._post("/api/fleet/profiles" if _kind(kind) == "fleet" else "/api/profiles", profile)

    def delete_profile(self, kind: str, pid: str) -> dict:
        return self._post(("/api/fleet/profiles" if _kind(kind) == "fleet" else "/api/profiles") + "/delete", dict(id=pid))

    def preview(self, kind: str, profile: dict) -> dict:
        return self._post("/api/fleet/preview" if _kind(kind) == "fleet" else "/api/profiles/preview", profile)

    def accept_risks(self, kind: str, profile: dict) -> dict:
        return self._post("/api/session/accept", dict(kind=_kind(kind), profile=profile))

    # ---- runs --------------------------------------------------------------------------------------------------
    def authorise(self, kind: str, profile: dict, confirm: str, delegate: bool = False) -> dict:
        """The page's tick + press-and-hold: `confirm` must be "AUTHORISE". Returns the run token (15 min)."""
        return self._post(self._base(kind) + "/authorise_quick", dict(profile=profile, confirm=confirm, delegate=delegate))

    def start(self, kind: str, profile: dict, token: str) -> dict:
        return self._post(self._base(kind) + "/start", dict(profile=profile, token=token))

    def _active_kind(self) -> str:
        if self.app.fleet and self.app.fleet.active:
            return "fleet"
        if self.app.run and self.app.run.active:
            return "single"
        return "fleet" if self.app.fleet else "single"

    def status(self, since: int = 0, kind: str | None = None) -> dict:
        """The active (or last) run: state, feed from item `since`, aircraft, result."""
        return self._get(self._base(kind or self._active_kind()) + f"?since={int(since)}")

    def wait_for(self, states, timeout_s: float = 300.0, kind: str | None = None, poll_s: float = 0.5) -> dict:
        """Poll until the run reaches one of `states`; returns the last status. The final states are "done" (passed)
        and "ended"; a fault mid-mission is "fault" until everyone is down."""
        states = (states,) if isinstance(states, str) else tuple(states)
        end = time.time() + timeout_s
        while True:
            s = self.status(kind=kind)
            if s.get("state") in states:
                return s
            if time.time() > end:
                raise ApiError(408, f"still {s.get('state')!r} after {timeout_s:g} s, waiting for {states}", s)
            time.sleep(poll_s)

    def arm(self, token: str, confirm: str) -> dict:
        """Single or group (bench) run: `confirm` must be "ARM"."""
        return self._post("/api/run/arm", dict(token=token, confirm=confirm))

    def arm_all(self, token: str, confirm: str) -> dict:
        """Fleet gate 1: `confirm` must be "ARM ALL"; every aircraft must be READY."""
        return self._post("/api/fleet/arm_all", dict(token=token, confirm=confirm))

    def start_all(self, token: str, confirm: str) -> dict:
        """Fleet gate 2: `confirm` must be "START ALL", inside the window after the first arm."""
        return self._post("/api/fleet/start_all", dict(token=token, confirm=confirm))

    def stop(self, word: str, target="all") -> dict:
        """HOLD / RTL / LAND (fleet, `target` a sysid or "all"); HOLD / LAND / ABORT (single run). Any signed-in role."""
        if self._active_kind() == "fleet":
            return self._post("/api/fleet/word", dict(word=word.upper(), target=str(target)))
        return self._post("/api/run/word", dict(word=word.upper()))

    def cancel(self) -> dict:
        """Fleet run: before START it disarms everyone; after START it ends the watch (aircraft keep their PX4 mode)."""
        return self._post("/api/fleet/cancel")

    def abort_flag(self, kind: str | None = None) -> dict:
        """The out-of-band ABORT flag on the companions (over ssh; works when the agent's link does not)."""
        return self._post(self._base(kind or self._active_kind()) + "/abort_flag")

    # ---- records and assistant ---------------------------------------------------------------------------------
    def list_runs(self) -> list:
        return self._get("/api/runs")

    def run(self, run_id: str) -> dict:
        return self._get("/api/runs/" + quote(run_id))

    def assistant_status(self) -> dict:
        return self._get("/api/assistant/status")

    def ask(self, question: str, context: dict | None = None, token: str | None = None) -> dict:
        return self._post("/api/assistant/ask", dict(question=question, context=context, token=token))

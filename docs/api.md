# Console API

Three ways in, one set of rules: the **desktop app** (its page calls Python through a bridge), the **local Python API**
(scripts, the assistant) and the **web server** (`artex-console --serve`). All three go through one router
(`gs_server.dispatch`), so roles, sessions, tokens, confirmation words and refusals are identical.

## Local Python API

```python
from artex.console.local_api import Console, ApiError
c = Console.open(demo=True)                     # its own console; inside the desktop app: Console(app)
c.sign_in("Ops Lead", "482916")
c.set_crew({113: "Pilot A", 115: "Pilot B"})
for s in (113, 115):
    assert c.preflight(s)["go"]
c.accept_risks("fleet", profile)
tok = c.authorise("fleet", profile, confirm="AUTHORISE")["token"]
c.start("fleet", profile, tok); c.wait_for("ready")
c.arm_all(tok, confirm="ARM ALL"); c.wait_for("armed")
c.start_all(tok, confirm="START ALL")
c.wait_for(("done", "ended"))
```

Methods: `state`, `version`, `first_operator`, `sign_in`, `sign_out`, `session_info`, `add_operator`, `set_crew`,
`preflight`, `list_areas`, `area`, `draw_area`, `upload_kml`, `plan_mission`, `list_missions`, `mission`,
`list_profiles`, `save_profile`, `delete_profile`, `preview`, `accept_risks`, `authorise`, `start`, `status`,
`wait_for`, `arm` (single run, `confirm="ARM"`), `arm_all`, `start_all`, `stop` (HOLD / RTL / LAND, any role, no token),
`cancel`, `abort_flag`, `list_runs`, `run`, `assistant_status`, `ask`. Each maps to one route below. Refusals raise
`ApiError(status, message)` with the same status codes. `artex.console.local_api_mock.MockConsole` has the same interface
with simulated aircraft and nothing behind it (`python -m artex.console.local_api_mock`).

## Desktop bridge

The page, opened from disk in the desktop window, calls `window.pywebview.api.call(method, path, body, session)` and gets
`{status, body}`: the same route paths and answers as HTTP, with no network involved. Only `/api/...` paths are accepted.

## Web server

With `artex-console --serve`, the console serves its web page and this JSON API on **127.0.0.1:8787** only. Every request must carry `Host: 127.0.0.1:8787`
(or `localhost:8787`); every POST must also carry `X-GS-Client: ui` and, when sign-in is required (the default), the
session token in `X-GS-Session`. Roles: **observer** < **operator** < **admin** (each includes the ones before it).

## Reading (GET, no role needed on this machine)

| Path | Returns |
|---|---|
| `/api/state` | demo or live, aircraft, stages, defaults, preflight results, whether a run is active, map tile settings |
| `/api/version` | app version and companion fingerprint (what the version handshake compares) |
| `/api/session` | signed in?, operator, role, crew, whether operators exist |
| `/api/profiles`, `/api/fleet/profiles` | saved single / fleet profiles |
| `/api/fleet/plans` | every `drone-N.plan` under `missions/` |
| `/api/planner/kml`, `/api/planner/missions` | saved areas (`ops/`), planned missions |
| `/api/planner/mission?folder=missions/<f>` | one mission for the map: fence, area, home, each drone's path, on-board status |
| `/api/run?since=N`, `/api/fleet?since=N` | the active (or last) run: state, aircraft, feed from item N |
| `/api/runs`, `/api/runs/<id>` | run records; one record with a paste-ready history block |
| `/api/assistant/status` | model, reachable?, whether delegation is available |

## Acting (POST)

| Path | Role | Body | Does |
|---|---|---|---|
| `/api/session/first_operator` | none | name, pin | creates the first operator (admin) on an empty installation |
| `/api/session/signin` | none | name, pin | returns a session token (5 wrong PINs lock the name for a minute) |
| `/api/session/signout` | observer | | ends the session |
| `/api/session/add_operator` | admin | name, pin, role | adds a person |
| `/api/session/crew` | operator | pilots {sysid: name} | one named pilot per aircraft, no person twice |
| `/api/session/accept` | operator | kind, profile | accepts that profile's risks for this session |
| `/api/profiles`, `/api/fleet/profiles` | operator | profile | saves (validated) |
| `/api/profiles/delete`, `/api/fleet/profiles/delete` | operator | id | archives (never deletes) |
| `/api/profiles/preview`, `/api/fleet/preview` | observer | profile | errors, risks, confirmations, agent commands, session readiness |
| `/api/preflight` | operator | sysid | runs the read-only preflight for one aircraft |
| `/api/run/authorise_quick`, `/api/fleet/authorise_quick` | operator | profile, confirm="AUTHORISE", delegate | the run token (needs signed-in session, crew, accepted risks) |
| `/api/run/authorise`, `/api/fleet/authorise` | operator | full form | the same token from the long form (API clients) |
| `/api/run/start`, `/api/fleet/start` | operator | profile, token | version handshake, then starts the agents |
| `/api/run/arm` | operator | token, confirm="ARM" | ARM (or ARM ALL for a group) at READY |
| `/api/fleet/arm_all`, `/api/fleet/start_all` | operator | token, confirm | the two fleet gates |
| `/api/run/word`, `/api/fleet/word` | observer | word (ABORT/LAND/HOLD, or HOLD/RTL/LAND + target) | stop commands |
| `/api/fleet/cancel` | observer | | before START disarms everyone; after START stops watching |
| `/api/run/abort_flag`, `/api/fleet/abort_flag` | observer | | the out-of-band ABORT flag on the companions, over ssh |
| `/api/planner/upload` | operator | name, text | stores a KML under `ops/` after the planner reads it |
| `/api/planner/draw` | operator | name, fence, ao, launch | stores a drawn area as KML |
| `/api/planner/area` | operator | kml | an area as polygons, for the editor |
| `/api/planner/run` | operator | kml, site, drones, altitude, overlap, scan | runs the planner into a new mission folder |
| `/api/assistant/ask` | observer | question, context, token | the assistant; act tools only with a delegated token, stop always |

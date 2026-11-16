# Plan: the console as a local desktop app with a local API

Status: **plan, nothing built yet** (16 Nov 2026). The mock of the API exists and runs: `artex/console/local_api_mock.py`.

## Why

Today the console is a web server on `127.0.0.1:8787` and the operator uses it in a browser tab. It works, but:

- an open port: anything on the laptop can reach the API; Host / Origin / header checks are what keep a malicious web page
  out, and they are easy to get wrong;
- it does not feel like a product: start a server, then open a browser at an address;
- scripts and the assistant have no supported way to drive the console other than imitating the browser.

Goal: a **desktop app** (a native window, no server, no port) with the **same page and the same rules**, plus a **local
Python API** for scripts and the assistant. The web server stays available as an option.

## What stays the same

The Jetson agents, the safety guard, every check and refusal, the run records, the page itself. The change is how the
page reaches the backend, and who else can.

## Shape

```
 desktop window (pywebview, WebKitGTK)            scripts / assistant              artex-console --serve (optional)
   index.html  --api(method,path,body)-->           local_api.Console                 HTTP on 127.0.0.1 (as today)
        |  window.pywebview.api.call(...)                 |                                   |
        +-------------------------+-----------------------+-----------------------------------+
                                  v
                    router.dispatch(app, method, path, query, body, session)   <- ONE place for routing, roles, sessions
                                  v
                    App (gs_server.py): runs, fleet runs, preflight, planner, assistant      <- unchanged
```

Every caller goes through `router.dispatch`, so the window, the scripts and the web server cannot drift apart: same role
check, same token, same confirmation words, same result.

## The local API (what the mock implements)

Python: `from artex.console.local_api import Console` (mock today: `from artex.console.local_api_mock import MockConsole`).
Refusals raise `ApiError(status, message)`, with the HTTP API's codes: 400 bad request, 401 not signed in, 403 not allowed
or not confirmed, 409 wrong state. The window bridge returns the same `{status, body}`.

| Method | Same as route | Role | Returns |
|---|---|---|---|
| `state()` / `version()` | GET `/api/state`, `/api/version` | none | aircraft, run active? / version, fingerprint |
| `first_operator(name, pin)` | POST `/api/session/first_operator` | none (empty install) | `{ok, role:"admin"}` |
| `sign_in(name, pin)` / `sign_out()` | `/api/session/signin`, `signout` | none / observer | `{session, operator, role}` |
| `add_operator(name, pin, role)` | `/api/session/add_operator` | admin | `{ok}` |
| `set_crew({sysid: pilot})` | `/api/session/crew` | operator | `{pilots}` |
| `preflight(sysid)` | POST `/api/preflight` | operator | `{go, blockers, warnings, battery_pct, gps}` |
| `list_areas()` / `draw_area(name, fence, ao, launch)` | `/api/planner/kml`, `/api/planner/draw` | -, operator | areas / `{path, gcs}` |
| `plan_mission(kml, site, drones, altitude_m, overlap_pct, scan)` | `/api/planner/run` | operator | `{folder, plans, warnings}` |
| `list_missions()` | `/api/planner/missions` | - | folders |
| `save_profile(kind, profile)` / `list_profiles(kind)` | `/api/profiles`, `/api/fleet/profiles` | operator | profile with id |
| `preview(kind, profile)` | `/api/.../preview` | observer | `{errors, risks, missing_accept, missing_pilots}` |
| `accept_risks(kind, profile)` | `/api/session/accept` | operator | `{accepted}` |
| `authorise(kind, profile, confirm="AUTHORISE", delegate=False)` | `/api/.../authorise_quick` | operator | `{token, ttl_s}` (15 min, bound to the profile) |
| `start(kind, profile, token)` | `/api/run/start`, `/api/fleet/start` | operator | `{id}` |
| `status(since=0)` | GET `/api/run`, `/api/fleet` | - | `{state, feed[since:], aircraft, window_s, result}` |
| `arm_all(token, confirm="ARM ALL")` | `/api/fleet/arm_all` (single: `arm`, `"ARM"`) | operator | `{ok}` |
| `start_all(token, confirm="START ALL")` | `/api/fleet/start_all` | operator | `{ok}` |
| `stop(word, target="all")` HOLD / RTL / LAND | `/api/.../word` | **observer** | `{sent}`; never needs a token |
| `cancel()` | `/api/fleet/cancel` | observer | before START disarms everyone |
| `list_runs()` / `run(id)` | `/api/runs`, `/api/runs/<id>` | - | records |
| `ask(question, context, token=None)` | `/api/assistant/ask` | observer | answer, lookups, actions |

The confirmation words are arguments on purpose: a script must say `confirm="ARM ALL"` just as the page makes a person
hold the button. A script runs as a signed-in person (`sign_in`), and its actions are recorded under that name.

Example (runs against the mock today; against the real API after step 3):

```python
from artex.console.local_api_mock import MockConsole as Console, ApiError

c = Console()
c.first_operator("Ops Lead", "482916"); c.sign_in("Ops Lead", "482916")
c.set_crew({113: "Pilot A", 115: "Pilot B"})
assert all(c.preflight(s)["go"] for s in (113, 115))
m = c.plan_mission("ops/sample-camp.kml", "Sample survey", drones=2, altitude_m=30)
prof = dict(name="Sample survey", mode="mission", flight_type="props_on",
            aircraft=[dict(sysid=113, plan=m["plans"][0]["path"]), dict(sysid=115, plan=m["plans"][1]["path"])])
c.accept_risks("fleet", prof)
tok = c.authorise("fleet", prof, confirm="AUTHORISE")["token"]
c.start("fleet", prof, tok)
while c.status()["state"] != "ready": ...
c.arm_all(tok, confirm="ARM ALL"); c.start_all(tok, confirm="START ALL")
```

`python -m artex.console.local_api_mock` runs a full scripted fleet mission; `tests/test_local_api_mock.py` pins the
refusals (no sign-in, missing pilot or risks, wrong word, gate order, a late START disarming everyone, an observer who may
stop but not arm, a fault holding the others).

**Window bridge** (for the page): `window.pywebview.api.call(method, path, body, session)` -> `{status, body}`. The page's
one `api()` function picks the bridge when it exists and `fetch` otherwise; nothing else in the page changes.

## Steps

**0. Spike, half a day: does the page run in a native window?** Open `index.html` from disk in pywebview (GTK backend,
WebKitGTK 4.0 is installed). Check: the map (Leaflet), the 3D view (three.js loaded as an ES module from `file://`),
`localStorage`, press-and-hold buttons, dark mode. If WebKit refuses ES modules from `file://`, fall back to pywebview's
built-in static server, which serves the page files only (no API) on a random loopback port.
*Decision point: go / no-go on pywebview.*

**1. One router.** Move the route handling out of `make_handler` (`gs_server.py`, `do_GET` / `do_POST` / `_post` /
`_session` / `_fleet`) into `artex/console/router.py`: `dispatch(app, method, path, query, body, session) -> (status,
body)`, including `route_role()` and the session lookup. The HTTP handler keeps only what is HTTP: Host / Origin /
header checks, reading the body, writing the reply. No behaviour change.
*Tests:* the existing suite unchanged and green; a parity test that sends every route through HTTP and through `dispatch`
and compares the answers.

**2. The local Python API.** `artex/console/local_api.py`: `Console` and `ApiError`, exactly the methods in the table,
each a thin call to `dispatch`. `Console(app)` in-process, or `Console.open(data_dir, demo=False)` to build its own
`App`. The mock's tests are re-pointed at the real `Console` in demo mode and must pass unchanged.

**3. The desktop shell.** `artex/console/desktop.py`: creates the `App`, a `Bridge` with `call()`, one pywebview window
(title, icon, size, remembered position).
- Page: `api()` uses the bridge when `window.pywebview` exists; the session token stays in `sessionStorage`.
- **Closing the window during a run.** In a server, closing the browser tab leaves the run alone; here, closing the
  window would end the process and the agents would lose the console (each aircraft returns to launch after 60 s without
  it). So: while a run is active, closing asks "A run is active; stop it first or keep the window open"; the window
  never closes silently during a run.
- Polling stays as today (it goes through the bridge). Pushing feed updates instead of polling is a later option.

**4. Launch modes.** `artex-console` opens the desktop app; `artex-console --serve` runs the web server as today (tests,
headless, a second screen); `--demo` and `--no-signin` work in both. The menu entry starts the desktop app.

**5. Packaging.** Laptop installers gain `pywebview` (pure Python wheels: pywebview, proxy_tools, bottle) and the
system packages `python3-gi` + WebKitGTK GIR (`gir1.2-webkit2-4.0` on 22.04, `-4.1` on 24.04). The `.deb` declares them;
the offline bundle documents them (they need apt once). The venv is created with access to the system `gi` module.
The Jetson bundle is unaffected.

**6. Docs and tests.** `docs/api.md` gains the Python API; install guide and operator manual updated; CI runs the router
parity tests and the API tests in demo mode (the window itself is checked by hand, step 7).

**7. Verification.**
- Suites green (today 490 + the mock's 7), parity test green.
- Desktop app in demo mode: sign in, plan from a drawn area, fleet run through both gates, stop, records, 3D view.
- The same against the SITL simulation (`docs/simulation.md`), with the independent track recorder.
- `ss -ltnp` shows **no listening port** for the desktop app; `--serve` still listens on 127.0.0.1 only.
- Close the window mid-run: refused with the message; agents unaffected.

## Risks

| Risk | Mitigation |
|---|---|
| WebKitGTK blocks ES modules from `file://` (3D view) | step 0 tests it; fallback: pywebview's static-only loopback server |
| WebKitGTK 4.0 vs 4.1 differs between Ubuntu releases | pywebview handles both; installer picks the right package |
| NVIDIA driver and WebKit compositing (blank window) | `WEBKIT_DISABLE_COMPOSITING_MODE=1` switch in the launcher |
| Window closed during a flight | step 3: refused while a run is active |
| A script skipping a safety step | impossible by construction: scripts go through `dispatch`, the same checks as a click |

## Size

About a day: half on the router move and its parity test, a quarter on the API and the window, the rest on packaging,
docs and the walk-throughs. Step 0 decides whether pywebview is the right shell before anything else is built.

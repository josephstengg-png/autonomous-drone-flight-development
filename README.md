# ARTEX SAR — ground station, mission planner and aircraft agent

Plan a search-and-rescue survey for several drones, and run it from one console, with the pilots' handsets as the
override at all times. Built from the working ARTEX lab system (`~/drone-dev/FlightPlanner`, frozen 12 Nov 2026).

```
Laptop (console, planner, assistant)  --ssh-->  Jetson per aircraft (agent, relay)  --ELRS radio / USB-->  PX4
```

## Quick start (demo, nothing contacted)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,plot]"
.venv/bin/python -m artex.console.gs_server --demo        # open http://127.0.0.1:8787
.venv/bin/python -m pytest -q                              # 423 tests
```

## Layout

| Package | What it is |
|---|---|
| `artex/planner` | KML or drawn area → per-drone `.plan` files; camera model; separation guardrail; mission map data |
| `artex/console` | web console (`static/index.html`) + server: profiles, single / group / fleet runs, gates, records |
| `artex/agent` | runs on each Jetson: the aircraft agent, the safety guard, the offboard controller, the radio relay |
| `artex/fleetops` | provision companions, preflight, parameters, flight logs, out-of-band abort |
| `artex/assistant` | Qwen3-4B assistant (Phase B/C) |
| `artex/common` | layout (where things live, how the agent is started) and the version handshake |
| `artex/diagnostics` | engineering tools: experiments, flight-log analysis, simulation harnesses (not in the default install) |
| `demo/` | simulated agents for demo mode |
| `packaging/` | release bundles (`build_bundles.sh`) |

Site configuration (per customer, not code): `companions.yaml`, `artex-registry.yaml`, `fleet-*.yaml`,
`sar_turn_params.params`. Data: `ops/` (sector KMLs), `missions/` (plans), `profiles/`, `runs/` (records).

## Safety model in one paragraph
The handset is the authority: each aircraft has a named pilot, ch5 OFF takes over, ch8 kill held 5 s stops it. The
console refuses what the rules forbid (invalid profile, stale preflight on a props-on radio run, a companion running
different agent code) and records who authorised what. Arming and starting are human-gated; the assistant (Phase C)
may only act inside one authorised run, and may always stop. See `docs/architecture.md`.

Status: see `docs/architecture.md` → "Before release".

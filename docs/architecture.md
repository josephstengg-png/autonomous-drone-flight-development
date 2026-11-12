# Architecture

## Data flow
1. **Area:** a KML (GCSlocation, GeoFence, OpsArea) uploaded or drawn → `ops/<name>.kml`.
2. **Plan:** `artex.planner.cli` (camera model → lane spacing; partition per drone; altitude layers; guardrail) →
   `missions/<site>-<date>/mission_drones/drone-N.plan`.
3. **Profile:** single-aircraft offboard tests (`profiles/`) or fleet waypoint runs (`profiles/fleet/`).
4. **Run:** the console starts one agent per aircraft on its Jetson over ssh (`python3 -m artex.agent.drone_agent`,
   from `~/artex_app`), after the version handshake. Single and group runs: offboard ladder, READY → ARM (ALL).
   Fleet runs: upload + verify + preflight contract → READY barrier → ARM ALL → START ALL (within 8 s) → watch;
   a fault on one aircraft puts the others in HOLD.
5. **Record:** every run → `runs/<id>.json` (who authorised, what was accepted, every event, the result).

## Authority, in order
1. The pilot's handset (PX4 acts on it with none of this software in the path).
2. The agent's guard on the companion (WARN → HOLD → LAND; stands down on a takeover).
3. The console's rules (validation, BLOCKs, version handshake, two gates).
4. The operator's authorisation.
5. The assistant (Phase C): only inside one authorised, delegated run; stopping always allowed.

## Before release
- Real-flight evidence: radio Offboard is props-off bench only; fleet missions never flown; outdoor parameter configuration.
- Lean outdoor downlink profile for missions.
- Simplified authorisation, drawn areas, 3D view (Phase A2).
- Assistant with evals (Phases B, C).
- Installers (.deb, offline Jetson bundle), accounts and roles, signing; safety case; licence.

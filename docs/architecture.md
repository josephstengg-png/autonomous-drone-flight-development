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
- Assistant with evals (Phases B, C).
- Installers (.deb, offline Jetson bundle), accounts and roles, signing; safety case; licence.

## Phase A2 (done, 12 Nov 2026)
- **Quick authorisation:** sign in once (operator + PIN, salted PBKDF2 in `operators.json`, 5 wrong PINs lock a name
  for a minute); name one pilot per aircraft; accept each profile's risks once per session (keyed by the profile hash,
  so an edited profile asks again). Per run: one confirmation tick + press-and-hold -> the same run token as before
  (`/api/run/authorise_quick`, `/api/fleet/authorise_quick`). BLOCKs and the fresh per-run confirmation remain.
  The full form endpoints still exist for API clients. Delegation to the assistant is shown but disabled (Phase C).
- **Draw the area:** Leaflet (bundled, `static/vendor/`) on OpenStreetMap tiles by default (`ARTEX_TILES` to point at
  the customer's tile server or an offline cache; OSM's public tiles are not for commercial heavy use). GeoFence,
  area of operation, launch point; drag corners; checked live (AO and launch inside the fence, no crossing edges) and
  again on the server; saved as a KML under `ops/` that plans identically to the same area imported.
  No-go zones: draw the AO around them (a notch); separate no-go polygons are not supported by the planner yet.
- **3D view:** three.js (bundled) on the same data as the 2D map: fence as a box (height = mission top + 5 m, the
  aircraft's GF_MAX_VER_DIST decides), AO, each drone's path at its altitude layer, launch point; heights exaggerated
  x3 by default (x1 / x10 selectable, labelled).

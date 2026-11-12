# Full simulation (PX4 SITL + Gazebo)

The console, the planner and the aircraft agent run unchanged against PX4 software-in-the-loop: the same preflight,
upload, READY barrier, ARM ALL / START ALL, offboard setpoint stream and records as on real aircraft. Only the radio
chain (backpack, relay, Wi-Fi) is absent, and preflight skips exactly that — after proving each vehicle is a simulator.

First run end to end on 12 Nov 2026 (two x500s over Pulau Ubin); results at the end.

## Pieces

| What | Where |
|---|---|
| PX4 v1.16.0 SITL build + Gazebo Harmonic | Docker image `px4-gz-harmonic:jammy`, PX4 tree at `~/drone-dev/PX4-Autopilot` (mounted) |
| Multi-vehicle launcher | `artex/diagnostics/sitl_swarm.sh` (instance *i* = sysid *i+1*, agent port 14540+*i*, GCS 14550) |
| Simulated site | a data folder (e.g. `~/artex_sitl`) with `site-examples/sitl/*.yaml`: aircraft `host: local`, `sim: true` |

## Run it

```bash
# 1. two simulated aircraft over Pulau Ubin (world origin = home; draw your area around it)
docker run -d --name artex-sitl --network host -u $(id -u):$(id -g) -e HOME=/tmp \
  -e GZ_PARTITION=artex -e DRONE_HOME=$HOME/drone-dev -e HEADLESS=1 \
  -v $HOME/drone-dev/PX4-Autopilot:$HOME/drone-dev/PX4-Autopilot \
  -v $HOME/artex_app_test/artex/diagnostics:/sitl:ro \
  px4-gz-harmonic:jammy bash -c 'bash /sitl/sitl_swarm.sh start ubin 2 && sleep infinity'

# 2. a data folder for the simulated site
mkdir -p ~/artex_sitl && cp artex/starter/site-examples/sitl/*.yaml ~/artex_sitl/

# 3. the console, against that folder (it says "SIMULATED aircraft" when every aircraft is sim)
ARTEX_HOME=~/artex_sitl .venv/bin/python -m artex.console.gs_server     # http://127.0.0.1:8787

# stop: Ctrl-C the console; docker rm -f artex-sitl
```

Then use the console as usual: sign in, name pilots for aircraft 1 and 2, Preflight, draw the area round the Ubin
origin (1.405367, 103.950961), plan, save a fleet profile in mission mode, authorise, Start, ARM ALL, START ALL.
QGroundControl can watch on UDP 14550 at the same time.

**Traps**
- `GZ_PARTITION` must be set. Gazebo names its partition after the user, and a host user id has no account inside the
  image: without it PX4 waits for a world it can never see ("Waiting for Gazebo world...").
- The world's `<spherical_coordinates>` is home; `PX4_HOME_LAT/LON` are ignored. A mission drawn elsewhere is rejected.
- PX4 keeps parameters per instance in `build/px4_sitl_default/rootfs/<i>/` across restarts.
- SITL defaults are not the survey configuration: load `sar_turn_params.params` (tilt 25°, etc.) and give each
  aircraft an `RTL_RETURN_ALT` at or above its own layer, or its agent refuses READY (by design).

## What the first run found (12 Nov 2026)

Each was a real defect the bench could not have shown; each is fixed and has a test.

| Found | Effect | Fix |
|---|---|---|
| ARM/START handlers read the heartbeats themselves | guard saw a 6.6 s "telemetry gap" and put both aircraft in HOLD on the ground at START | every received message reaches the guard (`observe_every_message`) |
| resident (mission) agent kept the indoor 2.0 m guard ceiling | both aircraft told to LAND at 2 m on the climb to 30 m | ceiling = mission top + 5 m (`mission_ceiling`) |
| innovation rule timed "failing" up to *now* | with estimator status every 5 s (mission downlink) one spike landed an aircraft from 30 m | measured between failing samples only |
| watcher treated any RTL/LAND then disarm as success | the 2 m guard landing was reported "every aircraft completed its mission" | completion needs the plan's last item; guard actions are faults |
| relayed operator HOLD reported as a guard fault | the aircraft the console held after a fault was itself marked faulted | operator-originated guard events are not faults |
| a clean full offboard flight recorded `passed: false` | records disagreed with the demo agent | `flight_passed` |
| drone-2 (32 m layer) had `RTL_RETURN_ALT` 30 | refused READY, nobody armed | correct behaviour; set per aircraft |

**Successful run** (`runs/20261112-215800` in the sim data folder): both READY, armed 0.01 s apart, START ALL inside
the 8 s window, survey cross-track error mean 0.6 / 0.7 m (max 5.3 / 5.7 m at corners), altitude held 29.94–30.10 m
and 31.93–32.10 m, both RTL at their own layer and disarmed on landing: "every aircraft completed its mission".
Offboard hover (`--stage disarm`, indoor downlink): 684 setpoints at 20 Hz, 0 link losses, peak 1.01 m, PX4 disarmed
on landing, passed.

**Still open, seen in the run:** both aircraft climb side by side from one launch area — 5 m apart and within 0.1 m of
each other's height for ~15 s until they reach their layers. The planner warns about it; launch scheduling (stagger
START, or separate pads) is not built.

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

## Over the radio: the ELRS link emulator (12-13 Nov 2026)

`artex/diagnostics/radio_link_emulator.py` sits where the ELRS relay sits on a companion: PX4 SITL's API port (14540+*i*)
on one side, the agent's `udpout://127.0.0.1:24540+i` on the other. Everything crosses it: setpoints, commands, ACKs,
mission upload, telemetry. Its numbers come from the flight logs (sources in the module docstring): 1240 B/s downlink;
delay and stalls that grow with downlink load and with a streaming uplink (Exp 4, ulogs 101-132); 1 % loss each way;
PX4 normal-mode streams instead of SITL's ~340 msgs/s; `SET_MESSAGE_INTERVAL` obeyed as the real port does, raised rates
honoured at 0.3x (4 Nov measurement). One number is calibrated, not measured: the uplink budget (700 B/s), chosen so
20 Hz chokes the uplink as Exp 1 showed and 3 Hz does not.

```bash
python -m artex.diagnostics.radio_link_emulator --px4-port 14540 --agent-port 24540 --seed 1 --log link-1.jsonl &
python -m artex.diagnostics.radio_link_emulator --px4-port 14541 --agent-port 24541 --seed 2 --log link-2.jsonl &
# site-examples/sitl/aircraft.yaml: radio = through the emulator, usb = straight to SITL, emulated_radio: true
```

`emulated_radio: true` makes the console pass `--emulated-radio`, so the agent uses its radio behaviour inside SITL
(3 Hz setpoints, 8 s ARM retry, the indoor downlink profile, the height-feed check) instead of the simulator's wired one.
Stop the emulators before running the test suite (one test binds 14540).

**Validation against Experiment 4** (aircraft disarmed, round trip of a harmless command, `validate_link`):

| Condition | Emulated median / p90 | ARTEX113 median / p90 |
|---|---|---|
| default streams, 0 Hz | 0.73 / 0.89 s | 0.82 / 0.87 s |
| trimmed, 0 Hz | 0.27 / 0.28 s | 0.26 / 0.28 s |
| default streams, 3 Hz | 0.93 / 2.17 s (1 of 12 lost) | 0.98 / 1.43 s (0 of 18) |
| trimmed, 3 Hz | 0.28 / 0.29 s | 0.24 / 0.31 s |

**Fleet waypoint mission over the emulated radio** (mission downlink): completed on the fourth attempt after fixes,
arm spread 0.11 s, survey cross-track mean 0.61 / 0.67 m, altitude within 0.1 m, "every aircraft completed its mission".

**Offboard hover at 3 Hz over the emulated radio** (6 runs, alternating downlink as Exp 5): **none held a hover.**

| Run | Downlink | Longest setpoint gap at PX4 | What happened (PX4 log) |
|---|---|---|---|
| 1 | indoors | 0.41 s | link held; height loop overshot to 1.81 m -> controller ceiling abort -> PX4 LAND |
| 2 | outdoors | 2.37 s | Offboard signal lost +2.8 s -> Position mode; PX4 landed it |
| 3 | indoors | 1.65 s | Offboard lost +2.3 s (back +3.0 s), then overshoot -> ceiling abort |
| 4 | outdoors | 0.62 s | **runaway climb to 11.6 m**: thrust ramped to 0.80 while late heights showed no lift; guard LAND at "3.08 m" when it was ~5 m |
| 5 | indoors | 0.62 s | Offboard lost +3.6 s -> Position; **unexplained**: no setpoint gap over 1 s in the emulator log |
| 6 | outdoors | 2.03 s | Offboard lost +1.3 s -> Position |

Outdoor gaps (2.0-2.4 s) match Exp 5 (1.35-2.9 s). The indoor link is somewhat harsher than Exp 5 measured (one 1.65 s
gap against 0.51-1.13 s); Exp 5 was props-off on the bench and never sent the flight stages' height-feed request.
Even where the link held (run 1), the thrust-only height loop could not hold height across the delay, as the 29 Oct
analysis predicted.

**Defects found and fixed (each with a test):**
- prepare's on-board mission count had one try: a lost reply refused READY (now 4 tries, like the read-back).
- **an aircraft left in AUTO.MISSION took off at ARM ALL** (PX4 starts the mission when armed in Mission mode) and one
  left in OFFBOARD could not arm. Prepare now puts the aircraft in Hold and READY needs it (`mode_hold`); `do_arm`
  refuses outside Hold.
- **the console closed the run as "disarmed" and released the agent of that airborne aircraft** (PX4's link-loss
  failsafe brought it home). An aircraft still armed after DISARM ALL is now a fault, held and supervised until down.
- READY was reported before the downlink profile was applied (~10 s): over the radio the last aircraft READY armed 7 s
  after the first. READY now comes when ARM will work (spread 0.08-0.11 s).
- the START window ran from the LAST arm; PX4's auto-disarm runs from each aircraft's own. A 7 s spread let PX4 disarm
  one aircraft 0.5 s after MISSION_START. The window now runs from the first arm.
- a held aircraft's quick RTL could land between two status polls and was called "disarmed mid-mission".
- `offboard-flight` skipped the indoor profile and the height-feed check in SITL even behind the emulator.

**Open, not changed (decisions for the operator):**
- The guard's innovation rule includes `mag_ratio`: magnetometer innovations reached 1.37-1.39 in survey corners in
  SITL and landed an aircraft from 30 m twice. PX4 itself only rejects those compass samples.
- Props-on flight stages over the radio are an ACCEPT-level risk in the console (`radio_loop`), not a block. Six of six
  simulated radio hovers failed, one climbing to 11.6 m.
- The console's preflight said GO while PX4 refused to arm ("Battery unhealthy"): it checks battery percentage, not
  PX4's own health verdict.

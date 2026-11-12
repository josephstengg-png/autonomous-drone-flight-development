# Safety manual

ARTEX helps a crew plan and run multi-aircraft surveys. It does **not** make the aircraft autonomous and it does not
replace the pilot in command. The operating organisation flies under its own aviation authority's rules and
permissions (in Singapore, CAAS); nothing in this software changes those obligations.

## Authority, in order

1. **The pilot's handset.** It talks to the flight controller directly, over a link that does not pass through the
   laptop, the companion computer, the network or any ARTEX code. Each aircraft has one named pilot holding its handset
   for the whole run. On the reference aircraft: flight-mode switch to the hand-flown position takes over; the kill
   switch held 5 s stops the motors. **Confirm the mapping on your own handsets before the first flight.**
2. **PX4's own failsafes** (geofence, battery, link loss, tumble detection), configured per aircraft.
3. **The companion's guard** (on each Jetson), which watches the aircraft and climbs a ladder it never descends:
   WARN → HOLD → LAND → DISARM (only on the ground). A tumble rule can cut the motors in the air only after a descent
   has been tried and failed, and only when enabled. A mode change on the handset makes the guard stand down for good.
4. **The console**, which refuses what the rules forbid and records who authorised what.
5. **The assistant**, which can act only inside one run a person authorised, and can always stop.

Everything below the handset helps only while the chain it depends on is intact — and an emergency is exactly when
that chain may be broken. Never treat a software stop as equivalent to the handset.

## What the console guarantees, and what it does not

It **refuses**: an invalid profile; a props-on radio run on a stale preflight; a run while any companion runs code
different from the laptop's; a run without a named pilot for every aircraft; a run whose risks nobody accepted; a token
older than 15 minutes or issued for a different version of the profile; START ALL more than 8 s after the last
aircraft armed (it disarms everyone instead); a mission whose paths leave the fence or let two drones meet.

It **does not**: see the airspace, people, weather or obstacles; know about buildings (the maps and 3D view show none
unless supplied); keep aircraft apart in the return-to-launch phase (see limits); guarantee the radio link.

## The assistant

- The language model never talks to an aircraft. It calls the same console functions a person's click calls, and those
  functions apply the same checks.
- Without a delegation it can only read and draft. With one, it may press ARM ALL and START ALL for that run, those
  aircraft, that profile, for at most 15 minutes. It can never authorise, edit a profile, clear a block or change a
  parameter.
- Stop requests ("stop", "hold", "land", "come back") are recognised by fixed code and acted on without the model.
- Delegation on real aircraft is off unless `ARTEX_ASSISTANT_LIVE=enabled`. Enable it only after the evaluation
  (`python -m artex.assistant.evals --phase BC --repeat 3`) passes on the installed model at your site.

## Hazard analysis (FMEA)

Severity: **C** catastrophic (injury possible), **H** hazardous (aircraft loss), **M** major (mission lost).
"Status" says whether the mitigation has been demonstrated on a real aircraft.

| # | Failure | Effect | Sev | Mitigation | Status |
|---|---|---|---|---|---|
| 1 | Laptop crashes or is unplugged mid-run | no console commands | H | each companion returns to launch after 60 s without commands; optional watchdog sets HOLD after 15 s; pilots take over | failsafe tested in software and on the bench; watchdog not on aircraft yet |
| 2 | Radio link degraded by telemetry load | commands delayed or lost, Offboard drops | H | lean downlink profiles (indoors ≈ 11 %, mission ≈ 22 % of capacity); handset on its own link | measured on the bench (5 Nov 2026); mission profile not yet measured on the radio |
| 3 | Companion runs stale code | wrong behaviour in flight | H | version handshake refuses the run | demonstrated in tests; caused a real refusal-to-arm on 10 Nov 2026 before the handshake existed |
| 4 | Aircraft tumbles or climbs uncommanded | loss of control | C | guard: LAND after 0.5 s tilt ≥ 30° or uncommanded climb; CUT (if enabled) after a failed descent; PX4 tumble detection beneath | LAND then on-ground disarm demonstrated on the bench; in-air cut not exercised |
| 5 | Pilot needs to take over | — | C | handset mode switch; guard stands down; agent sends nothing further | kill switch demonstrated in flight (tethered, 29 Oct 2026); mode-switch takeover in flight not yet tested |
| 6 | Two aircraft meet | collision | C | planner separation guardrail; 2 m altitude layers | **gap**: all aircraft climb from one launch point, and return-to-launch can put one aircraft on another's layer |
| 7 | Mission leaves the permitted area | airspace infringement | C | planner hard-stop on fence and area; PX4 geofence; upload rejected if home and fence disagree | demonstrated in simulation |
| 8 | Wrong profile flown (edited after authorising) | unplanned behaviour | H | token bound to the profile hash; an edit voids it | tests |
| 9 | Assistant presses a gate unasked, or invents a procedure | unplanned arming; operator misled | C | no act tools without a delegation; server checks every gate; stop routed by fixed code; evaluation gate before live use | 27/27 on the evaluation in simulation (12 Nov 2026); not used on real aircraft |
| 10 | Arming not followed by START ALL | armed aircraft idling on the ground | M | 8 s window, then the console disarms everyone | tests and simulation |
| 11 | One aircraft faults in a fleet run | partial mission, crowding | H | others HOLD; pilots decide | simulation |
| 12 | Unauthorised person operates the console | unplanned run | C | console listens on the laptop only; sign-in, roles, PIN lockout | tests |
| 13 | Battery exhausted on a long survey | forced landing | H | PX4 battery failsafe; mission-time estimates from cruise speed are about 50 % optimistic, so plan with margin | known limitation |
| 14 | Satellite imagery out of date | operator misjudges the area | M | street layer as a cross-check; walk the site | — |

## Known limits (read before any outdoor flight)

- Real-aircraft evidence so far is indoor and bench work: radio Offboard props-off, one tethered in-flight kill. Fleet
  missions have flown only in simulation. Outdoor parameter sets have not been applied to the reference aircraft.
- Return-to-launch flies at the aircraft's default cruise speed and its own return altitude, not the mission's layer.
- Turn speed is limited by geometry: aircraft slow to a few m/s at every corner.
- The guard's thresholds are engineering judgement, not measured from a healthy aircraft's error rate outdoors.
- The fallback watchdog and the assistant's delegation have not run on real aircraft.

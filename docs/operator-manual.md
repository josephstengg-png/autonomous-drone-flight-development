# Operator manual

For the people who plan and run surveys from the ARTEX console. Read the [safety manual](safety-manual.md) first:
it says what the console can and cannot do for you, and why the pilots' handsets always outrank it.

## Who does what

| Role | Can |
|---|---|
| **Observer** | see everything, ask the assistant, and **stop** aircraft (HOLD / RTL / LAND / ABORT) |
| **Operator** | everything an observer can, plus plan, save profiles, run preflight, authorise and fly runs |
| **Admin** | everything an operator can, plus add people |
| **Pilot** (one per aircraft, named on the Connect page) | holds that aircraft's handset for the whole run and takes over whenever they judge it necessary |

The first person to open a new installation creates the admin account. Admins add everyone else
(Connect page → "Add a person"). PINs are stored only as salted hashes; five wrong PINs lock that name for a minute.

## Start of the day (about a minute)

1. **Sign in** (Connect page): your name and PIN.
2. **Name the crew:** one pilot for each aircraft you will fly. Nobody can pilot two aircraft.
3. **Preflight:** press Preflight for each aircraft. It is read-only: link, flight controller, parameters, battery,
   the companion's agent version. A props-on radio run is refused when the preflight is older than the limit shown.
4. Accept each profile's risks when the console first asks. It remembers that for the session and asks again only if
   the profile changes.

## Planning a mission (Profiles page)

**The area.** Either upload a KML (it must contain a `GeoFence` polygon, an `OpsArea` polygon inside it and a
`GCSlocation` launch point) or draw it on the map:
1. *Fence*: click the corners of the area the aircraft must never leave. Click the first corner to close it.
2. *Area of operation*: the part to search, inside the fence. To keep the aircraft out of a building or a crowd,
   draw the area around it as a notch. Separate no-go polygons are not supported yet.
3. *Launch point*: where the aircraft take off and return to, inside the fence.

Drag any corner to adjust it. The editor shows a red message if the area of operation or the launch point leaves the
fence, or an edge crosses another. Save it under a name; it appears in the area list next to uploaded KMLs.

**The directive.** Choose drones, altitude, overlap and scan pattern, or type what you want into the Assistant panel
("survey the area with two drones at 30 m, 25 % overlap") and press *Apply to the form*. The assistant only fills the
form; nothing is planned until you press **Plan**.

**What altitude changes.** Everything derives from it: higher means wider lane spacing, fewer lanes and a faster
blur-safe speed, but coarser ground detail (cm per pixel). Each drone gets its own altitude layer, 2 m apart.
Real speed in turns is much lower than the cruise speed, so expect a mission to take longer than the planned figure.

**Check it before you fly.** The mission map shows the fence, the area, the launch point and each drone's path over
satellite imagery (switch to the street map if you are offline or the imagery is out of date). The **3D** button shows
the same paths at their altitude layers; heights are exaggerated three times by default and labelled.
The planner refuses a mission whose paths leave the fence, whose survey leaves the area of operation, or where two
drones could meet.

## Flying

### One aircraft, or a props-off group bench test (Run page)
1. Choose a profile. The panel shows what is already known and what is still missing.
2. Confirm what only you can see — props on or off, area clear, pilots at their handsets — with **one tick**.
3. **Press and hold** *Authorise*. You get a run token valid for 15 minutes, bound to that exact profile.
4. **Start**: the console checks every companion runs the same code as the laptop, then starts the agents.
5. When every aircraft reports READY, press **ARM** (group: ARM ALL). The aircraft follow the profile.
6. Watch the feed. To stop: **HOLD**, **LAND** or **ABORT** on the page, or the pilot takes over by handset.

### A fleet waypoint mission (Fleet page)
1. Choose the fleet profile (it names the mission folder, the aircraft and the downlink profile).
2. Tick, press and hold *Authorise*, then **Start**. Each companion uploads its aircraft's plan and reads it back;
   the console waits until every aircraft is READY.
3. **ARM ALL.** Then **START ALL** within 8 seconds of the last aircraft arming, or the console disarms everyone.
4. If one aircraft faults, the others **HOLD**; the pilots decide what happens next.
5. Each aircraft returns to launch on its own if the console goes silent for 60 s (the companion's failsafe). If the
   fallback watchdog is installed, the companions set HOLD after 15 s without the laptop.

**Upload only.** A fleet profile in upload mode puts the plans on the aircraft and does nothing else: there are no gates
and nothing arms. The plans then show as "on board" on the mission map.

### Delegating the gates to the assistant
Tick *Delegate to assistant* when you authorise and the assistant may press ARM ALL and START ALL **for that run
only**, when the console's own conditions are met. It cannot authorise anything, change a profile or clear a block.
On real aircraft this is off unless the installation enables it (see [install](install.md)). Everything it does is
written into the run record.

### Stopping
Any signed-in person, including an observer, can stop. Typing "stop", "hold", "land" or "come back" into the
Assistant panel stops the aircraft directly, without waiting for the model. If the laptop or the radio link is in
doubt, the pilots take over by handset: **that is always the fastest and most reliable stop.**

## After the run (Records page)
Every run is recorded: who authorised it, the confirmations, each aircraft's feed, any assistant action with its
reasoning. *Copy* gives a paste-ready summary for your log. Records are never deleted by the console.

## When something is refused

| Message | Meaning | What to do |
|---|---|---|
| version mismatch on a companion | that companion runs different code from the laptop | reinstall the companion bundle, or provision it from the laptop |
| preflight is stale | the last preflight is older than allowed for a props-on run | run Preflight again |
| a pilot is missing | an aircraft in the profile has no named pilot | name one on the Connect page |
| risks not accepted | the profile is new or has changed | accept its risks |
| token expired or does not match | more than 15 min passed, or the profile changed after authorising | authorise again |
| geofence violation on upload | the mission and the aircraft's home or fence disagree | check the launch point and the fence |

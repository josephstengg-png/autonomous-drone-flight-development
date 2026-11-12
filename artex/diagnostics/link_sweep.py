"""Link-load sweep: command round trip against downlink load, aircraft DISARMED (the measurement behind the downlink profiles).

    python3 -m artex.diagnostics.link_sweep --sysid 113 --out sweep.jsonl          # on the companion, radio link up
    python3 -m artex.diagnostics.link_sweep --plan                                 # print the conditions and budgets only

Conditions, in a random order per round: the downlink profiles "outdoors" (PX4 defaults, ~80-86 % of the ELRS telemetry
capacity), "mission" (~22 %), "indoors" (~11 %), each with no setpoint stream. Per condition: apply the profile, settle,
send N harmless MAV_CMD_REQUEST_MESSAGE (HOME_POSITION) and time each COMMAND_ACK, measure the downlink rate. Always restores
PX4's defaults at the end. Stops if the aircraft arms. Experiment 4 (5 Nov 2026) is the two-point version of this.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time

from artex.agent import drone_agent as a

PROFILES = ("outdoors", "mission", "indoors")
TIMEOUT_S, GAP_S, SETTLE_S, DRAIN_S = 15.0, 3.0, 12.0, 8.0


def plan() -> list[dict]:
    return [dict(profile=p, **{k: v for k, v in a.downlink_budget(p).items() if k != "profile"}) if p != "outdoors"
            else dict(profile=p, bytes_per_s="~1000-1070 (measured 4-5 Nov)", share_of_capacity="0.80-0.86") for p in PROFILES]


def summarise(rows: list[dict]) -> dict:
    by = {}
    for r in rows:
        if "rtt" in r:
            by.setdefault(r["profile"], []).append(TIMEOUT_S if r["rtt"] is None else r["rtt"])
    return {p: dict(n=len(v), median_s=round(statistics.median(v), 2), worst_s=round(max(v), 2),
                    lost=sum(x >= TIMEOUT_S for x in v)) for p, v in by.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--link", default="udpout://127.0.0.1:14540")
    ap.add_argument("--sysid", type=int, default=113)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--n", type=int, default=6, help="commands per condition")
    ap.add_argument("--out", default="link_sweep.jsonl")
    ap.add_argument("--plan", action="store_true", help="print the conditions and their estimated budgets, contact nothing")
    args = ap.parse_args()
    if args.plan:
        print(json.dumps(plan(), indent=1))
        return 0
    c = a.connect(args.link, args.sysid)
    if a.vehicle_armed(c) is not False:
        sys.exit("refusing: the aircraft is armed or silent")
    rng, rows, out = random.Random(12112026), [], open(args.out, "w")

    def pump(secs: float, st: dict) -> None:
        end = time.time() + secs
        while time.time() < end:
            m = c.recv_match(blocking=True, timeout=0.05)
            if m is None or m.get_srcSystem() != c.target_system:
                continue
            st["n"] = st.get("n", 0) + 1
            if m.get_type() == "HEARTBEAT" and m.get_srcComponent() == 1 and (m.base_mode & 128):
                raise SystemExit("ARMED: stopping")
            if m.get_type() == "COMMAND_ACK" and m.command == 512 and st.get("wait") and st.get("rtt") is None:
                st["rtt"] = time.time() - st["wait"]

    try:
        for rnd in range(args.rounds):
            order = list(PROFILES)
            rng.shuffle(order)
            for prof in order:
                a.send_downlink(c, a.downlink_restore_commands(), 0.1)
                if prof != "outdoors":
                    for _ in range(2):
                        a.send_downlink(c, a.downlink_commands(prof), 0.12)
                pump(SETTLE_S, {})
                st, t0 = {}, time.time()
                for i in range(args.n):
                    st.update(rtt=None, wait=time.time())
                    c.mav.command_long_send(c.target_system, 1, 512, 0, 242, 0, 0, 0, 0, 0, 0)
                    t1 = time.time()
                    while st["rtt"] is None and time.time() - t1 < TIMEOUT_S:
                        pump(0.1, st)
                    row = dict(round=rnd, profile=prof, i=i, rtt=st["rtt"])
                    rows.append(row)
                    out.write(json.dumps(row) + "\n")
                    st["wait"] = None
                    pump(max(0.0, GAP_S - (time.time() - t1)), st)
                rate = round(st.get("n", 0) / (time.time() - t0), 1)
                out.write(json.dumps(dict(round=rnd, profile=prof, downlink_msgs_per_s=rate)) + "\n")
                out.flush()
                print(f"round {rnd + 1} {prof}: downlink {rate}/s", flush=True)
                pump(DRAIN_S, {})
    finally:
        for _ in range(2):
            a.send_downlink(c, a.downlink_restore_commands(), 0.1)
        print("RESTORED PX4 default streams", flush=True)
    print(json.dumps(summarise(rows), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

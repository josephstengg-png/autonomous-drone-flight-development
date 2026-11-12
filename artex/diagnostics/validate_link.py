#!/usr/bin/env python3
# Run from the repo with the radio emulator up on 24540 and PX4 SITL on 14540:  python -m artex.diagnostics.validate_link
"""Experiment 4 (5 Nov) re-run through the emulator: command round trip and downlink rate, default streams vs trim."""
import json, statistics, time
from artex.agent import drone_agent as a
TRIM = [1, 4, 24, 29, 30, 31, 32, 36, 42, 65, 74, 83, 85, 132, 141, 147, 230, 241, 242, 245, 265, 285, 436, 12901]
c = a.connect("udpout://127.0.0.1:24540", 1)
st = {}
SP = {"hz": 0, "next": 0.0}
def pump(secs):
    n, end = 0, time.time() + secs
    while time.time() < end:
        if SP["hz"] and time.time() >= SP["next"]:      # zero-thrust attitude setpoints, as Exp 4 (aircraft disarmed)
            c.mav.set_attitude_target_send(0, 1, 1, 0b00000111, [1, 0, 0, 0], 0, 0, 0, 0.0)
            SP["next"] = time.time() + 1 / SP["hz"]
        m = c.recv_match(blocking=True, timeout=0.05)
        if m is None or m.get_srcSystem() != 1: continue
        n += 1
        if m.get_type() == "COMMAND_ACK" and m.command == 512 and st.get("wait") and st.get("rtt") is None:
            st["rtt"] = time.time() - st["wait"]
    return n
out = {}
for cond in ("default 0 Hz", "trim 0 Hz", "default 3 Hz", "trim 3 Hz"):
    SP["hz"] = 3 if "3 Hz" in cond else 0
    a.send_downlink(c, [(m, 0) for m in TRIM], 0.1)
    if cond.startswith("trim"):
        for _ in range(2): a.send_downlink(c, [(m, -1) for m in TRIM], 0.12)
    pump(12)
    rate = pump(10) / 10
    rtts = []
    for i in range(12):
        st.update(rtt=None, wait=time.time()); c.mav.command_long_send(1, 1, 512, 0, 242, 0, 0, 0, 0, 0, 0)
        t1 = time.time()
        while st["rtt"] is None and time.time() - t1 < 15: pump(0.1)
        rtts.append(st["rtt"] if st["rtt"] is not None else 15.0); st["wait"] = None
        pump(max(0, 3 - (time.time() - t1)))
    out.setdefault(cond, dict(rates=[], rtts=[]))
    out[cond]["rates"].append(rate); out[cond]["rtts"] += rtts
for _ in range(2): a.send_downlink(c, [(m, 0) for m in TRIM], 0.1)
for cond, d in out.items():
    r = sorted(d["rtts"])
    print(f"{cond:13s} downlink {statistics.mean(d['rates']):5.1f} msgs/s  round trip median {statistics.median(r):.2f} s  "
          f"p90 {r[int(0.9 * len(r))]:.2f} s  lost {sum(x >= 15 for x in r)}/{len(r)}")
print("Exp 4 on ARTEX113 (18 commands each):")
print("  default 0 Hz  27.3 msgs/s  median 0.82 s  p90 0.87 s  0/18 | trim 0 Hz  2.7 msgs/s  median 0.26 s  p90 0.28 s  0/18")
print("  default 3 Hz  24.0 msgs/s  median 0.98 s  p90 1.43 s  0/18 | trim 3 Hz  2.7 msgs/s  median 0.24 s  p90 0.31 s  0/18")

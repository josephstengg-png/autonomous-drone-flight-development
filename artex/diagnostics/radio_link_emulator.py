#!/usr/bin/env python3
"""ELRS radio-link emulator for SITL: sits where the ELRS relay sits on a companion, between the agent and PX4.

    python3 -m artex.diagnostics.radio_link_emulator --px4-port 14540 --agent-port 24540 --seed 1 --log link-1.jsonl
    # the agent then uses  --link udpout://127.0.0.1:24540   (exactly as on a Jetson, where the relay listens on 14540)

Everything the agent and PX4 exchange passes through it -- setpoints, commands, ACKs, mission upload, telemetry -- with the
link behaviour measured on ARTEX113 over ELRS (F500, telemetry ratio 1:2). Every number below comes from a flight log:

  capacity      downlink 9921 bit/s = 1240 B/s (the link's telemetry setting). Uplink UPLINK_BPS, calibrated (no direct
                measurement exists) so that a 20 Hz setpoint stream starves it as in Exp 1 (4 Nov: 7 of 10 commands got
                no answer in 15 s) and 3 Hz does not. Over capacity, packets queue (up to QUEUE_S) and then drop.
  delay         one-way = half the measured command round trip, which depends on how busy the DOWNLINK is (Exp 4,
                5 Nov): lean downlink (~10 % of capacity) median 0.25 s, p90 0.30 s; PX4 default streams (86 %) median
                0.82-0.98 s, p90 1.43 s. Interpolated on the downlink load of the last 2 s; log-normal jitter fitted to
                the p90/median ratio (1.2 lean, 1.46 busy).
  stalls        the uplink is bursty, and burstier when the downlink is busy: with PX4's default streams, gaps of
                1.35-2.9 s between setpoints and 2-4 offboard losses in every ~11 s armed run (ulogs 101-117, 5 Nov);
                with the lean downlink, one loss in ~55 s of flight (ulogs 128-132). Packets are HELD during a stall and
                then arrive together, as the ulogs show. Downlink stalls follow the same model independently.
  loss          1 % each way (0/18 commands lost at 3 Hz in Exp 4, but single setpoints do go missing).
  streams       the aircraft's MAVLink port on ELRS streams PX4's NORMAL-mode set (~27 msgs/s measured, Exp 4) -- not
                the SITL onboard port's ~340 msgs/s. Periodic messages are capped at those rates (STREAM_HZ; ids as
                listed in Exp 4), protocol messages pass, anything else is not something the real link carries and
                is dropped. The agent's SET_MESSAGE_INTERVAL requests change the caps as they would change the real
                port (-1 off, 0 default, else the asked rate), so the downlink profiles and the height-feed request
                behave as on the aircraft.

Writes one JSON line per uplink setpoint delivered (for gap analysis) and a stats line every 5 s; prints a summary on exit.
For the simulator: it binds 127.0.0.1 only, on the SITL API port, where no real aircraft can be.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import random
import select
import signal
import socket
import struct
import sys
import time
from collections import deque

CAPACITY_BPS = 9921 / 8                     # downlink bytes/s: ELRS F500 telemetry 1:2 (measured link setting)
UPLINK_BPS = 700.0                          # CALIBRATED, not measured: a 20 Hz stream (~1.3 kB/s) saturates it as in
                                            # Exp 1 (7/10 commands unanswered); 3 Hz (~0.2 kB/s) does not (8/8 answered)
QUEUE_S = 2.0                               # seconds of backlog a direction holds before dropping
LEAN_LOAD, BUSY_LOAD = 0.10, 0.86           # downlink share of capacity in Exp 4's two conditions
LEAN_ONE_WAY_S, BUSY_ONE_WAY_S = 0.06, 0.38    # fitted so the emulated round trip (with serialisation) gives
                                               # Exp 4's medians: 0.26 s trim, 0.82 s default streams
LEAN_SIGMA, BUSY_SIGMA = math.log(1.2) / 1.2816, math.log(1.46) / 1.2816   # p90/median -> log-normal sigma
# Stalls also depend on the downlink load. Busy (86 %): ulogs 101-117 (5 Nov) -- 2-4 offboard losses per ~11 s armed,
# setpoint gaps 1.35-2.9 s at 3 Hz -> a stall every ~3.7 s lasting 1.0-2.6 s. Lean: ulogs 128-132 (Exp 5) -- one loss in
# ~55 s of flight, Exp 4 trim saw none in 18 commands; durations as the 19 Oct model (0.5-1.5 s).
LEAN_STALL_EVERY_S, BUSY_STALL_EVERY_S = 55.0, 3.7
LEAN_STALL_S, BUSY_STALL_S = (0.5, 1.5), (1.0, 2.6)
LOSS = 0.01

# PX4 NORMAL-mode streams on a telemetry port (approximate defaults, scaled to the ~27 msgs/s measured on ARTEX113).
STREAM_HZ = {1: 1.0, 4: 0.1, 24: 1.0, 29: 1.0, 30: 4.0, 31: 1.0, 32: 1.0, 33: 2.0, 36: 1.0, 42: 1.0, 65: 2.0, 74: 2.0,
             83: 1.0, 85: 1.0, 132: 1.0, 141: 1.0, 147: 0.5, 230: 0.5, 241: 0.5, 242: 0.5, 245: 1.0, 265: 1.0, 285: 1.0,
             436: 0.5, 12901: 1.0}
# No TIMESYNC (111): SITL's onboard port sends it at ~15/s; the real link did not carry it (Exp 4 trimmed total 2.7/s).
PROTOCOL = {0, 20, 21, 22, 23, 37, 38, 39, 40, 41, 43, 44, 45, 46, 47, 49, 51, 73, 75, 76, 77, 148, 244, 253, 300,
            410, 411}
# A rate RAISED above the default is honoured only partly: PX4 fits its streams to the port's data rate. 4 Nov 2026 on
# ARTEX113 over ELRS: LOCAL_POSITION_NED asked at 5 Hz gave 1.64 Hz, at 10 Hz gave 3.0 Hz, total traffic unchanged at 25/s.
RAISED_RATE_FRACTION = 0.3
SETPOINT_IDS = {82, 84, 86}                 # SET_ATTITUDE_TARGET, SET_POSITION_TARGET_LOCAL_NED / _GLOBAL_INT


def requested_interval(frame: bytes) -> tuple[int, float] | None:
    """(msgid, interval_us) if this uplink frame is a COMMAND_LONG MAV_CMD_SET_MESSAGE_INTERVAL (511), else None."""
    if not frame or frame[0] != 0xFD or len(frame) < 12:
        return None
    if (frame[7] | frame[8] << 8 | frame[9] << 16) != 76:
        return None
    pl = frame[10:10 + frame[1]] + bytes(33)              # MAVLink 2 trims trailing zeros
    p1, p2 = struct.unpack_from("<ff", pl, 0)
    return (int(p1), p2) if struct.unpack_from("<H", pl, 28)[0] == 511 else None


def honoured_rate(msgid: int, interval_us: float) -> float:
    """The rate the aircraft's port actually streams after a SET_MESSAGE_INTERVAL: -1 off, 0 default, a lower rate as
    asked, a higher one only partly (RAISED_RATE_FRACTION, measured)."""
    default = STREAM_HZ.get(msgid, 0.0)
    if interval_us < 0:
        return 0.0
    if interval_us == 0:
        return default
    asked = 1e6 / interval_us
    return asked if asked <= default else max(default, RAISED_RATE_FRACTION * asked)


def frames(data: bytes):
    """Split a UDP datagram into MAVLink frames: yields (msgid, frame bytes). Unparseable tails are yielded whole (-1)."""
    i = 0
    while i < len(data):
        stx = data[i]
        if stx == 0xFD and i + 10 <= len(data):
            n = 12 + data[i + 1] + (13 if data[i + 2] & 1 else 0)
            yield data[i + 7] | data[i + 8] << 8 | data[i + 9] << 16, data[i:i + n]
            i += n
        elif stx == 0xFE and i + 6 <= len(data):
            n = 8 + data[i + 1]
            yield data[i + 5], data[i:i + n]
            i += n
        else:
            yield -1, data[i:]
            return


def delay_model(load: float) -> tuple[float, float]:
    """(median one-way delay, log-normal sigma) for this downlink load, interpolated between Exp 4's two conditions."""
    f = min(max((load - LEAN_LOAD) / (BUSY_LOAD - LEAN_LOAD), 0.0), 1.2)
    return (LEAN_ONE_WAY_S + f * (BUSY_ONE_WAY_S - LEAN_ONE_WAY_S),
            LEAN_SIGMA + min(f, 1.0) * (BUSY_SIGMA - LEAN_SIGMA))


def stall_model(load: float, streaming: float = 1.0) -> tuple[float, float, float]:
    """(mean seconds between stalls, shortest, longest) for this downlink load, log-interpolated between the two measured
    conditions. `streaming` (0..1): how much the uplink is carrying a setpoint stream. Stalls need both: Exp 4 at 0 Hz on
    the default streams was tight (p90 0.87 s, 0/18 lost), at 3 Hz it spread (p90 1.43 s), and every ulog gap was in an
    armed run streaming setpoints. No stream, no stalls."""
    f = min(max((load - LEAN_LOAD) / (BUSY_LOAD - LEAN_LOAD), 0.0), 1.0)
    every = math.exp(math.log(LEAN_STALL_EVERY_S) + f * (math.log(BUSY_STALL_EVERY_S) - math.log(LEAN_STALL_EVERY_S)))
    every = every / streaming if streaming > 0 else math.inf
    return (every, LEAN_STALL_S[0] + f * (BUSY_STALL_S[0] - LEAN_STALL_S[0]),
            LEAN_STALL_S[1] + f * (BUSY_STALL_S[1] - LEAN_STALL_S[1]))


def one_way_delay(load: float, rng: random.Random) -> float:
    median, sigma = delay_model(load)
    return median * math.exp(rng.gauss(0.0, sigma))


class Direction:
    """One way across the radio: capacity (serialisation + backlog), stalls, loss, delay."""

    def __init__(self, name: str, rng: random.Random, stalls: bool = True, capacity: float = CAPACITY_BPS):
        self.name, self.rng, self.stalls, self.capacity = name, rng, stalls, capacity
        self.free_at = 0.0                          # when the air is next free (serialisation)
        self.stall_until, self.last_t = 0.0, None
        self.sent = deque()                         # (t, bytes) for load
        self.count = deque()                        # frame times, for the message rate
        self.stats = dict(frames=0, bytes=0, dropped_loss=0, dropped_queue=0, stalls=0, stall_s=0.0)

    def load(self, now: float) -> float:
        while self.sent and self.sent[0][0] < now - 2.0:
            self.sent.popleft()
        return sum(b for _, b in self.sent) / 2.0 / self.capacity

    def rate(self, now: float) -> float:
        while self.count and self.count[0] < now - 2.0:
            self.count.popleft()
        return len(self.count) / 2.0

    def schedule(self, now: float, nbytes: int, delay: float, link_load: float = LEAN_LOAD,
                 streaming: float = 1.0) -> float | None:
        """Arrival time at the far end, or None if lost. `link_load`: the downlink's share of capacity; `streaming`: how much
        the uplink carries a setpoint stream (0..1). Both drive the stalls."""
        self.count.append(now)
        dt, self.last_t = (0.0 if self.last_t is None else max(0.0, now - self.last_t)), now
        if self.stalls and now >= self.stall_until and dt > 0:
            every, lo, hi = stall_model(link_load, streaming)   # a hazard rate, re-read every packet as conditions change
            if every < math.inf and self.rng.random() < 1.0 - math.exp(-dt / every):
                d = self.rng.uniform(lo, hi)
                self.stall_until = now + d
                self.stats["stalls"] += 1
                self.stats["stall_s"] += d
        if self.rng.random() < LOSS:
            self.stats["dropped_loss"] += 1
            return None
        start = max(now, self.free_at, self.stall_until)
        if start - max(now, self.stall_until) > QUEUE_S:      # held by a stall is late, not lost (the ulogs: bursts)
            self.stats["dropped_queue"] += 1
            return None
        self.free_at = start + nbytes / self.capacity
        self.sent.append((now, nbytes))
        self.stats["frames"] += 1
        self.stats["bytes"] += nbytes
        return self.free_at + delay


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--px4-port", type=int, required=True, help="the port PX4 SITL sends its API link to (14540 + i)")
    ap.add_argument("--agent-port", type=int, required=True, help="where the agent sends (its --link udpout port)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--log", default=None, help="JSON lines: setpoint arrivals and 5 s stats")
    ap.add_argument("--no-stalls", action="store_true", help="capacity, delay and loss only")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    up, down = Direction("up", rng, not a.no_stalls, UPLINK_BPS), Direction("down", rng, not a.no_stalls)
    px4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    px4.bind(("127.0.0.1", a.px4_port))
    gcs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    gcs.bind(("127.0.0.1", a.agent_port))
    px4_addr = agent_addr = None
    heap: list = []
    seq = 0
    caps: dict[int, float] = {}                 # msgid -> next time a periodic message may pass
    rates = dict(STREAM_HZ)                     # what the aircraft's port streams now: defaults, changed by SET_MESSAGE_INTERVAL
    log = open(a.log, "w") if a.log else None
    t0 = last_stat = time.time()
    shaped = dict(capped=0, foreign=0)
    running = [True]
    signal.signal(signal.SIGTERM, lambda *_: running.__setitem__(0, False))
    print(f"[radio] PX4 127.0.0.1:{a.px4_port} <-> agent 127.0.0.1:{a.agent_port}, seed {a.seed}, "
          f"{'no stalls' if a.no_stalls else 'stalls by downlink load (busy: 1.0-2.6 s every ~3.7 s; lean: 0.5-1.5 s every ~55 s)'}",
          file=sys.stderr, flush=True)
    try:
        while running[0]:
            now = time.time()
            timeout = max(0.0, min(0.05, heap[0][0] - now)) if heap else 0.05
            ready, _, _ = select.select([px4, gcs], [], [], timeout)
            now = time.time()
            for sock in ready:
                data, addr = sock.recvfrom(65535)
                if sock is px4:
                    px4_addr = addr
                    if agent_addr is None:
                        continue
                    for mid, fr in frames(data):
                        if mid in rates:
                            if rates[mid] <= 0 or now < caps.get(mid, 0.0):
                                shaped["capped"] += 1
                                continue
                            caps[mid] = now + 1.0 / rates[mid]
                        elif mid not in PROTOCOL:
                            shaped["foreign"] += 1
                            continue
                        load = down.load(now)
                        at = down.schedule(now, len(fr), one_way_delay(load, rng), load,
                                           min(max((up.rate(now) - 1.5) / 1.5, 0.0), 1.0))
                        if at is not None:
                            seq += 1
                            heapq.heappush(heap, (at, seq, gcs, fr, "down"))
                else:
                    agent_addr = addr
                    if px4_addr is None:
                        continue
                    streaming = min(max((up.rate(now) - 1.5) / 1.5, 0.0), 1.0)   # 3 Hz + heartbeat = 4/s -> 1
                    for mid, fr in frames(data):
                        load = down.load(now)
                        at = up.schedule(now, len(fr), one_way_delay(load, rng), load, streaming)
                        req = requested_interval(fr) if at is not None else None
                        if req:                         # the real port obeys interval requests: -1 off, 0 default
                            rid, us = req
                            rates[rid] = honoured_rate(rid, us)
                        if at is not None:
                            seq += 1
                            heapq.heappush(heap, (at, seq, px4, fr, "up" if mid not in SETPOINT_IDS else "sp"))
            while heap and heap[0][0] <= now:
                at, _, sock, fr, kind = heapq.heappop(heap)
                dst = px4_addr if sock is px4 else agent_addr
                if dst:
                    sock.sendto(fr, dst)
                if kind == "sp" and log:
                    log.write(json.dumps(dict(t=round(at, 3), sp=1)) + "\n")
            if now - last_stat >= 5.0 and log:
                last_stat = now
                log.write(json.dumps(dict(t=round(now, 1), down_load=round(down.load(now), 2),
                                          one_way_median_s=round(delay_model(down.load(now))[0], 3),
                                          up=dict(up.stats), down=dict(down.stats), shaped=dict(shaped))) + "\n")
                log.flush()
    finally:
        summary = dict(seconds=round(time.time() - t0, 1), up=up.stats, down=down.stats, shaped=shaped)
        print("[radio] " + json.dumps(summary), file=sys.stderr, flush=True)
        if log:
            log.write(json.dumps(dict(summary=summary)) + "\n")
            log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

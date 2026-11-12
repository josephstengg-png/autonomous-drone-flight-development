"""Fallback when the ground-station laptop disappears mid-run: the laptop -> jetson-2 -> jetson-1 hierarchy (12 Nov 2026).

    # on jetson-2: act if the laptop is gone
    python3 -m artex.fleetops.fallback --superior 192.168.144.51 --peer jetson-1 --action hold
    # on jetson-1: act only if the laptop AND jetson-2 are gone
    python3 -m artex.fleetops.fallback --superior 192.168.144.51 --superior 192.168.144.102 --action hold

STOP-ONLY and deterministic, on purpose. When every superior has been unreachable for --timeout-s while an aircraft agent is
running here or on a peer, it writes the agents' out-of-band ABORT flag (the same file artex.fleetops.takeover writes)
with --action (hold | land) here and on each peer. The agents' guards act on it. It never arms, starts, resumes, or clears
a flag: when the laptop comes back the flags stay until the operator clears them (python3 -m artex.fleetops.takeover --clear).
No language model is involved: stopping must not depend on one.

Fleet waypoint runs already have a slower net: each resident agent returns to launch if the console is silent for its
--failsafe-s (60 s by default). This watchdog acts sooner and on every aircraft together.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from artex.common.layout import REMOTE_DIR

AGENT_MARK = b"artex.agent.drone_agent"
ACTIONS = ("hold", "land")


def agents_running(proc: Path = Path("/proc"), exclude: set[int] | None = None) -> int:
    """How many aircraft agents run on this machine (scans /proc; never matches itself or its parent shell)."""
    skip = (exclude or set()) | {os.getpid(), os.getppid()}
    n = 0
    for d in proc.iterdir() if proc.exists() else []:
        if not d.name.isdigit() or int(d.name) in skip:
            continue
        try:
            cmd = (d / "cmdline").read_bytes()
        except OSError:
            continue
        if AGENT_MARK in cmd and b"fallback" not in cmd:
            n += 1
    return n


def reachable(host: str, timeout_s: float = 1.0) -> bool:
    r = subprocess.run(["ping", "-c", "1", "-W", str(max(1, int(timeout_s))), host], capture_output=True)
    return r.returncode == 0


def decide(state: dict, superiors_ok: bool, active: bool, now: float, timeout_s: float) -> str | None:
    """'act' when every superior has been gone for timeout_s while an agent runs and we have not acted yet;
    'back' once when a superior returns after we acted; None otherwise. `state` keeps lost_since and acted."""
    if superiors_ok:
        state["lost_since"] = None
        if state.get("acted") and not state.get("told_back"):
            state["told_back"] = True
            return "back"
        return None
    state["lost_since"] = state.get("lost_since") or now
    if active and not state.get("acted") and now - state["lost_since"] >= timeout_s:
        state["acted"], state["told_back"] = True, False
        return "act"
    return None


def write_flag(action: str, peers: list[str], root: Path) -> list[str]:
    """The ABORT flag here and on each peer. Returns where it was written."""
    done = []
    try:
        (root / "ABORT").write_text(action + "\n")
        done.append("local")
    except OSError as exc:
        print(f"[fallback] could not write the local flag: {exc}", file=sys.stderr, flush=True)
    for peer in peers:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", peer,
                            f"cd {REMOTE_DIR} && printf '%s\\n' {action} > ABORT"], capture_output=True, timeout=15)
        if r.returncode == 0:
            done.append(peer)
        else:
            print(f"[fallback] could not reach {peer} to set its flag", file=sys.stderr, flush=True)
    return done


def peer_agents(peer: str) -> int:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", peer,
                        f"cd {REMOTE_DIR} && python3 -m artex.fleetops.fallback --probe"], capture_output=True, text=True, timeout=15)
    try:
        return int(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--superior", action="append", default=[], help="host that outranks this one (repeat); act when ALL are gone")
    ap.add_argument("--peer", action="append", default=[], help="companion whose aircraft also get the flag (repeat)")
    ap.add_argument("--action", choices=ACTIONS, default="hold", help="hold needs a GPS position; land works anywhere")
    ap.add_argument("--timeout-s", type=float, default=15.0)
    ap.add_argument("--period-s", type=float, default=2.0)
    ap.add_argument("--root", default=str(Path(REMOTE_DIR).expanduser()), help="where the agents look for the ABORT flag")
    ap.add_argument("--probe", action="store_true", help="print how many agents run here, then exit (used by a superior)")
    a = ap.parse_args()
    if a.probe:
        print(agents_running())
        return 0
    if not a.superior:
        ap.error("give at least one --superior")
    state: dict = {}
    print(f"[fallback] watching {', '.join(a.superior)}; on loss for {a.timeout_s:g} s with an agent running: "
          f"{a.action.upper()} here and on {', '.join(a.peer) or 'no peers'}", flush=True)
    while True:
        ok = any(reachable(h) for h in a.superior)
        active = agents_running() > 0 or (not ok and any(peer_agents(p) > 0 for p in a.peer))
        verdict = decide(state, ok, active, time.time(), a.timeout_s)
        if verdict == "act":
            where = write_flag(a.action, a.peer, Path(a.root))
            print(f"[fallback] every superior gone for {a.timeout_s:g} s with aircraft agents running: {a.action.upper()} flag set "
                  f"({', '.join(where) or 'NOWHERE'}). Pilots: take over by handset.", flush=True)
        elif verdict == "back":
            print("[fallback] a superior is back. The flags STAY until the operator clears them "
                  "(python3 -m artex.fleetops.takeover --clear).", flush=True)
        time.sleep(a.period_s)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Ground-station GUI server: mock 5 made real (4 Nov 2026). Standard library only.

    python3 -m artex.console.gs_server                 # LIVE: runs the agent on the companion over ssh
    python3 -m artex.console.gs_server --demo          # DEMO: a simulated aircraft, nothing is contacted
    then open http://127.0.0.1:8787

It is a front end over the existing tools and decides nothing about flight: profiles become a
`drone_agent.py offboard-flight` command (artex/console/gs_core.py), the agent runs where it always has
(on the Jetson, over ssh) and stops at READY, and the aircraft arms only when the page sends `ARM`
after the operator's recorded authorisation and a deliberate press. Preflight is artex/fleetops/preflight.py;
the out-of-band abort is artex/fleetops/takeover.py. The handset stays the real override.

Local only, on purpose. It binds 127.0.0.1, rejects any other Host or Origin (DNS rebinding) and
every POST must carry X-GS-Client (a web page in your browser cannot send that cross-origin).
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from artex.console import gs_core as core  # noqa: E402
from artex.console import gs_fleet as fleet  # noqa: E402
from artex.planner import gs_planner as planner  # noqa: E402
from artex.console import sessions  # noqa: E402
from artex.assistant import agent as assistant  # noqa: E402
from artex.assistant.llm import OllamaChat  # noqa: E402
from artex.fleetops.swarm_deploy import host_argv, push as push_plan  # noqa: E402
from artex.common import layout, version  # noqa: E402

ROOT = core.ROOT
STATIC = Path(__file__).resolve().parent / "static"
GUI = STATIC / "index.html"
MIME = {".js": "application/javascript", ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml",
        ".json": "application/json", ".html": "text/html", ".txt": "text/plain"}
GATE_S = 150          # drone_agent --gate-timeout-s: how long READY waits for the operator's ARM
SEND_WORDS = {"ARM", "ABORT", "LAND", "HOLD"}
DEMO_WORDS = {"DEMO_TAKEOVER", "DEMO_KILL"}


class Run:
    """One agent process: its events, its log, its state, and the pipe the operator's words go down."""

    def __init__(self, rec: dict, argv: list[str], demo: bool, feed: list | None = None,
                 lock: threading.Lock | None = None, tag: int | None = None):
        self.rec, self.argv, self.demo = rec, argv, demo
        # a member of a GroupRun shares the group's feed and lock, and tags its lines with its sysid
        self.feed: list[dict] = feed if feed is not None else []
        self.lock = lock or threading.Lock()
        self.tag = tag
        self.state = "starting"
        self.stage = None
        self.t = dict(started=time.time(), ready=None, armed=None, done=None)
        self.result: dict | None = None
        self.arm_sent = False
        self.proc: subprocess.Popen | None = None
        self.saved = False

    def start(self) -> None:
        if self.demo:
            cmd = [sys.executable, str(ROOT / "demo" / "fake_agent.py")] + self.argv[len(layout.AGENT):]
        else:
            ac = core.AIRCRAFT[self.rec["profile"]["sysid"]]
            cmd, _ = host_argv(dict(host=ac["host"], remote_dir=layout.REMOTE_DIR), self.argv)
            cmd[1:1] = ["-o", "ConnectTimeout=8"]
        self.rec["command"] = cmd
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1, cwd=str(ROOT))
        threading.Thread(target=self._read, args=(self.proc.stdout, "event"), daemon=True).start()
        threading.Thread(target=self._read, args=(self.proc.stderr, "log"), daemon=True).start()
        threading.Thread(target=self._wait, daemon=True).start()

    def _push(self, kind: str, **kw) -> None:
        with self.lock:
            self.feed.append(dict(n=len(self.feed), t=round(time.time(), 2), kind=kind,
                                  **(dict(sysid=self.tag) if self.tag else {}), **kw))

    def _read(self, pipe, kind: str) -> None:
        for line in pipe:
            line = line.rstrip("\n")
            if not line:
                continue
            if kind == "log":
                self._push("log", text=line)
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                self._push("log", text=line)
                continue
            self._on_event(ev)

    def _on_event(self, ev: dict) -> None:
        if "verb" in ev:
            self.result = ev
            self.t["done"] = time.time()
            self.state = "done"
            self._push("result", **{"data": ev})
            return
        e = ev.get("event")
        if e == "ready":
            self.state, self.t["ready"] = "ready", time.time()
        elif e == "armed":
            self.state, self.t["armed"] = "armed", time.time()
        elif e == "stage":
            self.stage = ev.get("stage")
        elif e in ("rc_takeover", "takeover_classified", "still_armed"):
            if self.state != "done":
                self.state = "pilot"
        self._push("event", data=ev)

    def _wait(self) -> None:
        rc = self.proc.wait()
        time.sleep(0.3)     # let the readers drain
        if self.result is None:
            tail = [f["text"] for f in self.feed if f["kind"] == "log"][-1:]
            self.result = dict(error=f"agent exited with code {rc}" + (f": {tail[0]}" if tail else "")
                               + (" (ssh could not reach the companion)" if rc == 255 else ""), passed=False)
            self.state = "ended"
        self.t["done"] = self.t["done"] or time.time()
        with self.lock:
            own = [f for f in self.feed if f.get("sysid") in (None, self.tag)] if self.tag else list(self.feed)
        self.rec.update(state=self.state, result=self.result, finished=time.strftime("%Y-%m-%d %H:%M:%S"), feed=own)
        core.save_run(self.rec)
        self.saved = True

    def send(self, word: str) -> bool:
        if not self.proc or self.proc.poll() is not None:
            return False
        try:
            self.proc.stdin.write(word + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return False
        self._push("sent", text=word)
        return True

    @property
    def active(self) -> bool:
        return self.state not in ("done", "ended")

    def snapshot(self, since: int) -> dict:
        with self.lock:
            feed = self.feed[since:]
        return dict(id=self.rec["id"], state=self.state, stage=self.stage, t=self.t, result=self.result,
                    profile=self.rec["profile"], operator=self.rec["operator"], feed=feed, demo=self.demo,
                    gate_s=GATE_S, arm_sent=self.arm_sent, timeline=core.timeline(self.rec["profile"]),
                    speed=float(os.environ.get("DEMO_SPEED", "4")) if self.demo else 1.0)


class GroupRun:
    """A props-off bench test on several aircraft at once (operator's request, 10 Nov 2026): one offboard agent per
    aircraft, each stops at READY; ARM goes to all of them only when ALL are READY; if any has not armed within
    ARM_ALL_TIMEOUT_S every aircraft is told to DISARM. Each member keeps its own run record; the group adds one more."""

    ARM_ALL_TIMEOUT_S = 25.0      # the radio agent retries ARM every 8 s for 20 s

    def __init__(self, rec: dict, demo: bool):
        self.rec, self.demo, self.p = rec, demo, rec["profile"]
        self.feed: list[dict] = []
        self.lock = threading.Lock()
        self.state = "starting"
        self.t = dict(started=time.time(), ready=None, armed=None, done=None)
        self.result: dict | None = None
        self.arm_sent = False
        self.stage = None
        self.members: dict[int, Run] = {}
        for sid in self.p["sysids"]:
            mrec = dict(rec, id=f"{rec['id']}-{sid}", group=rec["id"], profile=dict(self.p, sysid=sid, sysids=[sid]))
            self.members[sid] = Run(mrec, core.agent_argv(self.p, f"/tmp/gs_{rec['id']}-{sid}.csv", sid), demo,
                                    feed=self.feed, lock=self.lock, tag=sid)

    def _note(self, text: str, bad: bool = False) -> None:
        with self.lock:
            self.feed.append(dict(n=len(self.feed), t=round(time.time(), 2), kind="note", text=text, bad=bad))

    def start(self) -> None:
        for m in self.members.values():
            m.start()
        threading.Thread(target=self._watch, daemon=True).start()

    def send(self, word: str) -> bool:
        return all([m.send(word) for m in self.members.values()])

    def _watch(self) -> None:
        ms = self.members
        armed = lambda m: m.t["armed"] is not None  # noqa: E731
        while True:
            time.sleep(0.2)
            if self.state == "starting":
                if all(m.state == "ready" for m in ms.values()):
                    self.state, self.t["ready"] = "ready", time.time()
                    self._note("Every aircraft is READY. ARM ALL arms them together.")
                elif any(not m.active for m in ms.values()):
                    down = [f"ARTEX{s}" for s, m in ms.items() if not m.active]
                    self._note(f"{', '.join(down)} stopped before READY: releasing the others, nobody arms.", bad=True)
                    for m in ms.values():
                        if m.active:
                            m.send("STOP")          # any word but ARM at the gate = not authorised to arm
                    self.state = "not_ready"
            elif self.state == "ready" and self.arm_sent:
                self.state, arm_at = "arming", time.time()
            elif self.state == "arming":
                if all(armed(m) for m in ms.values()):
                    self.state, self.t["armed"] = "armed", time.time()
                    spread = max(m.t["armed"] for m in ms.values()) - min(m.t["armed"] for m in ms.values())
                    self.rec["arm_spread_s"] = round(spread, 2)
                    self._note(f"Every aircraft ARMED (spread {spread:.1f} s as seen here).")
                elif time.time() - arm_at > self.ARM_ALL_TIMEOUT_S or any(not m.active and not armed(m)
                                                                          for m in ms.values()):
                    late = [f"ARTEX{s}" for s, m in ms.items() if not armed(m)]
                    self._note(f"{', '.join(late)} did not arm: DISARM to every aircraft, nobody proceeds.", bad=True)
                    for m in ms.values():
                        m.send("DISARM")
                    self.state = "disarming"
            elif self.state == "ready" and not self.arm_sent and any(not m.active for m in ms.values()):
                self._note("An agent stopped while waiting at READY: releasing the others.", bad=True)
                for m in ms.values():
                    if m.active:
                        m.send("STOP")
                self.state = "not_ready"
            if not any(m.active for m in ms.values()) and all(m.saved for m in ms.values()):
                break
        per = {str(s): dict(id=m.rec["id"], state=m.state, outcome=(m.result or {}).get("outcome"),
                            error=(m.result or {}).get("error"), passed=(m.result or {}).get("passed"))
               for s, m in ms.items()}
        gate_failed = self.state == "disarming"
        outcome = ("not every aircraft reached READY -- nobody armed" if self.state == "not_ready" else
                   "ARM ALL failed -- every aircraft told to DISARM" if gate_failed else
                   "; ".join(f"ARTEX{s}: {v['outcome'] or v['error']}" for s, v in per.items()))
        self.result = dict(outcome=outcome, passed=all(v["passed"] for v in per.values()), aircraft=per)
        self.state, self.t["done"] = ("done" if self.result["passed"] else "ended"), time.time()
        self._note(f"GROUP RESULT: {outcome}", bad=not self.result["passed"])
        self.rec.update(kind="group", state=self.state, result=self.result, members=[m.rec["id"] for m in ms.values()],
                        finished=time.strftime("%Y-%m-%d %H:%M:%S"), feed=self.feed)
        core.save_run(self.rec)

    @property
    def active(self) -> bool:
        return self.state not in ("done", "ended")

    def snapshot(self, since: int) -> dict:
        with self.lock:
            feed = self.feed[since:]
        members = [dict(sysid=s, state=m.state, stage=m.stage, outcome=(m.result or {}).get("outcome"),
                        error=(m.result or {}).get("error"), passed=(m.result or {}).get("passed"), armed=m.t["armed"] is not None)
                   for s, m in self.members.items()]
        return dict(id=self.rec["id"], group=True, state=self.state, stage=None, t=self.t, result=self.result,
                    profile=self.p, operator=self.rec["operator"], feed=feed, demo=self.demo, gate_s=GATE_S,
                    arm_sent=self.arm_sent, timeline=core.timeline(self.p), members=members,
                    speed=float(os.environ.get("DEMO_SPEED", "4")) if self.demo else 1.0)


FLEET_WORDS = {"HOLD": "HOLD", "RTL": "ABORT rtl", "LAND": "ABORT land"}
FLEET_DEMO_WORDS = {"DEMO_TAKEOVER", "DEMO_LINK"}
PREPARE_TIMEOUT_S = 240.0     # upload + read-back + contract over the radio (~1 s per item)


class Member:
    """One aircraft's resident agent (`drone_agent.py prepare --resident`), spoken to in json lines."""

    def __init__(self, run: "FleetRun", m: dict, argv: list[str]):
        self.run, self.sysid, self.plan, self.argv = run, m["sysid"], m["plan"], argv
        self.replies: queue.Queue = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.wlock = threading.Lock()     # the conductor and a reader thread (HOLD on a fault) may both write
        self.v = dict(sysid=self.sysid, plan=m["plan"], link=m["link"], ready=None, items=None, checks=[],
                      token=None, armed=False, started=False, held=False, fault=None, finished=False,
                      status=None, misses=0, error=None)

    def start(self, demo: bool) -> None:
        if demo:
            cmd = [sys.executable, str(ROOT / "demo" / "fake_resident_agent.py")] + self.argv[len(layout.AGENT):]
        else:
            cmd, _ = host_argv(dict(host=core.AIRCRAFT[self.sysid]["host"], remote_dir=layout.REMOTE_DIR), self.argv)
            cmd[1:1] = ["-o", "ConnectTimeout=8"]
        self.run.rec.setdefault("commands", {})[str(self.sysid)] = cmd
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1, cwd=str(ROOT))
        threading.Thread(target=self._out, daemon=True).start()
        threading.Thread(target=self._err, daemon=True).start()

    def _out(self) -> None:
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                self.run.push("log", self.sysid, text=line)
                continue
            if "verb" in ev:
                self.replies.put(ev)
            else:
                self.run.on_event(self, ev)

    def _err(self) -> None:
        for line in self.proc.stderr:
            if line.strip():
                self.run.push("log", self.sysid, text=line.rstrip())

    @property
    def alive(self) -> bool:
        return bool(self.proc) and self.proc.poll() is None

    def send(self, cmd: str) -> bool:
        if not self.alive:
            return False
        try:
            with self.wlock:
                self.proc.stdin.write(cmd + "\n")
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return False
        self.run.push("sent", self.sysid, text=cmd.split()[0] if cmd.split()[0] in ("ARM", "START") else cmd)
        return True

    def await_verb(self, verb: str, timeout: float) -> dict | None:
        """The next reply of this kind; anything else on the way goes to the feed. None on timeout or a dead agent."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.replies.get(timeout=min(0.5, max(0.05, deadline - time.time())))
            except queue.Empty:
                if not self.alive and self.replies.empty():
                    return None
                continue
            if msg.get("verb") == verb:
                return msg
            if msg.get("verb") == "failsafe":
                self.run.mark_fault(self, "the agent's own failsafe returned it to launch (ground station silent)")
            self.run.push("reply", self.sysid, data=msg)
        return None


class FleetRun:
    """A multi-aircraft waypoint run: prepare -> barrier -> ARM ALL -> START ALL -> watch (artex/console/gs_fleet.py)."""

    def __init__(self, rec: dict, demo: bool):
        self.rec, self.demo, self.p = rec, demo, rec["profile"]
        self.feed: list[dict] = []
        self.lock = threading.Lock()
        self.state = "starting"
        self.t = dict(started=time.time(), ready=None, armed=None, flying=None, done=None)
        self.result: dict | None = None
        self.arm_evt, self.start_evt, self.cancel_evt = threading.Event(), threading.Event(), threading.Event()
        rid = rec["id"]
        self.members = {m["sysid"]: Member(self, m, fleet.agent_argv(self.p, m, f"gs{rid}"))
                        for m in self.p["aircraft"]}

    # ---- feed --------------------------------------------------------------------
    def push(self, kind: str, sysid: int | None = None, **kw) -> None:
        with self.lock:
            self.feed.append(dict(n=len(self.feed), t=round(time.time(), 2), kind=kind, sysid=sysid, **kw))

    def note(self, text: str, bad: bool = False, sysid: int | None = None) -> None:
        self.push("note", sysid, text=text, bad=bad)

    def on_event(self, m: Member, ev: dict) -> None:
        self.push("event", m.sysid, data=ev)
        if ev.get("event") in ("rc_takeover", "takeover_classified") and m.v["started"]:
            src = ev.get("source")
            self.mark_fault(m, "the pilot took over" if src in (None, "pilot_switch", "kill_switch")
                            else f"left the mission ({src})")

    def mark_fault(self, m: Member, why: str) -> None:
        """First fault on an aircraft after START: flag it and put every other flying aircraft in HOLD."""
        with self.lock:
            if m.v["fault"]:
                return
            m.v["fault"] = why
            vs = {s: mm.v for s, mm in self.members.items()}
        self.note(f"ARTEX{m.sysid}: FAULT -- {why}", bad=True, sysid=m.sysid)
        targets = fleet.hold_targets(vs, m.sysid)
        for s in targets:
            if self.members[s].send("HOLD"):
                self.members[s].v["held"] = True
        if targets:
            self.note("Others put in HOLD: " + ", ".join(f"ARTEX{s}" for s in targets)
                      + ". Decide: RTL, LAND or take over by handset.", bad=True)
        if self.state in ("flying", "launching"):
            self.state = "fault"

    # ---- the sequence ------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._conduct, daemon=True).start()

    def _all(self, verb: str, timeout: float) -> dict[int, dict | None]:
        """Await one reply from every agent, in parallel (the replies queue up while we wait on the first)."""
        deadline = time.time() + timeout
        return {s: m.await_verb(verb, max(0.1, deadline - time.time())) for s, m in self.members.items()}

    def _wait_press(self, evt: threading.Event, seconds: float) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            if evt.wait(0.2):
                return True
            if self.cancel_evt.is_set():
                return False
        return False

    def _disarm_all(self, why: str) -> None:
        self.note(f"DISARM ALL: {why}", bad=True)
        for m in self.members.values():
            m.send("DISARM")       # also the ones that did not arm: a late ARM may still be in the radio
        for s, r in self._all("disarm", 30.0).items():
            ok = bool(r and r.get("disarmed"))
            self.members[s].v["armed"] = not ok and self.members[s].v["armed"]
            self.note(f"ARTEX{s}: " + ("disarmed" if ok else "DISARM NOT CONFIRMED -- disarm by handset (ch6 / hold kill 5 s)"),
                      bad=not ok, sysid=s)

    def _finish(self, outcome: str, ok: bool) -> None:
        self.result = dict(outcome=outcome, passed=ok,
                           aircraft={str(s): {k: v for k, v in m.v.items() if k != "token"}
                                     for s, m in self.members.items()})
        self.note(f"RUN ENDED: {outcome}", bad=not ok)

    def _conduct(self) -> None:
        try:
            self._sequence()
        except Exception as exc:          # never leave the record unwritten
            self.note(f"ground station error: {type(exc).__name__}: {exc}", bad=True)
            self._finish(f"ground station error ({type(exc).__name__})", False)
        finally:
            for m in self.members.values():
                m.send("QUIT")
            time.sleep(1.0)
            for m in self.members.values():
                if m.alive:
                    try:
                        m.proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        m.proc.kill()
            self.state = "done" if self.result and self.result.get("passed") else "ended"
            self.t["done"] = time.time()
            self.rec.update(state=self.state, result=self.result, finished=time.strftime("%Y-%m-%d %H:%M:%S"),
                            feed=self.feed)
            core.save_run(self.rec)

    def _sequence(self) -> None:
        mode = self.p["mode"]
        # 1. push and prepare
        self.state = "preparing"
        for s, m in self.members.items():
            if not self.demo:
                err = push_plan(dict(slot=s, host=core.AIRCRAFT[s]["host"], remote_dir=layout.REMOTE_DIR),
                                Path(m.plan))
                if err:
                    m.v.update(ready=False, error=f"plan not pushed: {err}")
                    self.note(f"ARTEX{s}: plan could not be copied to the companion -- {err}", bad=True, sysid=s)
                    continue
            m.start(self.demo)
        for s, r in self._all("prepare", PREPARE_TIMEOUT_S).items():
            m = self.members[s]
            if m.v["error"]:
                continue
            if not r:
                m.v.update(ready=False, error="no prepare report (agent died or timed out)")
                self.note(f"ARTEX{s}: no prepare report", bad=True, sysid=s)
                continue
            m.v.update(ready=bool(r.get("ready")), items=r.get("items"), checks=r.get("checks") or [],
                       token=r.get("token"), top_alt_m=r.get("top_alt_m"))
            self.note(f"ARTEX{s}: " + ("READY" if r.get("ready") else "NOT READY -- " + ", ".join(r.get("reasons") or []))
                      + f" ({r.get('items')} items on board check: "
                      + next((c["detail"] for c in m.v["checks"] if c["name"] == "mission_uploaded"), "?") + ")",
                      bad=not r.get("ready"), sysid=s)
        all_ready = all(m.v["ready"] for m in self.members.values())
        self.t["ready"] = time.time()
        if mode == "upload":
            self.state = "ready" if all_ready else "not_ready"
            uploaded = all(any(c["name"] == "mission_uploaded" and c["ok"] for c in m.v["checks"])
                           for m in self.members.values())
            return self._finish("missions uploaded and verified" if uploaded else "upload FAILED on at least one aircraft",
                                uploaded)
        if not all_ready:
            self.state = "not_ready"
            return self._finish("not every aircraft READY -- NOBODY armed", False)

        # 2. gate 1: ARM ALL
        self.state = "ready"
        self.note(f"BARRIER PASSED: every aircraft READY. Waiting up to {fleet.READY_GATE_S:.0f} s for ARM ALL.")
        if not self._wait_press(self.arm_evt, fleet.READY_GATE_S):
            return self._finish("ARM ALL was not pressed -- nobody armed", False)
        self.state = "arming"
        at = time.time() + fleet.ARM_LEAD_S
        for m in self.members.values():
            m.send(f"ARM {m.v['token']} {at:.3f}")
        arms = self._all("arm", 40.0)
        for s, r in arms.items():
            self.members[s].v["armed"] = bool(r and r.get("armed"))
            self.note(f"ARTEX{s}: " + ("ARMED" if self.members[s].v["armed"]
                                       else f"NOT ARMED -- {(r or {}).get('error', 'no reply')}"),
                      bad=not self.members[s].v["armed"], sysid=s)
        if not all(m.v["armed"] for m in self.members.values()):
            self.state = "disarming"
            self._disarm_all("not every aircraft armed, so nobody proceeds")
            return self._finish("ARM gate failed -- disarmed", False)
        times = [r["t_epoch"] for r in arms.values() if r and r.get("t_epoch")]
        spread = round(max(times) - min(times), 2) if len(times) > 1 else None
        self.rec["arm_spread_s"] = spread
        self.state, self.t["armed"] = "armed", time.time()
        self.note(f"ALL ARMED (arm spread {spread} s).")
        if mode == "arm":
            self._wait_press(self.cancel_evt, self.p["arm_hold_s"])
            self.state = "disarming"
            self._disarm_all(f"arm-only run: held {self.p['arm_hold_s']:g} s")
            ok = not any(m.v["armed"] for m in self.members.values())
            return self._finish("ARM ALL and DISARM ALL proven" if ok else "armed, but a DISARM was not confirmed", ok)

        # 3. gate 2: START ALL, inside PX4's auto-disarm window
        self.note(f"Waiting up to {fleet.START_WINDOW_S:g} s for START ALL (PX4 disarms on the ground after ~10 s).")
        if not self._wait_press(self.start_evt, fleet.START_WINDOW_S):
            self.state = "disarming"
            self._disarm_all("START ALL not pressed inside the window" if not self.cancel_evt.is_set()
                             else "operator cancelled")
            return self._finish("not started -- disarmed", False)
        self.state = "launching"
        at = time.time() + fleet.START_LEAD_S
        for m in self.members.values():
            m.send(f"START {m.v['token']} {at:.3f}")
        starts = self._all("start", 60.0)
        for s, r in starts.items():
            self.members[s].v["started"] = bool(r and r.get("started"))
        failed = [s for s, m in self.members.items() if not m.v["started"]]
        self.state, self.t["flying"] = "flying", time.time()
        for s in failed:
            self.mark_fault(self.members[s], f"did not start: {(starts[s] or {}).get('error', 'no reply')}")
            self.members[s].send("DISARM")
        if not failed:
            self.note("ALL AWAY: every aircraft started its mission.")

        # 4. watch
        deadline = time.time() + 1800
        while time.time() < deadline and not self.cancel_evt.is_set():
            live = [m for m in self.members.values() if not m.v["finished"] and m.alive]
            if not live:
                break
            for m in live:
                m.send("STATUS")
            for m in live:
                r = m.await_verb("status", 10.0)
                if r:
                    prev = m.v["status"] or {}
                    r["_was_rtl"] = prev.get("_was_rtl") or (r.get("main_mode") == fleet.PX4_MAIN_AUTO
                                                             and r.get("sub_mode") in (5, 6))
                    m.v.update(status=r, misses=0)
                else:
                    m.v["misses"] += 1
                why = fleet.member_fault(m.v["status"], m.v["items"], m.v["misses"])
                if why and not m.v["fault"]:
                    self.mark_fault(m, why)
                st = m.v["status"] or {}
                if st.get("armed") is False and m.v["started"]:
                    m.v["finished"] = True
                    m.v["armed"] = False
                    how = (" (after a fault)" if m.v["fault"] else " (after HOLD / an operator command)" if m.v["held"]
                           else " -- mission complete")
                    self.note(f"ARTEX{m.sysid}: on the ground, disarmed{how}", sysid=m.sysid)
            if all(m.v["finished"] for m in self.members.values()):
                break
            self.cancel_evt.wait(fleet.WATCH_POLL_S)
        faults = [s for s, m in self.members.items() if m.v["fault"]]
        if self.cancel_evt.is_set():
            return self._finish("watch ended by the operator (agents released; aircraft keep their PX4 mode)", False)
        if not all(m.v["finished"] for m in self.members.values()):
            return self._finish("watch timed out or an agent died before landing", False)
        held = [s for s, m in self.members.items() if m.v["held"] and not m.v["fault"]]
        if not faults and held:
            return self._finish("landed after an operator HOLD/RTL/LAND on " + ", ".join(f"ARTEX{s}" for s in held), True)
        return self._finish("every aircraft completed its mission" if not faults else
                            "landed, with faults on " + ", ".join(f"ARTEX{s}" for s in faults), not faults)

    # ---- operator ----------------------------------------------------------------
    def word(self, w: str, target: str) -> list[int]:
        cmd = FLEET_WORDS.get(w) or (w if self.demo and w in FLEET_DEMO_WORDS else None)
        if not cmd:
            raise ValueError(f"{w} is not sent from here")
        sent = []
        for s, m in self.members.items():
            if target in ("all", str(s)) and m.send(cmd):
                sent.append(s)
                if w == "HOLD":
                    m.v["held"] = True
        return sent

    @property
    def active(self) -> bool:
        return self.state not in ("done", "ended")

    def snapshot(self, since: int) -> dict:
        with self.lock:
            feed = self.feed[since:]
        now = time.time()
        window = None
        if self.state == "ready" and self.t["ready"]:
            window = max(0.0, fleet.READY_GATE_S - (now - self.t["ready"]))
        elif self.state == "armed" and self.t["armed"] and self.p["mode"] == "mission":
            window = max(0.0, fleet.START_WINDOW_S - (now - self.t["armed"]))
        ms = []
        for s, m in self.members.items():
            v = {k: x for k, x in m.v.items() if k != "token"}
            st = v["status"] or {}
            v["mode_text"] = fleet.describe_mode(st.get("main_mode"), st.get("sub_mode")) if st else None
            ms.append(v)
        return dict(id=self.rec["id"], kind="fleet", state=self.state, t=self.t, window_s=window, result=self.result,
                    profile=self.p, operator=self.rec["operator"], pilots=self.rec.get("pilots"), members=ms,
                    feed=feed, demo=self.demo, arm_pressed=self.arm_evt.is_set(), start_pressed=self.start_evt.is_set())


class App:
    def __init__(self, demo: bool):
        self.demo = demo
        self.tokens = core.Tokens()
        self.run: Run | None = None
        self.fleet: FleetRun | None = None
        self.pre: dict[int, dict] = {}
        self.lock = threading.Lock()
        self.sessions = sessions.Sessions()
        self.llm = OllamaChat()
        self.pin_fails: dict[str, list[float]] = {}

    def quick_authorise(self, kind: str, p: dict, s: dict, body: dict) -> tuple[int, dict]:
        """Session + one confirmation + the GUI's press-and-hold -> the same run token the full form issues."""
        if kind == "fleet":
            sysids = [m["sysid"] for m in p["aircraft"]]
            items = fleet.assess(p, {x: self.ctx(x) for x in sysids})
            confs = fleet.confirmations(p) if not fleet.validate(p) else []
        else:
            sysids = p["sysids"]
            items = core.assess(p, self.ctx(p["sysid"]))
            confs = core.confirmations(p) if p["sysid"] in core.AIRCRAFT else []
        blocks = [i for i in items if i["level"] == "block"]
        if blocks:
            return 403, dict(error="Blocked: " + "; ".join(i["title"] for i in blocks), blocks=blocks)
        need = {i["id"] for i in items if i["level"] == "accept"}
        h = core.profile_hash(p)
        missing = sessions.Sessions.missing_acceptance(s, h, need)
        if missing:
            return 409, dict(error="Accept this profile's risks for the session first.", need_accept=missing)
        no_pilot = sessions.Sessions.missing_pilots(s, sysids)
        if no_pilot:
            return 409, dict(error="Name a pilot for " + ", ".join(f"ARTEX{x}" for x in no_pilot) + " (Connect page, crew).",
                             need_pilots=no_pilot)
        if body.get("confirm") != "AUTHORISE":
            return 403, dict(error="Tick the confirmation and press and hold Authorise.")
        if body.get("delegate"):
            return 403, dict(error="Delegating the gates to the assistant arrives in Phase C; authorise and fly the gates yourself.")
        rec = dict(operator=s["operator"], pilots={str(x): s["pilots"][str(x)] for x in sysids}, accepted=sorted(need),
                   confirmations=[c["text"] for c in confs], phrase="quick authorisation: signed-in operator, one "
                   "confirmation, press-and-hold", aircraft_typed=", ".join(f"ARTEX{x}" for x in sysids),
                   authorised=time.strftime("%Y-%m-%d %H:%M:%S"), quick=True, delegate=False)
        return 200, dict(token=self.tokens.issue(p, s["operator"], rec), ttl_s=core.TOKEN_TTL_S)

    def busy(self) -> bool:
        return bool((self.run and self.run.active) or (self.fleet and self.fleet.active))

    # ---- version handshake: never drive a companion running different agent code -----------
    def remote_version(self, sysid: int) -> dict | None:
        host = core.AIRCRAFT[sysid]["host"]
        try:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
                                f"cd {layout.REMOTE_DIR} && python3 -m artex.common.version"],
                               capture_output=True, text=True, timeout=25)
            return json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else None
        except (subprocess.TimeoutExpired, ValueError, IndexError, OSError):
            return None

    def version_problem(self, sysids: list[int]) -> str | None:
        """None when every companion runs exactly this console's agent code. DEMO contacts nothing."""
        if self.demo:
            return None
        mine, bad = version.report(), []
        found: dict[int, dict | None] = {}
        threads = [threading.Thread(target=lambda s=s: found.__setitem__(s, self.remote_version(s))) for s in sysids]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        for s in sysids:
            r, host = found.get(s), core.AIRCRAFT[s]["host"]
            if not r:
                bad.append(f"ARTEX{s} ({host}): no answer from the app at {layout.REMOTE_DIR} (not installed, or unreachable)")
            elif r.get("fingerprint") != mine["fingerprint"]:
                bad.append(f"ARTEX{s} ({host}) runs {r.get('version')} / {r.get('fingerprint')}, this console "
                           f"{mine['version']} / {mine['fingerprint']}")
        return ("Version mismatch, nothing started: " + "; ".join(bad)
                + ". Update the companions (python3 -m artex.fleetops.provision --all) before running.") if bad else None

    def ctx(self, sysid: int) -> dict:
        p = self.pre.get(sysid)
        return dict(preflight_go=None if not p else p["go"], preflight_age_s=None if not p else time.time() - p["at"])

    def preflight(self, sysid: int) -> dict:
        ac = core.AIRCRAFT[sysid]
        if self.demo:
            rep = dict(all_go=True, aircraft=[dict(sysid=sysid, host=ac["host"], verdict="GO", blockers=[],
                       warnings=["DEMO: simulated result, nothing was contacted"],
                       ground=dict(loss_pct=0.0, avg_ms=0.4),
                       probe=dict(backpack_ping=dict(loss_pct=0.0), wifi=dict(signal_dbm=-50), relay_pids=[1],
                                  vehicle=dict(reachable=True, armed=False, battery_pct=88)))])
        else:
            try:
                r = subprocess.run([*layout.local_module("artex.fleetops.preflight"), "--fleet", ac["fleet"],
                                    "--json"], capture_output=True, text=True, timeout=170, cwd=str(ROOT))
                rep = json.loads(r.stdout[r.stdout.index("{"):])
            except (subprocess.TimeoutExpired, ValueError) as exc:
                rep = dict(all_go=False, aircraft=[dict(sysid=sysid, host=ac["host"], verdict="NO-GO",
                           blockers=[f"preflight did not complete: {type(exc).__name__}"], warnings=[])])
        go = bool(rep.get("all_go"))
        self.pre[sysid] = dict(go=go, at=time.time(), report=rep)
        return dict(go=go, at=time.time(), report=rep)

    def abort_out_of_band(self, sysid: int | None) -> dict:
        """sysid None = every aircraft (a group run)."""
        if self.demo:
            return dict(ok=True, text="DEMO: out-of-band abort flag simulated")
        r = subprocess.run([*layout.local_module("artex.fleetops.takeover"), "--fleet",
                            core.AIRCRAFT[sysid]["fleet"] if sysid else "fleet-artex-pair.yaml", "--now"],
                           capture_output=True, text=True,
                           timeout=30, cwd=str(ROOT))
        return dict(ok=r.returncode == 0, text=(r.stderr or r.stdout).strip()[-400:])


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        server_version = "gs/1"

        def log_message(self, *a):          # quiet
            pass

        def _send(self, code: int, body, ctype="application/json") -> None:
            data = body if isinstance(body, bytes) else (json.dumps(body, default=str) if ctype == "application/json"
                                                          else body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _guard(self, post: bool) -> bool:
            port = self.server.server_address[1]
            allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host", "") not in allowed_hosts:
                self._send(403, dict(error="bad Host"))
                return False
            if post:
                origin = self.headers.get("Origin")
                if origin and origin.split("://", 1)[-1] not in allowed_hosts:
                    self._send(403, dict(error="bad Origin"))
                    return False
                if self.headers.get("X-GS-Client") != "ui":
                    self._send(403, dict(error="missing X-GS-Client"))
                    return False
            return True

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        # ---- GET ----
        def do_GET(self):
            if not self._guard(False):
                return
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                return self._send(200, GUI.read_bytes(), "text/html")
            if u.path.startswith("/static/"):
                # bundled libraries (Leaflet, three.js) so the console works with no internet in the field
                f = (STATIC / u.path[len("/static/"):]).resolve()
                if STATIC not in f.parents or not f.is_file() or f.suffix not in MIME:
                    return self._send(404, dict(error="not found"))
                return self._send(200, f.read_bytes(), MIME[f.suffix])
            if u.path == "/api/state":
                return self._send(200, dict(demo=app.demo, phrase=core.PHRASE, aircraft=sorted(core.AIRCRAFT),
                                            stages=[[s, core.STAGE_NOTE[s]] for s in core.STAGES],
                                            defaults=core.DEFAULTS, height_max=core.HEIGHT_MAX_M, gate_s=GATE_S,
                                            preflight={k: dict(go=v["go"], age_s=time.time() - v["at"],
                                                               report=v["report"]) for k, v in app.pre.items()},
                                            run_active=bool(app.run and app.run.active),
                                            fleet_active=bool(app.fleet and app.fleet.active),
                                            map=dict(tiles=os.environ.get("ARTEX_TILES", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
                                                     attribution=os.environ.get("ARTEX_TILES_ATTRIBUTION",
                                                                                "&copy; OpenStreetMap contributors")),
                                            fleet=dict(modes=[[k, fleet.MODE_NOTE[k]] for k in fleet.MODES],
                                                       defaults=fleet.DEFAULTS, start_window_s=fleet.START_WINDOW_S,
                                                       ready_gate_s=fleet.READY_GATE_S),
                                            ttl_s=core.TOKEN_TTL_S))
            if u.path == "/api/profiles":
                return self._send(200, core.list_profiles())
            if u.path == "/api/run":
                if not app.run:
                    return self._send(200, dict(state="none"))
                return self._send(200, app.run.snapshot(int(q.get("since", ["0"])[0])))
            if u.path == "/api/planner/kml":
                return self._send(200, planner.list_kml())
            if u.path == "/api/planner/missions":
                return self._send(200, planner.list_missions())
            if u.path == "/api/planner/mission":
                try:
                    return self._send(200, planner.mission_view(q.get("folder", [""])[0]))
                except (ValueError, OSError, KeyError) as exc:
                    return self._send(400, dict(error=str(exc)))
            if u.path == "/api/fleet/profiles":
                return self._send(200, fleet.list_profiles())
            if u.path == "/api/fleet/plans":
                return self._send(200, fleet.list_plans())
            if u.path == "/api/fleet":
                if not app.fleet:
                    return self._send(200, dict(state="none", kind="fleet"))
                return self._send(200, app.fleet.snapshot(int(q.get("since", ["0"])[0])))
            if u.path == "/api/session":
                ses = app.sessions.get(self.headers.get("X-GS-Session"))
                return self._send(200, dict(signed_in=bool(ses), has_operators=sessions.has_operators(),
                                            operator=ses and ses["operator"], pilots=ses and ses["pilots"]))
            if u.path == "/api/assistant/status":
                return self._send(200, dict(app.llm.status(), phase="B", mode="advise only"))
            if u.path == "/api/version":
                return self._send(200, dict(version.report(), demo=app.demo, remote_dir=layout.REMOTE_DIR))
            if u.path == "/api/runs":
                return self._send(200, core.list_runs())
            if u.path.startswith("/api/runs/"):
                r = core.load_run(u.path.rsplit("/", 1)[-1])
                return self._send(200 if r else 404, dict(record=r, history=core.history_block(r)) if r
                                  else dict(error="no such run"))
            self._send(404, dict(error="not found"))

        # ---- POST ----
        def do_POST(self):
            if not self._guard(True):
                return
            u, b = urlparse(self.path), self._body()
            try:
                return self._post(u.path, b)
            except (KeyError, ValueError, TypeError) as exc:
                return self._send(400, dict(error=f"{type(exc).__name__}: {exc}"))

        def _post(self, path: str, b: dict):
            if path == "/api/profiles":
                return self._send(200, core.save_profile(b))
            if path == "/api/profiles/delete":
                return self._send(200, dict(ok=core.delete_profile(str(b.get("id", "")))))
            if path == "/api/profiles/preview":
                p = core.normalise(b)
                return self._send(200, dict(profile=p, errors=core.validate(p),
                                            session=self._session_view(p, core.assess(p, app.ctx(p["sysid"])), p["sysids"]),
                                            timeline=core.timeline(p) if not core.validate(p) else None,
                                            assessment=core.assess(p, app.ctx(p["sysid"])),
                                            confirmations=core.confirmations(p) if p["sysid"] in core.AIRCRAFT else [],
                                            argv=core.agent_argv(p, "/tmp/gs_trace.csv")
                                            if not core.validate(p) else None))
            if path == "/api/preflight":
                return self._send(200, app.preflight(int(b["sysid"])))
            if path == "/api/run/authorise":
                p = core.normalise(b["profile"])
                ok, why = core.authorise(p, app.ctx(p["sysid"]), b)
                if not ok:
                    return self._send(403, dict(error=why))
                texts = {c["id"]: c["text"] for c in core.confirmations(p)}
                rec = dict(operator=b["operator"].strip(), accepted=sorted(b["accepted"]),
                           confirmations=[texts[i] for i in sorted(b["confirmed"]) if i in texts],
                           phrase=core.PHRASE, aircraft_typed=b["aircraft"].strip().upper(),
                           authorised=time.strftime("%Y-%m-%d %H:%M:%S"))
                return self._send(200, dict(token=app.tokens.issue(p, b["operator"], rec), ttl_s=core.TOKEN_TTL_S))
            if path.startswith("/api/session"):
                return self._session(path, b)
            if path == "/api/assistant/ask":
                if not app.sessions.get(self.headers.get("X-GS-Session")):
                    return self._send(401, dict(error="Sign in to use the assistant."))
                q = str(b.get("question", "")).strip()
                if not q:
                    return self._send(400, dict(error="Ask a question."))
                return self._send(200, assistant.ask(q[:2000], b.get("context") if isinstance(b.get("context"), dict) else None,
                                                     llm=app.llm))
            if path in ("/api/run/authorise_quick", "/api/fleet/authorise_quick"):
                ses = app.sessions.get(self.headers.get("X-GS-Session"))
                if not ses:
                    return self._send(401, dict(error="Sign in first (Connect page)."))
                kind = "fleet" if path.startswith("/api/fleet") else "single"
                prof = fleet.normalise(b["profile"]) if kind == "fleet" else core.normalise(b["profile"])
                code, out = app.quick_authorise(kind, prof, ses, b)
                return self._send(code, out)
            if path.startswith("/api/fleet"):
                return self._fleet(path, b)
            if path == "/api/planner/upload":
                return self._send(200, planner.save_kml(str(b.get("name", "")), str(b.get("text", ""))))
            if path == "/api/planner/draw":
                return self._send(200, planner.save_drawing(str(b.get("name", "")), b.get("fence") or [],
                                                            b.get("ao") or [], b.get("launch")))
            if path == "/api/planner/area":
                return self._send(200, planner.read_area(str(b.get("kml", ""))))
            if path == "/api/planner/run":
                return self._send(200, planner.run_planner(str(b.get("kml", "")), str(b.get("site", "")),
                                                           int(b.get("drones", 0)), float(b.get("altitude", "nan")),
                                                           float(b.get("overlap", "nan")), str(b.get("scan", ""))))
            if path == "/api/run/start":
                p = core.normalise(b["profile"])
                tok = app.tokens.get(b.get("token"), p)
                if not tok:
                    return self._send(403, dict(error="Authorisation missing, expired or for a different profile."))
                problem = app.version_problem(p["sysids"])
                if problem:
                    return self._send(409, dict(error=problem))
                with app.lock:
                    if app.busy():
                        return self._send(409, dict(error="A run is already active. Only one agent may hold the link."))
                    rid = core.new_run_id()
                    rec = dict(id=rid, started=time.strftime("%Y-%m-%d %H:%M:%S"), mode="demo" if app.demo else "live",
                               profile=p, **tok["record"])
                    if len(p["sysids"]) > 1:
                        rec["kind"] = "group"
                        app.run = GroupRun(rec, app.demo)
                    else:
                        app.run = Run(rec, core.agent_argv(p, f"/tmp/gs_{rid}.csv"), app.demo)
                    app.run.start()
                return self._send(200, dict(id=rid))
            run = app.run
            if path == "/api/run/arm":
                if not run or run.state != "ready" or run.arm_sent:
                    return self._send(409, dict(error="The agent is not waiting at READY."))
                tok = app.tokens.get(b.get("token"), run.rec["profile"])
                if not tok or b.get("confirm") != "ARM":
                    return self._send(403, dict(error="Authorisation expired or ARM not confirmed. Authorise again."))
                run.arm_sent = True
                app.tokens.revoke(b["token"])
                run.rec["arm_requested"] = time.strftime("%Y-%m-%d %H:%M:%S")
                return self._send(200, dict(ok=run.send("ARM")))
            if path == "/api/run/word":
                w = str(b.get("word", "")).upper()
                if not run or not run.active:
                    return self._send(409, dict(error="No active run."))
                if w not in SEND_WORDS - {"ARM"} and not (app.demo and w in DEMO_WORDS):
                    return self._send(400, dict(error=f"{w} is not sent from here."))
                return self._send(200, dict(ok=run.send(w)))
            if path == "/api/run/abort_flag":
                if isinstance(run, GroupRun):
                    return self._send(200, app.abort_out_of_band(None))
                sysid = int(run.rec["profile"]["sysid"]) if run else int(b["sysid"])
                return self._send(200, app.abort_out_of_band(sysid))
            self._send(404, dict(error="not found"))

        def _session_view(self, p: dict, items: list[dict], sysids: list[int]) -> dict:
            """For the quick authorisation panel: is the session ready for THIS profile?"""
            ses = app.sessions.get(self.headers.get("X-GS-Session"))
            if not ses:
                return dict(signed_in=False)
            need = {i["id"] for i in items if i["level"] == "accept"}
            return dict(signed_in=True, operator=ses["operator"],
                        missing_accept=sessions.Sessions.missing_acceptance(ses, core.profile_hash(p), need),
                        missing_pilots=sessions.Sessions.missing_pilots(ses, sysids),
                        pilots={str(x): ses["pilots"].get(str(x)) for x in sysids})

        def _session(self, path: str, b: dict):
            tok = self.headers.get("X-GS-Session")
            ses = app.sessions.get(tok)
            if path == "/api/session/first_operator":
                if sessions.has_operators():
                    return self._send(403, dict(error="Operators exist already: sign in."))
                sessions.add_operator(str(b.get("name", "")), str(b.get("pin", "")))
                return self._send(200, dict(token=app.sessions.open(str(b["name"]).strip())))
            if path == "/api/session/signin":
                name = str(b.get("name", "")).strip().lower()
                fails = [t for t in app.pin_fails.get(name, []) if time.time() - t < 60]
                if len(fails) >= 5:
                    return self._send(429, dict(error="Too many wrong PINs: wait a minute."))
                who = sessions.check_pin(str(b.get("name", "")), str(b.get("pin", "")))
                if not who:
                    app.pin_fails[name] = fails + [time.time()]
                    return self._send(403, dict(error="Name or PIN not recognised."))
                app.pin_fails.pop(name, None)
                return self._send(200, dict(token=app.sessions.open(who)))
            if not ses:
                return self._send(401, dict(error="Sign in first."))
            if path == "/api/session/signout":
                app.sessions.close(tok)
                return self._send(200, dict(ok=True))
            if path == "/api/session/add_operator":
                sessions.add_operator(str(b.get("name", "")), str(b.get("pin", "")))
                return self._send(200, dict(ok=True))
            if path == "/api/session/crew":
                return self._send(200, dict(pilots=sessions.Sessions.set_crew(ses, b.get("pilots") or {})))
            if path == "/api/session/accept":
                kind = str(b.get("kind", "single"))
                prof = fleet.normalise(b["profile"]) if kind == "fleet" else core.normalise(b["profile"])
                items = (fleet.assess(prof, {m["sysid"]: app.ctx(m["sysid"]) for m in prof["aircraft"]}) if kind == "fleet"
                         else core.assess(prof, app.ctx(prof["sysid"])))
                ids = [i["id"] for i in items if i["level"] == "accept"]
                sessions.Sessions.accept(ses, core.profile_hash(prof), ids)
                return self._send(200, dict(accepted=ids))
            self._send(404, dict(error="not found"))

        def _fleet(self, path: str, b: dict):
            ctx = lambda p: {m["sysid"]: app.ctx(m["sysid"]) for m in p["aircraft"]}  # noqa: E731
            if path == "/api/fleet/profiles":
                return self._send(200, fleet.save_profile(b))
            if path == "/api/fleet/profiles/delete":
                return self._send(200, dict(ok=fleet.delete_profile(str(b.get("id", "")))))
            if path == "/api/fleet/preview":
                p = fleet.normalise(b)
                errs = fleet.validate(p)
                return self._send(200, dict(profile=p, errors=errs, assessment=fleet.assess(p, ctx(p)),
                                            session=self._session_view(p, fleet.assess(p, ctx(p)),
                                                                       [m["sysid"] for m in p["aircraft"]]),
                                            confirmations=fleet.confirmations(p) if not errs else [],
                                            plans={m["plan"]: fleet.plan_summary(m["plan"]) for m in p["aircraft"]
                                                   if fleet.plan_ok(m["plan"])},
                                            argv=[fleet.agent_argv(p, m, "gs<run>") for m in p["aircraft"]]
                                            if not errs else None, mode_note=fleet.MODE_NOTE.get(p["mode"])))
            if path == "/api/fleet/authorise":
                p = fleet.normalise(b["profile"])
                ok, why = fleet.authorise(p, ctx(p), b)
                if not ok:
                    return self._send(403, dict(error=why))
                texts = {c["id"]: c["text"] for c in fleet.confirmations(p)}
                pilots = {str(m["sysid"]): str((b.get("pilots") or {}).get(str(m["sysid"]), "")).strip()
                          for m in p["aircraft"]}
                rec = dict(operator=b["operator"].strip(), pilots=pilots, accepted=sorted(b["accepted"]),
                           confirmations=[texts[i] for i in sorted(b["confirmed"]) if i in texts],
                           phrase=core.PHRASE, aircraft_typed=str(b["aircraft"]).strip().upper(),
                           authorised=time.strftime("%Y-%m-%d %H:%M:%S"))
                return self._send(200, dict(token=app.tokens.issue(p, b["operator"], rec), ttl_s=core.TOKEN_TTL_S))
            if path == "/api/fleet/start":
                p = fleet.normalise(b["profile"])
                tok = app.tokens.get(b.get("token"), p)
                if not tok:
                    return self._send(403, dict(error="Authorisation missing, expired or for a different fleet profile."))
                problem = app.version_problem([m["sysid"] for m in p["aircraft"]])
                if problem:
                    return self._send(409, dict(error=problem))
                with app.lock:
                    if app.busy():
                        return self._send(409, dict(error="A run is already active. Only one agent may hold each link."))
                    rid = core.new_run_id()
                    rec = dict(id=rid, kind="fleet", started=time.strftime("%Y-%m-%d %H:%M:%S"),
                               mode="demo" if app.demo else "live", profile=p, **tok["record"])
                    app.fleet = FleetRun(rec, app.demo)
                    app.fleet.start()
                return self._send(200, dict(id=rid))
            fr = app.fleet
            if not fr or not fr.active:
                return self._send(409, dict(error="No active fleet run."))
            if path in ("/api/fleet/arm_all", "/api/fleet/start_all"):
                arm = path.endswith("arm_all")
                if (arm and fr.state != "ready") or (not arm and (fr.state != "armed" or fr.p["mode"] != "mission")):
                    return self._send(409, dict(error="Not at that gate." if arm else "Not every aircraft is armed, "
                                                "or this is not a mission run."))
                tok = app.tokens.get(b.get("token"), fr.p)
                if not tok or b.get("confirm") != ("ARM ALL" if arm else "START ALL"):
                    return self._send(403, dict(error="Authorisation expired or not confirmed. Authorise again."))
                (fr.arm_evt if arm else fr.start_evt).set()
                fr.rec["arm_all_pressed" if arm else "start_all_pressed"] = time.strftime("%Y-%m-%d %H:%M:%S")
                if not arm:
                    app.tokens.revoke(b["token"])
                return self._send(200, dict(ok=True))
            if path == "/api/fleet/word":
                w = str(b.get("word", "")).upper()
                return self._send(200, dict(sent=fr.word(w, str(b.get("target", "all")))))
            if path == "/api/fleet/cancel":
                fr.cancel_evt.set()
                fr.note("Operator: CANCEL / end watch", bad=True)
                return self._send(200, dict(ok=True))
            if path == "/api/fleet/abort_flag":
                if app.demo:
                    return self._send(200, dict(ok=True, text="DEMO: out-of-band abort flag simulated on every aircraft"))
                r = subprocess.run([*layout.local_module("artex.fleetops.takeover"), "--fleet",
                                    "fleet-artex-pair.yaml", "--now"], capture_output=True, text=True,
                                   timeout=40, cwd=str(ROOT))
                return self._send(200, dict(ok=r.returncode == 0, text=(r.stderr or r.stdout).strip()[-400:]))
            self._send(404, dict(error="not found"))

    return H


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="simulated aircraft; contacts nothing")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    app = App(a.demo)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(app))
    print(f"[gs] {'DEMO (simulated aircraft)' if a.demo else 'LIVE (real aircraft over ssh)'} -- "
          f"http://127.0.0.1:{a.port}", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        if app.run and app.run.active:
            print("[gs] a run is still active; its agent keeps going on the companion.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

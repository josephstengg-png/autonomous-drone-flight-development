"""Phase C: what the assistant may DO, and only inside one run the operator authorised AND delegated.

Design rule: the model decides; code does the timing. The assistant never presses ARM ALL / START ALL itself, step by
step (a local 4B model needs seconds per turn, and START ALL must land within 8 s of ALL ARMED). Instead it may ask
for `execute_authorised_run`, and a deterministic sequencer here presses each gate when the console says it is due,
through exactly the methods the operator's buttons use (App.start_run / arm_run / start_fleet / fleet_gate), so every
precondition, BLOCK and refusal is the same. Stopping (HOLD / RTL / LAND) needs no delegation: it is the safe way.

Live aircraft: delegation is refused unless the site sets ARTEX_ASSISTANT_LIVE=enabled, which should happen only after
`python -m artex.assistant.evals` passes and the operator decides to allow it. DEMO always allows it.
"""
from __future__ import annotations

import os
import threading
import time

LIVE_SWITCH = "ARTEX_ASSISTANT_LIVE"


def live_allowed() -> bool:
    return os.environ.get(LIVE_SWITCH, "") == "enabled"


def _log(app, text: str, bad: bool = False) -> None:
    fr, run = app.fleet, app.run
    target = fr if fr and fr.active else run if run and run.active else None
    if target is None:
        return
    target.rec.setdefault("assistant_actions", []).append(dict(t=time.strftime("%H:%M:%S"), text=text))
    if hasattr(target, "note"):
        target.note(f"ASSISTANT: {text}", bad=bad)
    elif hasattr(target, "_note"):
        target._note(f"ASSISTANT: {text}", bad=bad)
    else:
        target._push("log", text=f"ASSISTANT: {text}")


def delegation_problem(app, token: str | None) -> str | None:
    d = app.delegations.get(token or "")
    if not d:
        return "no delegated authorisation: the operator must authorise with 'delegate the gates to the assistant'"
    if not app.tokens.get(token, d["profile"]):
        return "the delegated authorisation has expired, was used up or the profile changed: authorise again"
    if not app.demo and not live_allowed():
        return f"delegation to the assistant is switched off for real aircraft ({LIVE_SWITCH} is not 'enabled')"
    return None


def _wait(cond, timeout_s: float, step: float = 0.2) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def _sequence_fleet(app, token: str) -> None:
    fr = app.fleet
    if fr.p["mode"] == "upload":
        return                      # upload and verify: no gates, nothing to press
    if not _wait(lambda: fr.state in ("ready", "not_ready", "done", "ended"), 300):
        return _log(app, "gave up waiting for READY", bad=True)
    if fr.state != "ready":
        return _log(app, f"not every aircraft READY ({fr.state}): nothing pressed", bad=True)
    code, out = app.fleet_gate("arm", token, "ARM ALL")
    _log(app, "pressed ARM ALL" if code == 200 else f"ARM ALL refused: {out.get('error')}", bad=code != 200)
    if code != 200 or fr.p["mode"] != "mission":
        return
    if not _wait(lambda: fr.state in ("armed", "disarming", "done", "ended"), 60):
        return _log(app, "gave up waiting for ALL ARMED", bad=True)
    if fr.state != "armed":
        return _log(app, f"not every aircraft armed ({fr.state}): START ALL not pressed", bad=True)
    code, out = app.fleet_gate("start", token, "START ALL")
    _log(app, "pressed START ALL" if code == 200 else f"START ALL refused: {out.get('error')}", bad=code != 200)


def _sequence_run(app, token: str) -> None:
    run = app.run
    if not _wait(lambda: run.state in ("ready", "not_ready", "done", "ended"), 200):
        return _log(app, "gave up waiting for READY", bad=True)
    if run.state != "ready":
        return _log(app, f"not READY ({run.state}): nothing pressed", bad=True)
    code, out = app.arm_run(token, "ARM")
    _log(app, "pressed ARM" + (" ALL" if hasattr(run, "members") else "") if code == 200
         else f"ARM refused: {out.get('error')}", bad=code != 200)


def execute_authorised_run(app, token: str | None) -> dict:
    why = delegation_problem(app, token)
    if why:
        return dict(error=why)
    d = app.delegations[token]
    code, out = (app.start_fleet if d["kind"] == "fleet" else app.start_run)(d["profile"], token)
    if code != 200:
        return dict(error=out.get("error"), status=code)
    _log(app, f"started the authorised {d['kind']} run for {d['operator']} (delegated); gates follow when due")
    threading.Thread(target=_sequence_fleet if d["kind"] == "fleet" else _sequence_run, args=(app, token), daemon=True).start()
    return dict(started=out["id"], kind=d["kind"],
                note="Running. The console presses each gate only when it is due; the pilots' handsets stay the override.")


def stop_aircraft(app, action: str, target: str = "all") -> dict:
    action = (action or "").lower()
    if action not in ("hold", "rtl", "land"):
        return dict(error="action must be hold, rtl or land")
    fr, run = app.fleet, app.run
    if fr and fr.active:
        sent = fr.word(action.upper(), str(target))
        _log(app, f"{action.upper()} sent to " + (", ".join(f"ARTEX{s}" for s in sent) or "nobody"), bad=True)
        return dict(sent=sent, action=action)
    if run and run.active:
        word = {"hold": "HOLD", "rtl": "ABORT", "land": "LAND"}[action]
        ok = run.send(word)
        _log(app, f"{word} sent to the run's agents", bad=True)
        return dict(sent=ok, action=action, note="single/group runs: ABORT = land here (no RTL without a position)")
    return dict(error="no active run to stop")


def run_status(app) -> dict:
    fr, run = app.fleet, app.run
    if fr and (fr.active or fr.result):
        s = fr.snapshot(len(fr.feed))
        return dict(kind="fleet", id=s["id"], state=s["state"], result=s["result"] and s["result"].get("outcome"),
                    aircraft=[dict(sysid=m["sysid"], ready=m["ready"], armed=m["armed"], started=m["started"], held=m["held"],
                                   fault=m["fault"], mode=m["mode_text"]) for m in s["members"]])
    if run:
        s = run.snapshot(len(run.feed))
        return dict(kind="group" if s.get("group") else "single", id=s["id"], state=s["state"],
                    result=(s["result"] or {}).get("outcome") if s["result"] else None,
                    aircraft=s.get("members") or [dict(sysid=s["profile"]["sysid"], stage=s.get("stage"))])
    return dict(state="no run")


def tools_for(app, token: str | None):
    """Phase C tools bound to this console. kinds: act (needs delegation), stop (always), read."""
    from artex.assistant.tools import _fn
    S = dict(type="string")
    return {
        "execute_authorised_run": ("act", lambda: execute_authorised_run(app, token), _fn(
            "execute_authorised_run", "Start the run the operator authorised AND delegated to you, and let the console press "
            "each gate when it is due. Refused without a valid delegation.", {})),
        "stop_aircraft": ("stop", lambda action, target="all": stop_aircraft(app, action, target), _fn(
            "stop_aircraft", "Stop the active run's aircraft: hold (loiter), rtl (return to launch) or land. target = all or a sysid.",
            dict(action=dict(type="string", enum=["hold", "rtl", "land"]), target=S), ["action"])),
        "run_status": ("read", lambda: run_status(app), _fn("run_status", "The active or last run: state and each aircraft.", {})),
    }

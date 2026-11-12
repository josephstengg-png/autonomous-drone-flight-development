"""The read-back comparison decides whether an upload counts as verified."""

import copy
import sys
from pathlib import Path


from artex.agent import drone_agent as agent  # noqa: E402


def _items():
    return [
        dict(seq=0, frame=3, command=22, p1=0.0, p2=0.0, p3=0.0, p4=0.0,
             x=14172000, y=1038636000, z=30.0),
        dict(seq=1, frame=3, command=16, p1=0.0, p2=4.83, p3=0.0, p4=0.0,
             x=14173000, y=1038637000, z=30.0),
        dict(seq=2, frame=2, command=20, p1=0.0, p2=0.0, p3=0.0, p4=0.0,
             x=0, y=0, z=0.0),
    ]


def test_identical_mission_has_no_differences():
    assert agent.diff_items(_items(), copy.deepcopy(_items())) == []


def test_px4_frame_renumbering_is_not_a_difference():
    # PX4 stores the _INT frames: local-relative 3 reads back as 6, and 0 as 5.
    got = copy.deepcopy(_items())
    got[0]["frame"], got[1]["frame"] = 6, 6
    assert agent.diff_items(_items(), got) == []


def test_real_differences_are_named_by_item():
    got = copy.deepcopy(_items())
    got[1]["z"] += 2.0
    got[1]["x"] += 1
    got[2]["command"] = 17
    assert agent.diff_items(_items(), got) == [
        "item 1: position/z differ",
        "item 2: command differ",
    ]


def test_missing_items_are_reported():
    assert agent.diff_items(_items(), _items()[:2]) == ["2 items on board, 3 sent"]


def test_onboard_count_survives_one_lost_reply():
    """12 Nov 2026 (ELRS emulator): one lost MISSION_COUNT refused READY."""
    from types import SimpleNamespace
    from artex.agent.drone_agent import mission_count_onboard
    sent = []
    replies = iter([None, SimpleNamespace(count=11)])

    class Mav:
        def mission_request_list_send(self, *a):
            sent.append(a)

        def mission_ack_send(self, *a):
            pass

    conn = SimpleNamespace(target_system=1, mav=Mav(), recv_match=lambda **k: next(replies))
    assert mission_count_onboard(conn) == 11 and len(sent) == 2


class _Link:
    """Heartbeats in a given PX4 mode; DO_SET_MODE (176) changes it when `obeys`."""
    def __init__(self, main, sub, obeys=True, armed_after_arm=True):
        from types import SimpleNamespace
        self.mode, self.obeys, self.sent, self.armed = (main, sub), obeys, [], False
        self.target_system, self.armed_after_arm = 1, armed_after_arm
        link = self

        class Mav:
            def command_long_send(self, tgt, comp, cmd, conf, *p):
                link.sent.append(cmd)
                if cmd == 176 and link.obeys:
                    link.mode = (int(p[1]), int(p[2]))
                if cmd == 400 and p[0] == 1:
                    link.armed = link.armed_after_arm
        self.mav = Mav()
        self.NS = SimpleNamespace

    def recv_match(self, type=None, blocking=False, timeout=None):
        if type == "HEARTBEAT":
            return self.NS(get_srcSystem=lambda: 1, get_srcComponent=lambda: 1,
                           custom_mode=(self.mode[0] << 16) | (self.mode[1] << 24), base_mode=128 if self.armed else 0)
        return None


def test_arming_puts_a_mission_mode_aircraft_in_hold_first():
    """12 Nov 2026 (SITL): left in AUTO.MISSION, an aircraft took off at ARM ALL."""
    from artex.agent import drone_agent as a
    link = _Link(4, 4)                              # AUTO.MISSION
    r = a.do_arm(link)
    assert r["armed"] and link.mode == (4, 3) and link.sent.index(176) < link.sent.index(400)


def test_arming_is_refused_when_hold_cannot_be_set():
    from artex.agent import drone_agent as a
    link = _Link(6, 0, obeys=False)                 # stuck in OFFBOARD
    r = a.do_arm(link)
    assert not r["armed"] and "Hold" in r["error"] and 400 not in link.sent

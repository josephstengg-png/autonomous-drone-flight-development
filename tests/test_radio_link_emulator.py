"""The ELRS link emulator reproduces the measured link (history.md, Exp 1 4 Nov, Exp 4 5 Nov, ulogs 5 Nov)."""
import random
import statistics

from pymavlink.dialects.v20 import common as mav

from artex.diagnostics import radio_link_emulator as rl


def _frame(msg) -> bytes:
    m = mav.MAVLink(None, srcSystem=1, srcComponent=1)
    return msg.pack(m)


def test_frames_splits_a_packed_datagram_by_message_id():
    hb = _frame(mav.MAVLink_heartbeat_message(2, 12, 0, 0, 3, 3))
    att = _frame(mav.MAVLink_attitude_message(1, 0, 0, 0, 0, 0, 0))
    got = list(rl.frames(hb + att))
    assert [m for m, _ in got] == [0, 30] and b"".join(f for _, f in got) == hb + att


def test_delay_model_spans_experiment_4():
    """Medians fitted so the live round trip (with serialisation) matches Exp 4 -- checked end to end by
    validate_link (docs/simulation.md); here: monotone in load, jitter widths from the measured p90/median."""
    rng = random.Random(4)
    lean = [rl.one_way_delay(0.10, rng) for _ in range(4000)]
    busy = [rl.one_way_delay(0.86, rng) for _ in range(4000)]
    assert abs(statistics.median(lean) - rl.LEAN_ONE_WAY_S) < 0.01 and abs(statistics.median(busy) - rl.BUSY_ONE_WAY_S) < 0.02
    p90 = lambda xs: sorted(xs)[int(0.9 * len(xs))]       # noqa: E731
    assert 1.1 < p90(lean) / statistics.median(lean) < 1.3 and 1.35 < p90(busy) / statistics.median(busy) < 1.6


def test_a_20_hz_stream_chokes_the_link_and_3_hz_does_not():
    """Exp 1: 20 Hz buried the commands; 3 Hz got through."""
    for hz, choked in ((20, True), (3, False)):
        d = rl.Direction("up", random.Random(1), stalls=False, capacity=rl.UPLINK_BPS)
        t, lost = 1000.0, 0
        for i in range(int(hz * 30)):
            now = t + i / hz
            nbytes = 63 + (21 if i % hz == 0 else 0)          # SET_POSITION_TARGET_LOCAL_NED + a heartbeat a second
            if d.schedule(now, nbytes, 0.1) is None:
                lost += 1
        assert (d.stats["dropped_queue"] > 0) is choked, (hz, d.stats)


def test_stalls_hold_packets_and_release_them_together():
    d = rl.Direction("up", random.Random(2))
    d.stall_until = 101.0                                   # a stall in progress until t=101
    arrivals = [d.schedule(100.0 + i * 0.33, 60, 0.1, 0.86, 1.0) for i in range(3)]
    held = [x for x in arrivals if x is not None]
    assert held and all(x > 101.0 for x in held) and max(held) - min(held) < 0.5   # late, together, not lost
    assert d.stats["dropped_queue"] == 0


def test_no_setpoint_stream_no_stalls():
    """Exp 4, default streams, 0 Hz: p90 0.87 s, nothing lost. With a 3 Hz stream on a busy downlink they come back."""
    d = rl.Direction("up", random.Random(3))
    for i in range(600):
        d.schedule(1000.0 + i, 40, 0.1, 0.86, 0.0)
    assert d.stats["stalls"] == 0
    d2 = rl.Direction("up", random.Random(3))
    for i in range(1800):
        d2.schedule(1000.0 + i / 3, 63, 0.1, 0.86, 1.0)      # 10 min of 3 Hz on a busy downlink
    assert 80 < d2.stats["stalls"] < 250                    # ~ one every 3.7 s plus the stall itself


def test_emulated_radio_flag_restores_the_radio_defaults():
    from artex.agent.drone_agent import arm_retry_s, default_setpoint_hz, ARM_RETRY_RADIO_S, RADIO_SETPOINT_HZ
    assert default_setpoint_hz("udpout://127.0.0.1:24540", False, None) == RADIO_SETPOINT_HZ
    assert arm_retry_s("udpout://127.0.0.1:24540", False) == ARM_RETRY_RADIO_S


def test_stalls_follow_the_downlink_load_as_measured():
    lean, busy = rl.stall_model(0.10), rl.stall_model(0.86)
    assert abs(lean[0] - rl.LEAN_STALL_EVERY_S) < 1e-9 and abs(busy[0] - rl.BUSY_STALL_EVERY_S) < 1e-9
    assert busy[1:] == rl.BUSY_STALL_S and lean[1:] == rl.LEAN_STALL_S
    assert lean[0] > rl.stall_model(0.4)[0] > busy[0]


def test_set_message_interval_requests_are_recognised():
    m = mav.MAVLink(None, srcSystem=250, srcComponent=190)
    fr = mav.MAVLink_command_long_message(1, 1, 511, 0, 32, 200000, 0, 0, 0, 0, 0).pack(m)
    assert rl.requested_interval(fr) == (32, 200000.0)
    off = mav.MAVLink_command_long_message(1, 1, 511, 0, 74, -1, 0, 0, 0, 0, 0).pack(m)
    assert rl.requested_interval(off) == (74, -1.0)
    other = mav.MAVLink_command_long_message(1, 1, 400, 0, 1, 0, 0, 0, 0, 0, 0).pack(m)
    assert rl.requested_interval(other) is None


def test_raised_rates_are_honoured_as_measured_on_4_nov():
    assert abs(rl.honoured_rate(32, 1e6 / 10) - 3.0) < 1e-9          # 10 Hz asked -> 3.0 Hz (measured)
    assert abs(rl.honoured_rate(32, 1e6 / 5) - 1.5) < 0.2            # 5 Hz asked -> 1.64 Hz (measured)
    assert rl.honoured_rate(30, 1e6 / 3) == 3.0                       # lowered: as asked
    assert rl.honoured_rate(74, -1) == 0.0 and rl.honoured_rate(74, 0) == rl.STREAM_HZ[74]

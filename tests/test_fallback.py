"""The laptop -> jetson-2 -> jetson-1 fallback: stop-only, deterministic, never clears what it set."""
from artex.fleetops import fallback as fb


def test_it_acts_once_when_every_superior_is_gone_long_enough_and_an_agent_runs():
    st = {}
    assert fb.decide(st, superiors_ok=False, active=True, now=100, timeout_s=15) is None       # just lost
    assert fb.decide(st, False, True, 110, 15) is None
    assert fb.decide(st, False, True, 115, 15) == "act"
    assert fb.decide(st, False, True, 130, 15) is None                                          # once only


def test_no_agent_running_means_nothing_to_stop():
    st = {}
    for t in (0, 20, 60):
        assert fb.decide(st, False, active=False, now=t, timeout_s=15) is None


def test_a_brief_blip_does_not_act_and_a_return_after_acting_is_reported_once():
    st = {}
    fb.decide(st, False, True, 0, 15)
    assert fb.decide(st, True, True, 10, 15) is None                                            # back before the timeout
    assert fb.decide(st, False, True, 20, 15) is None and st["lost_since"] == 20                 # the clock restarts
    assert fb.decide(st, False, True, 36, 15) == "act"
    assert fb.decide(st, True, True, 40, 15) == "back" and fb.decide(st, True, True, 42, 15) is None


def test_the_flag_is_written_locally_and_never_cleared_by_the_watchdog(tmp_path):
    assert fb.write_flag("hold", [], tmp_path) == ["local"]
    assert (tmp_path / "ABORT").read_text().strip() == "hold"
    st = {"acted": True}
    fb.decide(st, True, False, 0, 15)
    assert (tmp_path / "ABORT").exists()                       # recovery is the operator's decision


def test_agents_are_found_in_proc_but_not_the_watchdog_itself(tmp_path):
    for pid, cmd in ((101, b"python3\x00-m\x00artex.agent.drone_agent\x00prepare"), (102, b"bash\x00-c\x00ls"),
                     (103, b"python3\x00-m\x00artex.fleetops.fallback\x00--probe\x00artex.agent.drone_agent")):
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "cmdline").write_bytes(cmd)
    assert fb.agents_running(tmp_path) == 1

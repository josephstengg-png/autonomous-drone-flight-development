"""Simulated aircraft (PX4 SITL on this machine, 12 Nov 2026): run locally, skip only the radio chain, prove it is a sim."""
import sys

from artex.common import layout
from artex.console import gs_core as core
from artex.console import gs_fleet as fleet
from artex.fleetops import preflight
from artex.fleetops.swarm_deploy import host_argv

SIM_DRONE = dict(slot=1, sysid=1, host="local", link="udpin://:14540", sim=True)
GOOD_VEHICLE = dict(reachable=True, sysid=1, armed=False, simulator=True, home_set=True,
                    gps=dict(position_usable=True, gate_failures=[]))


def test_local_agent_runs_with_this_interpreter_in_the_data_folder():
    cmd, cwd = host_argv(dict(host="local"), [*layout.AGENT, "status"])
    assert cmd[0] == sys.executable and cmd[1:3] == ["-m", "artex.agent.drone_agent"]
    assert cwd == str(layout.DATA)


def test_remote_agent_still_goes_over_ssh():
    cmd, cwd = host_argv(dict(host="jetson-2"), [*layout.AGENT, "status"])
    assert cmd[0] == "ssh" and cwd is None and "python3 -m artex.agent.drone_agent status" in cmd[-1]


def test_sim_preflight_skips_the_radio_chain_only():
    bad, warn = preflight.assess(SIM_DRONE, {"loss_pct": 0.0, "avg_ms": 0.0}, dict(simulated=True, vehicle=GOOD_VEHICLE))
    assert bad == []


def test_sim_preflight_refuses_a_real_aircraft_marked_sim():
    v = dict(GOOD_VEHICLE, simulator=False)
    bad, _ = preflight.assess(SIM_DRONE, {"loss_pct": 0.0, "avg_ms": 0.0}, dict(simulated=True, vehicle=v))
    assert any("REAL aircraft" in b for b in bad)


def test_sim_preflight_keeps_the_vehicle_checks():
    for v, word in ((dict(GOOD_VEHICLE, armed=True), "ARMED"), (dict(GOOD_VEHICLE, sysid=2), "WRONG AIRCRAFT"),
                    (dict(reachable=False, error="timeout"), "heartbeat")):
        bad, _ = preflight.assess(SIM_DRONE, {"loss_pct": 0.0, "avg_ms": 0.0}, dict(simulated=True, vehicle=v))
        assert any(word in b for b in bad), (v, bad)


def test_real_drone_without_radio_chain_is_still_no_go():
    real = dict(SIM_DRONE, sim=False, host="jetson-2")
    bad, _ = preflight.assess(real, {"loss_pct": 0.0, "avg_ms": 1.0}, dict(vehicle=GOOD_VEHICLE))
    assert any("backpack" in b for b in bad) and any("relay" in b for b in bad)


def test_two_simulated_aircraft_may_share_this_machine(monkeypatch):
    monkeypatch.setitem(core.AIRCRAFT, 1, dict(host="local", radio="udpin://:14540", usb="udpin://:14540", hover=0.42))
    monkeypatch.setitem(core.AIRCRAFT, 2, dict(host="local", radio="udpin://:14541", usb="udpin://:14541", hover=0.42))
    p = fleet.normalise(dict(name="sim pair", aircraft=[dict(sysid=1, plan="missions/x/mission_drones/drone-1.plan"),
                                                        dict(sysid=2, plan="missions/x/mission_drones/drone-2.plan")]))
    assert not any("companion" in e for e in fleet.validate(p))
    q = core.normalise(dict(name="sim bench", sysids=[1, 2], flight_type="bench"))
    assert not any("companion" in e for e in core.validate(q))


def test_agent_command_adds_ssh_options_only_for_companions():
    from artex.console.gs_server import agent_command
    cmd, cwd = agent_command("local", [*layout.AGENT, "status"])
    assert "-o" not in cmd and cmd[0] == sys.executable and cwd == str(layout.DATA)
    cmd, _ = agent_command("jetson-1", [*layout.AGENT, "status"])
    assert cmd[:3] == ["ssh", "-o", "ConnectTimeout=8"]

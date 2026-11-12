"""The 'mission' downlink profile for outdoor waypoint runs, and the link sweep's arithmetic."""
from artex.agent import drone_agent as a
from artex.console import gs_fleet as fleet
from artex.diagnostics import link_sweep


def test_mission_keeps_position_and_what_the_console_reads_and_fits_the_radio():
    cmds = dict(a.downlink_commands("mission"))
    assert cmds[33] == 500000                              # GLOBAL_POSITION_INT at 2 Hz: the map and the STATUS position
    for needed in (1, 24, 30, 42, 65, 147, 230, 242, 245):   # battery, gps, attitude, mission progress, rc, estimator, home, landed
        assert cmds.get(needed, 0) > 0, needed
    assert all(cmds[m] == -1 for m in (31, 36, 265, 285, 12901))
    b = a.downlink_budget("mission")
    assert b["share_of_capacity"] < 0.3 and b["bytes_per_s"] < 400
    assert a.downlink_commands("outdoors") == []


def test_every_id_any_profile_touches_is_restored():
    touched = {m for p in ("indoors", "mission") for m, _ in a.downlink_commands(p)}
    restored = {m for m, us in a.downlink_restore_commands() if us == 0}
    assert touched == restored


def test_a_fleet_profile_can_ask_its_agents_for_the_mission_downlink():
    p = fleet.normalise(dict(name="Sample pair", mode="mission", flight_type="props_on", downlink="mission",
                             aircraft=[dict(sysid=113, plan="missions/sample/mission_drones/drone-2.plan"),
                                       dict(sysid=115, plan="missions/sample/mission_drones/drone-1.plan")]))
    assert fleet.validate(p) == []
    argv = fleet.agent_argv(p, p["aircraft"][0], "gsX")
    assert argv[argv.index("--downlink") + 1] == "mission"
    assert "--downlink" not in fleet.agent_argv(dict(p, downlink="outdoors"), p["aircraft"][0], "gsX")
    assert fleet.validate(dict(p, downlink="indoors"))


def test_the_sweep_summary_counts_lost_commands_as_the_timeout():
    rows = [dict(profile="mission", rtt=0.3), dict(profile="mission", rtt=None), dict(profile="outdoors", rtt=0.9)]
    s = link_sweep.summarise(rows)
    assert s["mission"]["lost"] == 1 and s["mission"]["worst_s"] == link_sweep.TIMEOUT_S and s["outdoors"]["n"] == 1
    assert [x["profile"] for x in link_sweep.plan()] == ["outdoors", "mission", "indoors"]

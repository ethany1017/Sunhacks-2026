"""S2 Referee: each fail rule is forced and caught, one violation is one record, the tower passes the smoke day."""
from __future__ import annotations

from typing import Any, Dict, List

from sim.core.aircraft_types import get_type
from sim.core.engine import Simulation
from sim.core.entities import Aircraft
from sim.core.physics import unit
from sim.referee import limits as L
from sim.referee.referee import air_loss
from tests.conftest import SMOKE, NoopController


def steps(sim: Simulation, n: int) -> None:
    for _ in range(n):
        sim.step()


def fails(sim: Simulation, rule: str) -> List[Dict[str, Any]]:
    return [f for f in sim.referee.fails if f["rule"] == rule]


def _taxiing(sim: Simulation, graph, name: str, route: List[str], s: float = 0.0) -> Aircraft:
    ac = Aircraft(callsign=name, type=get_type("B738"), kind="departure", airline="TST", seq=500 + len(sim.aircraft),
                  phase="taxi_out")
    ac.path = graph.build_path(route)
    ac.s = s
    xy = ac.path.xy_at(s)
    ac.x, ac.y, ac.heading = float(xy[0]), float(xy[1]), ac.path.heading_at(s)
    sim.aircraft[name] = ac
    return ac


def _on_final(sim: Simulation, name: str, icao: str, end: str, dist_m: float) -> Aircraft:
    rw, info, _ = sim.graph.end_info(end)
    pos = sim.graph.node_xy[info["node"]] - unit(info["heading_deg"]) * dist_m
    ac = Aircraft(callsign=name, type=get_type(icao), kind="arrival", airline="TST", seq=600 + len(sim.aircraft), gate="B20",
                  runway=end, phase="final", scheduled_s=1e6)
    ac.x, ac.y, ac.heading = float(pos[0]), float(pos[1]), info["heading_deg"]
    ac.speed, ac.alt_m = ac.type.vref_mps, dist_m * 0.0524
    sim.aircraft[name] = ac
    return ac


def _crossing_route(graph) -> List[str]:
    return graph.route(graph.stands["B12"].lane_node, graph.entries("25L")[0][0])     # crosses 07L/25R, then 07R/25L


# -- PASS ---------------------------------------------------------------------------------------------------------------
def test_tower_passes_the_smoke_day(tmp_path, graph) -> None:
    sim = Simulation(SMOKE, run_dir=tmp_path / "smoke", graph=graph)
    assert sim.referee_report()["verdict"] == "RUNNING"
    sim.run_to_completion()
    sim.close()
    rep = sim.referee_report()
    assert rep["verdict"] == "PASS" and rep["fails"] == [], rep["fails"]
    results = [e for e in sim.log.events if e["kind"] == "referee_result"]
    assert len(results) == 1 and results[0]["data"]["verdict"] == "PASS"
    assert set(results[0]["data"]) == {"verdict", "fails", "score"}
    score = rep["score"]
    assert score["fails_by_rule"] == {r: 0 for r in score["fails_by_rule"]} and score["completed"] == 6
    assert {"total_delay_s", "avg_delay_s", "go_arounds", "diversions", "runway_operations", "throughput_per_10min",
            "readback_errors_caught", "readback_errors_accepted"} <= set(score)


# -- 1. collision --------------------------------------------------------------------------------------------------------
def test_overlapping_aircraft_collide_and_it_is_one_record(manual_sim: Simulation, graph) -> None:
    sim = manual_sim
    route = _crossing_route(graph)
    a = _taxiing(sim, graph, "TSTA", route, s=40.0)
    b = _taxiing(sim, graph, "TSTB", route, s=60.0)           # 20 m apart: a B738 is 39.5 m long
    a.hold_s, b.hold_s = 40.0, 60.0                          # both stay put
    steps(sim, 50)
    got = fails(sim, "collision")
    assert len(got) == 1 and got[0]["callsigns"] == ["TSTA", "TSTB"] and "collided on the ground" in got[0]["reason"]
    assert sim.referee_report()["verdict"] == "FAIL"
    assert len([e for e in sim.log.events if e["kind"] == "referee_fail" and e["data"]["rule"] == "collision"]) == 1


# -- 2. loss of separation -----------------------------------------------------------------------------------------------
def test_in_trail_arrivals_below_3nm_lose_separation(manual_sim: Simulation) -> None:
    sim = manual_sim
    lead = sim.aircraft["SKW722"]                            # on final 26
    a = sim.physics.approach_distance(lead)
    _on_final(sim, "TSTF", "B738", "26", a + 1.0 * L.NM)
    steps(sim, 2)
    got = fails(sim, "loss_of_separation")
    assert len(got) == 1 and set(got[0]["callsigns"]) == {"SKW722", "TSTF"} and "5-5-4a" in got[0]["reason"]


def test_wake_table_sets_the_minimum_behind_a_heavy(manual_sim: Simulation) -> None:
    sim = manual_sim
    heavy = _on_final(sim, "TSTH", "B772", "25L", 3000.0)
    light = _on_final(sim, "TSTL", "C172", "25L", 3000.0 + 5.0 * L.NM)
    light.alt_m = heavy.alt_m
    d, minimum, std = air_loss(sim, heavy, light)              # B772 is CWT B, C172 is I: 6 NM
    assert abs(minimum - 6.0 * L.NM) < 1.0 and "CWT I behind B" in std
    light.alt_m = heavy.alt_m + L.VERTICAL_MIN_M + 1.0        # 1000 ft vertical separation replaces it
    assert air_loss(sim, heavy, light) is None


# -- 3. two aircraft on one runway ---------------------------------------------------------------------------------------
def test_landing_on_an_occupied_runway_fails_once(manual_sim: Simulation) -> None:
    sim = manual_sim
    sim.res.enter("08/26", "OBSTACLE", sim.t)
    assert sim.issue_clearance("SKW722", "cleared_to_land", {"runway": "26"})["ok"]
    for _ in range(3000):
        sim.step()
        if sim.aircraft["SKW722"].touchdown_t is not None:
            break
    steps(sim, 200)                                           # the violation goes on for the whole landing roll
    got = fails(sim, "runway_occupied")
    assert len(got) == 1, got
    assert got[0]["callsigns"] == ["SKW722", "OBSTACLE"]
    assert got[0]["reason"] == "SKW722 landed on runway 08/26 while OBSTACLE was still on it"
    assert not fails(sim, "runway_incursion")                 # it was cleared to land


# -- 4. runway incursion -------------------------------------------------------------------------------------------------
def test_accepted_wrong_hold_short_readback_is_an_incursion(manual_sim: Simulation, graph) -> None:
    sim = manual_sim
    ac = _taxiing(sim, graph, "TST1", _crossing_route(graph))
    assert ac.path.runs[0].runway == "07L/25R" and not ac.cleared_runs
    sim.scenario.readback_error_rate = 1.0
    c = sim.issue_clearance("TST1", "hold_short", {"runway": "25R"})["clearance_id"]
    steps(sim, 31)
    sim.scenario.readback_error_rate = 0.0
    rb = next(p for p in sim.observe()["pending_readbacks"] if p["clearance_id"] == c)
    seen = sim.observe()["referee"]["score"]                  # nothing says the pending readback is wrong
    assert seen["readback_errors_accepted"] == 0 and seen["readback_errors_caught"] == 0
    sim.resolve_readback(rb["id"], True)                      # the controller misses it: the pilot misses the hold
    for _ in range(3000):
        sim.step()
        if fails(sim, "runway_incursion"):
            break
    got = fails(sim, "runway_incursion")
    assert len(got) == 1 and got[0]["callsigns"] == ["TST1"] and "07L/25R" in got[0]["reason"]
    assert sim.referee_report()["score"]["readback_errors_accepted"] == 1


def test_a_cleared_crossing_is_not_an_incursion(manual_sim: Simulation, graph) -> None:
    sim = manual_sim
    ac = _taxiing(sim, graph, "TST2", _crossing_route(graph))
    first = ac.path.runs[0]
    assert sim.issue_clearance("TST2", "cross_runway", {"runway": "25R"})["ok"]
    for _ in range(3000):
        sim.step()
        if ac.s - ac.length_m / 2 > first.s1:
            break
    assert ac.s - ac.length_m / 2 > first.s1 and not fails(sim, "runway_incursion")


def test_landing_without_a_clearance_is_an_incursion(manual_sim: Simulation) -> None:
    sim = manual_sim
    ac = sim.aircraft["SKW722"]
    ac.landing_cleared = True                                 # the pilot lands, but no landing clearance was issued
    for _ in range(3000):
        sim.step()
        if ac.touchdown_t is not None:
            break
    got = fails(sim, "runway_incursion")
    assert len(got) == 1 and "landed on runway 08/26 without a landing clearance" in got[0]["reason"]


# -- 5. diversion --------------------------------------------------------------------------------------------------------
def test_arrival_held_too_long_diverts_and_fails(manual_sim: Simulation) -> None:
    sim = manual_sim
    ac = sim.aircraft["SKW722"]
    view = next(a for a in sim.snapshot(mark_sent=False)["aircraft"] if a["id"] == "SKW722")
    assert view["holding_s"] == 0.0 and view["holding_limit_s"] == L.DIVERSION_HOLDING_S
    assert all("holding_s" in a and "holding_limit_s" in a for a in sim.observe()["aircraft"])
    ac.scheduled_s, ac.holding_s = 0.0, L.DIVERSION_HOLDING_S - 0.45
    steps(sim, 10)
    assert ac.removed and [e for e in sim.log.events if e["kind"] == "aircraft.diverted"]
    got = fails(sim, "diversion")
    assert len(got) == 1 and got[0]["callsigns"] == ["SKW722"]
    assert "SKW722" not in {a["id"] for a in sim.snapshot(mark_sent=False)["aircraft"]}
    assert sim.score()["diversions"] == 1


# -- 6. stuck / gridlock -------------------------------------------------------------------------------------------------
def test_do_nothing_controller_fails_stuck_within_the_limit(tmp_path, graph) -> None:
    sim = Simulation(SMOKE, run_dir=tmp_path / "noop", controller=NoopController(), graph=graph)
    first = min(f.scheduled_sim_s for f in sim.scenario.flights if f.kind == "departure")
    while sim.t < first + L.STUCK_AT_GATE_S + 1.0 and not fails(sim, "stuck"):
        sim.step()
    sim.close()
    got = fails(sim, "stuck")
    assert got and got[0]["callsigns"] == ["AAL2702"] and got[0]["t"] <= first + L.STUCK_AT_GATE_S + 0.1
    assert "never pushed back" in got[0]["reason"] and sim.referee_report()["verdict"] == "FAIL"


def test_aircraft_stopped_on_a_taxiway_too_long_is_stuck(manual_sim: Simulation, graph) -> None:
    sim = manual_sim
    ac = _taxiing(sim, graph, "TST3", _crossing_route(graph), s=20.0)
    ac.hold_s = 20.0
    steps(sim, 2)
    ac.stopped_since = sim.t - L.STUCK_STOPPED_S
    steps(sim, 20)
    got = fails(sim, "stuck")
    assert len(got) == 1 and got[0]["callsigns"] == ["TST3"] and "has not moved" in got[0]["reason"]


def test_a_gridlock_is_stuck(manual_sim: Simulation) -> None:
    sim = manual_sim
    ev = sim.emit("deadlock", "A1", {"aircraft": ["A1", "B2"], "waited_s": 60.0})
    sim.referee.tick([ev])
    got = fails(sim, "stuck")
    assert len(got) == 1 and got[0]["callsigns"] == ["A1", "B2"] and "gridlocked" in got[0]["reason"]


# -- 7. traffic unhandled at the end of the day --------------------------------------------------------------------------
def test_traffic_left_at_the_end_of_the_day_fails(manual_sim: Simulation) -> None:
    sim = manual_sim
    assert sim.referee.day_end_s == sim.scenario.duration_s + L.END_OF_DAY_GRACE_S
    sim.referee.day_end_s = sim.t + 0.15
    steps(sim, 5)
    assert sim.finished and [e for e in sim.log.events if e["kind"] == "scenario.day_end"]
    got = fails(sim, "unhandled_at_end")
    assert len(got) == 1 and sorted(got[0]["callsigns"]) == sorted(f.callsign for f in sim.scenario.flights)
    results = [e for e in sim.log.events if e["kind"] == "referee_result"]
    assert len(results) == 1 and results[0]["data"]["verdict"] == "FAIL"
    assert results[0]["data"]["score"]["fails_by_rule"]["unhandled_at_end"] == 1


# -- contract ------------------------------------------------------------------------------------------------------------
def test_referee_state_is_in_snapshot_delta_and_observe(manual_sim: Simulation) -> None:
    sim = manual_sim
    snap = sim.snapshot()
    assert snap["referee"]["verdict"] == "RUNNING" and snap["referee"]["fails"] == []
    assert sim.observe()["referee"]["verdict"] == "RUNNING"
    sim.aircraft["SKW722"].holding_s = 1e9
    sim.aircraft["SKW722"].scheduled_s = 0.0
    steps(sim, 2)
    d = sim.delta()
    assert d["referee"]["verdict"] == "FAIL" and d["referee"]["fails"][0]["rule"] == "diversion"
    ev = next(e for e in d["events"] if e["kind"] == "referee_fail")
    assert set(ev["data"]) == {"rule", "callsigns", "reason", "t"}

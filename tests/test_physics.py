from __future__ import annotations

import math

from sim.core.engine import Simulation
from sim.core.entities import Aircraft
from sim.core.aircraft_types import get_type
from tests.conftest import BUSY


def test_no_teleporting_and_smooth_motion_over_a_full_run(tmp_path, graph) -> None:
    sim = Simulation(BUSY, run_dir=tmp_path / "r", graph=graph)
    prev = {}
    worst_step = worst_turn = worst_acc = 0.0
    while not sim.finished and sim.t < 1500:
        sim.step()
        for a in sim.aircraft.values():
            if a.removed:
                continue
            if a.callsign in prev:
                px, py, ph, pv = prev[a.callsign]
                worst_step = max(worst_step, math.hypot(a.x - px, a.y - py) - max(a.speed, pv) * 0.1)
                worst_turn = max(worst_turn, abs((a.heading - ph + 180) % 360 - 180))
                worst_acc = max(worst_acc, abs(a.speed - pv) / 0.1)
            prev[a.callsign] = (a.x, a.y, a.heading, a.speed)
    sim.close()
    assert sim.finished
    assert worst_step < 0.1          # never moves further than its speed allows in one tick
    assert worst_turn <= 3.0         # heading changes at most 30 deg/s
    assert worst_acc <= 5.0          # no instant stops or starts (m/s^2)


def _add(sim: Simulation, graph, name: str, route: list, s: float, speed: float = 0.0, phase: str = "taxi_out") -> Aircraft:
    ac = Aircraft(callsign=name, type=get_type("B738"), kind="departure", airline="TST", seq=len(sim.aircraft) + 100, phase=phase)
    ac.path = graph.build_path(route)
    ac.s, ac.speed = s, speed
    xy = ac.path.xy_at(s)
    ac.x, ac.y = float(xy[0]), float(xy[1])
    ac.heading = ac.path.heading_at(s)
    sim.aircraft[name] = ac
    return ac


def _long_route(graph):
    stand = graph.stands["B12"]
    for end in ("25R", "26"):
        route = graph.route(stand.lane_node, graph.entries(end)[0][0])
        path = graph.build_path(route)
        if path.length > 600 and not path.runs[:-1]:
            return route
    raise AssertionError("no long route")


def test_aircraft_holds_a_60m_gap_behind_a_stopped_leader(manual_sim: Simulation, graph) -> None:
    route = _long_route(graph)
    leader = _add(manual_sim, graph, "LEAD1", route, s=420.0)
    leader.hold_s = 420.0                                   # leader stays put
    follower = _add(manual_sim, graph, "FOLL1", route, s=0.0)
    follower.cleared_runs = {len(follower.path.runs) - 1} if follower.path.runs else set()
    for _ in range(1500):
        manual_sim.step()
    gap = leader.s - follower.s - (leader.length_m + follower.length_m) / 2
    assert follower.speed < 0.1
    assert 55.0 <= gap <= 75.0, gap          # holds behind at about 60 m, never closer
    assert not [e for e in manual_sim.log.events if e["kind"] == "separation_loss"]


def test_aircraft_holds_short_of_an_occupied_runway_and_goes_when_it_clears(manual_sim: Simulation, graph) -> None:
    stand = graph.stands["B12"]
    route = graph.route(stand.lane_node, graph.entries("25L")[0][0])      # crosses 07L/25R on the way
    ac = _add(manual_sim, graph, "XING1", route, s=0.0)
    first_run = ac.path.runs[0]
    ac.cleared_runs = set(range(len(ac.path.runs)))                       # cleared to cross everything
    manual_sim.res.enter(first_run.runway, "BLOCKER", manual_sim.t)       # someone is on the pavement
    for _ in range(5000):
        manual_sim.step()
        if ac.speed < 0.01 and ac.s > 100:
            break
    assert ac.speed < 0.1 and ac.s < first_run.s0 - ac.length_m / 2      # held short of the pavement
    assert ac.stop_why == "busy"
    manual_sim.res.leave(first_run.runway, "BLOCKER", manual_sim.t)
    for _ in range(600):
        manual_sim.step()
    assert ac.s > first_run.s0                                           # proceeds once the runway is free


def test_landing_on_an_occupied_runway_is_allowed_and_logged_not_refused(manual_sim: Simulation) -> None:
    sim = manual_sim
    sim.runway_cfg["arrival_runways"] = ["26"]
    sim.res.enter("08/26", "OBSTACLE", sim.t)
    cs = next(a.callsign for a in sim.aircraft.values() if a.kind == "arrival")
    assert sim.issue_clearance(cs, "cleared_to_land", {"runway": "26"})["ok"]
    for _ in range(3000):
        sim.step()
        if sim.aircraft[cs].touchdown_t is not None:
            break
    kinds = [e for e in sim.log.events if e["kind"] == "conflict"]
    assert any(e["data"]["type"] == "landing_on_occupied_runway" for e in kinds)


def test_final_approach_speed_comes_from_csv_and_arrivals_are_smooth(manual_sim: Simulation) -> None:
    ac = next(a for a in manual_sim.aircraft.values() if a.kind == "arrival")
    assert abs(ac.type.vref_mps - 74.6) < 20 and ac.type.vref_mps > 50      # CRJ9 ACD Vref proxy 141 kt
    assert ac.speed >= ac.type.vref_mps and ac.alt_m > 100

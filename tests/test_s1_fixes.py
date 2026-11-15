"""S1 simulator fixes: no readback truth leak, explicit taxi routes, observation RNG, wrong-readback and wrong-phase handling."""
from __future__ import annotations

import json
from typing import Any, List

from sim.core.engine import READBACK_SILENCE_S, Simulation
from sim.core.entities import Aircraft
from sim.core.aircraft_types import get_type
from tests.conftest import BUSY, SMOKE, NoopController

READBACK_KEYS = {"id", "clearance_id", "voice_id", "text", "parsed", "status"}


def departure(sim: Simulation) -> Aircraft:
    return next(a for a in sim.aircraft.values() if a.kind == "departure")


def steps(sim: Simulation, n: int) -> None:
    for _ in range(n):
        sim.step()


class Recorder:
    """Keeps every event and observation a controller is handed, to check nothing leaks the readback truth."""
    name, status = "recorder", "idle"

    def __init__(self) -> None:
        from sim.tower.controller import TowerController
        self.tower = TowerController()
        self.seen: List[Any] = []

    def on_tick(self, sim: Simulation, events: List[Any]) -> None:
        self.seen.extend(events)
        self.seen.append(sim.observe())
        self.tower.on_tick(sim, events)


# -- item 3: readback truth stays private -----------------------------------------------------------------------------
def test_tower_catches_wrong_readbacks_without_seeing_which_are_wrong(tmp_path, graph) -> None:
    rec = Recorder()
    sim = Simulation(BUSY, run_dir=tmp_path / "busy", controller=rec, graph=graph)
    sim.run_to_completion()
    sim.close()
    assert sim.finished
    readbacks = [e for e in sim.log.events if e["kind"] == "readback"]
    assert readbacks and all(set(e["data"]) == READBACK_KEYS and e["data"]["status"] == "pending" for e in readbacks)
    dump = json.dumps(rec.seen)
    assert "error_truth" not in dump and "error_accepted" not in dump and "wrong_runway_side" not in dump
    assert "dropped_word" not in dump
    score = sim.score()
    assert sim.readback_truth and score["readback_errors_caught"] == len(sim.readback_truth)   # tower caught every one
    assert score["readback_errors_accepted"] == 0


def test_correct_readbacks_are_pending_too_and_silence_accepts_them(manual_sim: Simulation) -> None:
    sim = manual_sim
    ac = departure(sim)
    res = sim.issue_clearance(ac.callsign, "pushback_approved")
    steps(sim, 31)                                   # readback due 1.5-3.0 s later
    pending = sim.observe()["pending_readbacks"]
    assert [p["clearance_id"] for p in pending] == [res["clearance_id"]]     # error rate is 0: a correct readback, pending
    assert next(v for v in sim.voice if v.speaker == "pilot").status == "pending"
    steps(sim, int(READBACK_SILENCE_S * 10) + 1)
    assert not sim.observe()["pending_readbacks"]
    assert sim.clearances[res["clearance_id"]].state == "acknowledged" and ac.phase == "pushback"
    acc = [e for e in sim.log.events if e["kind"] == "readback.accepted"]
    assert acc and acc[0]["data"]["implicit"] is True


# -- item 2: taxi_via follows the explicit route ------------------------------------------------------------------------
def _detour(graph, src: str, dst: str) -> List[str]:
    """A valid route src -> dst through a node that is not on the shortest path."""
    short = graph.route(src, dst)
    on = set(short)
    adj = {}
    for e in graph.edges.values():
        if e.pavement is None:
            adj.setdefault(e.u, set()).add(e.v)
            adj.setdefault(e.v, set()).add(e.u)
    for n in short[2:-2]:
        for mid in sorted(adj.get(n, ())):
            if mid in on:
                continue
            a, b = graph.route(src, mid), graph.route(mid, dst)
            if a and b and len(set(a + b[1:])) == len(a) + len(b) - 1:
                return a + b[1:]
    raise AssertionError("no detour found")


def test_taxi_via_route_is_followed_node_for_node(manual_sim: Simulation, graph) -> None:
    sim = manual_sim
    ac = departure(sim)
    src = graph.stands[ac.gate].node
    dst = graph.route(src, graph.entries("25R")[0][0])[-6]
    route = _detour(graph, src, dst)
    assert route != graph.route(src, dst)
    res = sim.issue_clearance(ac.callsign, "taxi_via", {"route": route})
    assert res["ok"], res
    steps(sim, 30 + int(READBACK_SILENCE_S * 10) + 1)          # readback, then silence accepts it
    assert ac.path is not None and ac.phase == "taxi_out"
    nodes = sorted(ac.path.node_s, key=ac.path.node_s.get)
    assert nodes == route                                      # every node, in order, not the shortest path
    assert abs(ac.path.length - graph.route_length(route)) < 1.0


def test_taxi_via_with_only_to_still_takes_the_shortest_path(manual_sim: Simulation, graph) -> None:
    sim = manual_sim
    ac = departure(sim)
    src = graph.stands[ac.gate].node
    dst = graph.route(src, graph.entries("25R")[0][0])[-6]
    assert sim.issue_clearance(ac.callsign, "taxi_via", {"to": dst})["ok"]
    steps(sim, 30 + int(READBACK_SILENCE_S * 10) + 1)
    assert sorted(ac.path.node_s, key=ac.path.node_s.get) == graph.route(src, dst)


def test_taxi_via_route_shape_errors(manual_sim: Simulation, graph) -> None:
    sim = manual_sim
    ac = departure(sim)
    src = graph.stands[ac.gate].node
    full = graph.route(src, graph.entries("25R")[0][0])
    gap = sim.issue_clearance(ac.callsign, "taxi_via", {"route": [full[0], full[1], full[5]]})
    assert gap["ok"] is False and "not connected by a graph edge" in gap["error"]
    away = sim.issue_clearance(ac.callsign, "taxi_via", {"route": full[3:]})
    assert away["ok"] is False and "next node %s" % src in away["error"]
    short = sim.issue_clearance(ac.callsign, "taxi_via", {"route": full[:-3], "to": "25R"})
    assert short["ok"] is False and "must end at a node on that runway" in short["error"]
    ok = sim.issue_clearance(ac.callsign, "taxi_via", {"route": full, "to": "25R"})
    assert ok["ok"] and ok["text"]
    assert sim.observe()["aircraft"] and any(a["next_node"] == src for a in sim.observe()["aircraft"])


# -- item 4: observation noise has its own generator ----------------------------------------------------------------------
class Scripted:
    """Fixed actions from the truth, never from observations; optionally calls observe() many times per tick."""
    name, status = "scripted", "idle"

    def __init__(self, observe_calls: int) -> None:
        self.observe_calls = observe_calls
        self.pushed: set = set()

    def on_tick(self, sim: Simulation, events: List[Any]) -> None:
        for _ in range(self.observe_calls if sim.clock.tick % 3 else 0):
            sim.observe()
        for e in events:
            if e["kind"] == "readback":
                sim.resolve_readback(e["data"]["id"], True)
        for a in sim.aircraft.values():
            if a.kind == "departure" and a.ready and a.callsign not in self.pushed:
                self.pushed.add(a.callsign)
                sim.issue_clearance(a.callsign, "pushback_approved")
                sim.issue_clearance(a.callsign, "hold_short", {"runway": "25R"})


def _truth_stream(tmp_path, graph, name: str, observe_calls: int) -> str:
    sim = Simulation(SMOKE, run_dir=tmp_path / name, controller=Scripted(observe_calls), graph=graph)
    sim.scenario.readback_error_rate = 0.5
    steps(sim, 2000)
    sim.close()
    return json.dumps([(e["kind"], e["entity_id"], e["sim_time_ms"], e["data"]) for e in sim.log.events], sort_keys=True)


def test_observe_call_pattern_does_not_change_the_world(tmp_path, graph) -> None:
    none = _truth_stream(tmp_path, graph, "none", 0)
    many = _truth_stream(tmp_path, graph, "many", 7)
    assert "pushback.start" in none and "readback" in none
    assert none == many


def test_observation_noise_is_seeded(tmp_path, graph) -> None:
    def obs(name: str) -> Any:
        sim = Simulation(SMOKE, run_dir=tmp_path / name, controller=NoopController(), graph=graph)
        steps(sim, 20)
        out = [sim.observe()["aircraft"] for _ in range(3)]
        sim.close()
        return out
    a, b = obs("a"), obs("b")
    assert a == b and a[0] != a[1]                          # repeatable per seed, fresh noise per call


# -- item 5a: an accepted wrong hold-short only affects that one hold ------------------------------------------------------
def test_accepted_wrong_hold_short_does_not_disable_later_holds(manual_sim: Simulation, graph) -> None:
    sim = manual_sim
    stand = graph.stands["B12"]
    route = graph.route(stand.lane_node, graph.entries("25L")[0][0])        # crosses 07L/25R, then enters 07R/25L
    ac = Aircraft(callsign="TST1", type=get_type("B738"), kind="departure", airline="TST", seq=500, phase="taxi_out")
    ac.path = graph.build_path(route)
    xy = ac.path.xy_at(0.0)
    ac.x, ac.y, ac.heading = float(xy[0]), float(xy[1]), ac.path.heading_at(0.0)
    sim.aircraft["TST1"] = ac
    first, second = ac.path.runs[0], ac.path.runs[1]
    assert first.runway == "07L/25R" and second.runway == "07R/25L"
    ac.cleared_runs = {0}                                   # cleared to cross 25R; 25L is not cleared
    sim.scenario.readback_error_rate = 1.0
    c = sim.issue_clearance("TST1", "hold_short", {"runway": "25R"})["clearance_id"]
    steps(sim, 31)                                          # the pilot reads back when due: wrong at rate 1.0
    sim.scenario.readback_error_rate = 0.0
    rb = next(p for p in sim.observe()["pending_readbacks"] if p["clearance_id"] == c)
    assert rb["parsed"]["runway"] != "25R"
    sim.resolve_readback(rb["id"], True)                     # the controller misses the wrong readback
    assert sim.score()["readback_errors_accepted"] == 1
    for _ in range(6000):
        sim.step()
        if ac.s > first.s1 + ac.length_m and ac.speed < 0.05:
            break
    assert ac.s > first.s1                                  # acted on the wrong readback: did not hold short of 25R
    assert sim.clearances[c].state == "complete"            # the hold-short ended once the aircraft was past that runway
    assert ac.speed < 0.05 and ac.stop_why == "run" and ac.s < second.s0   # but still holds at the next runway
    later = sim.issue_clearance("TST1", "cross_runway", {"runway": "25L"})["clearance_id"]
    steps(sim, 30 + int(READBACK_SILENCE_S * 10) + 1)
    assert sim.clearances[later].state == "acknowledged" and 1 in ac.cleared_runs


# -- item 5b: a clearance that cannot take effect does not stay acknowledged ------------------------------------------------
def test_wrong_phase_clearances_end_as_no_effect(manual_sim: Simulation) -> None:
    sim = manual_sim
    ac = departure(sim)
    assert ac.phase == "at_gate"
    ids = [sim.issue_clearance(ac.callsign, act, p)["clearance_id"] for act, p in
           (("cleared_to_land", {"runway": "26"}), ("go_around", {}), ("cleared_for_takeoff", {"runway": "25R"}),
            ("hold_position", {}))]
    steps(sim, 30 + int(READBACK_SILENCE_S * 10) + 1)
    assert [sim.clearances[i].state for i in ids] == ["cancelled"] * 4
    no_effect = {e["data"]["clearance_id"] for e in sim.log.events if e["kind"] == "clearance.no_effect"}
    assert set(ids) <= no_effect
    assert not {c["id"] for c in sim.observe()["clearances"]} & set(ids)
    assert not ac.takeoff_cleared and not sim.active.get(ac.callsign)

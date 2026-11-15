"""S3 levels: seeds, repeatability, rain effects, spawn spacing and the wind-shift runway change."""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List

import pytest

from sim import levels
from sim.core.engine import Simulation
from sim.core.aircraft_types import get_type
from tests.conftest import NoopController

SHORT_S = 600.0     # a shorter day keeps the test runs quick; the shift still happens (WIND_SHIFT_FRAC of the day)


def run_level(level: str, seed: int, duration: float, run_dir: Any, graph: Any, controller: Any = "tower",
              probe: Any = None) -> Simulation:
    sim = Simulation(level=level, seed=seed, duration_s=duration, run_dir=run_dir, graph=graph, controller=controller)
    while not sim.finished:
        sim.step()
        if probe:
            probe(sim)
    sim.log.flush()
    sim.close()
    return sim


def test_training_and_held_out_seeds_are_disjoint() -> None:
    assert set(levels.TRAINING_SEEDS) == set(levels.HELD_OUT_SEEDS) == set(levels.LEVEL_IDS) == {"normal_day", "light_rain"}
    train = {s for v in levels.TRAINING_SEEDS.values() for s in v}
    held = {s for v in levels.HELD_OUT_SEEDS.values() for s in v}
    assert not train & held
    for lv in levels.LEVEL_IDS:
        assert len(levels.TRAINING_SEEDS[lv]) == 5 and len(levels.HELD_OUT_SEEDS[lv]) == 3


def test_same_level_and_seed_is_repeatable(tmp_path, graph) -> None:
    a = run_level("light_rain", 21, SHORT_S, tmp_path / "a" / "run", graph)
    b = run_level("light_rain", 21, SHORT_S, tmp_path / "b" / "run", graph)
    ha, hb = (hashlib.sha256((p / "events.jsonl").read_bytes()).hexdigest() for p in (tmp_path / "a" / "run", tmp_path / "b" / "run"))
    assert ha == hb and a.referee_report() == b.referee_report()


def test_seeds_differ_and_levels_share_the_traffic(graph) -> None:
    def flights(level: str, seed: int) -> List[Any]:
        return [(f.callsign, f.type, f.kind, f.scheduled_sim_s, f.gate) for f in levels.build_scenario(level, seed, graph=graph).flights]
    assert flights("normal_day", 1) != flights("normal_day", 2)
    assert flights("normal_day", 3) == flights("light_rain", 3)      # same day shape: the level only changes the weather


def test_traffic_mix_and_arrival_schedule_spacing(graph) -> None:
    for seed in levels.TRAINING_SEEDS["normal_day"]:
        sc = levels.build_scenario("normal_day", seed, graph=graph)
        arr = [f for f in sc.flights if f.kind == "arrival"]
        assert any(get_type(f.type).wtc.startswith("H") for f in arr) and any(get_type(f.type).light for f in arr)
        assert len({f.callsign for f in sc.flights}) == len(sc.flights)
        for stream in levels.ARRIVAL_STREAMS:
            s = sorted((f for f in arr if f.runway_preference == stream), key=lambda f: f.scheduled_sim_s)
            for lead, follow in zip(s, s[1:]):
                gap = levels.arrival_gap_s(get_type(lead.type), get_type(follow.type))
                assert follow.scheduled_sim_s - lead.scheduled_sim_s >= gap - 0.11, (seed, lead.callsign, follow.callsign)


class ClearEveryArrival:
    """Clears every arrival to land on the runway it was handed over on, and does nothing else."""
    name = "clear-all"
    status = "idle"

    def on_tick(self, sim: Any, events: List[Dict[str, Any]]) -> None:
        for e in events:
            if e["kind"] == "aircraft.spawned" and e["data"]["kind"] == "arrival":
                sim.issue_clearance(e["entity_id"], "cleared_to_land", {"runway": e["data"]["runway"]})


def test_arrivals_reach_the_tower_legally_separated(tmp_path, graph, monkeypatch) -> None:
    monkeypatch.setattr(levels, "WIND_STEPS", [])          # no wind shift: this controller never turns the airport round
    sim = run_level("normal_day", 3, levels.DEFAULT_DURATION_S, tmp_path / "sp", graph, controller=ClearEveryArrival())
    ev = sim.log.events
    first_ga = min((e["sim_time_ms"] for e in ev if e["kind"] == "go_around"), default=10 ** 12)
    losses = [e for e in ev if e["kind"] == "separation_loss" and e["data"]["kind"] == "air" and e["sim_time_ms"] < first_ga]
    assert sum(1 for e in ev if e["kind"] == "aircraft.touchdown") >= 10
    assert losses == []


def test_pilot_go_around_rate_matches_the_level(graph) -> None:
    for level, lo, hi in (("light_rain", 0.04, 0.06), ("normal_day", 0.0, 0.006)):
        sim = Simulation(level=level, seed=5, graph=graph, controller=NoopController())
        while not any(a.kind == "arrival" for a in sim.aircraft.values()):
            sim.step()
        ac = next(a for a in sim.aircraft.values() if a.kind == "arrival")
        n = 4000
        hits = sum(1 for _ in range(n) if sim._pilot_go_around(ac))
        assert lo <= hits / n <= hi, (level, hits / n, sim.weather.go_around_rate)
        sim.close()


@pytest.fixture(scope="module")
def pair(tmp_path_factory, graph) -> Dict[str, Any]:
    """Normal Day and Light Rain, same seed, with the tower; records taxi and approach speeds as they fly."""
    out: Dict[str, Any] = {}
    for level in ("normal_day", "light_rain"):
        seen = {"taxi": 0.0, "final": []}

        def probe(sim: Simulation, seen: Dict[str, Any] = seen) -> None:
            for a in sim.aircraft.values():
                if a.removed or a.kind == "vehicle":
                    continue
                if a.phase in ("taxi_in", "taxi_out"):
                    seen["taxi"] = max(seen["taxi"], a.speed)
                elif a.phase == "final" and a.wp is None and 5000.0 < sim.physics.approach_distance(a) < 6000.0:
                    seen["final"].append(a.speed - a.type.vref_mps)
        sim = run_level(level, 3, 900.0, tmp_path_factory.mktemp(level), graph, probe=probe)
        out[level] = (sim, seen)
    return out


def test_light_rain_has_the_documented_effects(pair) -> None:
    (dry, d), (wet, w) = pair["normal_day"], pair["light_rain"]
    rain = levels.LEVELS["light_rain"]
    assert wet.weather_view()["vis_sm"] < dry.weather_view()["vis_sm"] and "-RA" in wet.weather_view()["metar"]
    assert w["taxi"] == pytest.approx(d["taxi"] * rain["taxi_mult"], rel=0.03)
    extra_dry, extra_wet = sum(d["final"]) / len(d["final"]), sum(w["final"]) / len(w["final"])
    assert extra_wet == pytest.approx(extra_dry * rain["approach_mult"], rel=0.1)        # slower approach above Vref
    assert wet.score()["avg_landing_occupancy_s"] > dry.score()["avg_landing_occupancy_s"] * 1.08
    assert wet.level_view()["effects"]["go_around_rate"] > dry.level_view()["effects"]["go_around_rate"]


def test_tower_turns_the_airport_round_before_the_tailwind_limit(pair) -> None:
    for level in ("normal_day", "light_rain"):
        sim = pair[level][0]
        ev = sim.log.events
        wx = [(e["sim_time_ms"], e["data"]) for e in ev if e["kind"] == "weather.update"]
        cfg = [(e["sim_time_ms"], e["data"]) for e in ev if e["kind"] == "runway.config"]
        assert len(wx) == len(levels.WIND_STEPS) and wx[-1][1]["wind_dir"] == 90
        assert cfg and cfg[0][1] == {"arrival_runways": ["07R", "08"], "departure_runways": ["07L", "08"]}
        assert cfg[0][0] < wx[-1][0]                       # switched while the tailwind was still within the limit
        assert any(e["kind"] == "referee_result" for e in ev)


def _resolve_all(sim: Simulation, ticks: int) -> None:
    for _ in range(ticks):
        sim.step()
        for rb in sim.observe()["pending_readbacks"]:
            sim.resolve_readback(rb["id"], True)


def test_tailwind_is_refused_and_switching_direction_fixes_it(tmp_path, graph) -> None:
    sim = Simulation(level="normal_day", seed=1, run_dir=tmp_path / "tw", graph=graph, controller=NoopController())
    sim.weather._i = len(sim.weather.schedule) - 1                       # jump to the post-shift wind: 090 at 14 kt
    sim.weather.current = dict(sim.weather.schedule[-1][1], t=0.0)
    while not any(a.kind == "arrival" for a in sim.aircraft.values()):
        sim.step()
    arr = next(a for a in sim.aircraft.values() if a.kind == "arrival")
    dep = next(a for a in sim.aircraft.values() if a.kind == "departure")
    west = arr.runway
    assert sim.tailwind_over(west) == 14.0 and sim.tailwind_over(sim.graph.other_end(west)) is None
    c1 = sim.issue_clearance(arr.callsign, "cleared_to_land", {"runway": west})["clearance_id"]
    c2 = sim.issue_clearance(dep.callsign, "cleared_for_takeoff", {"runway": "25R"})["clearance_id"]
    _resolve_all(sim, 40)
    unable = {e["data"]["clearance_id"]: e for e in sim.log.events if e["kind"] == "pilot.unable"}
    assert set(unable) == {c1, c2} and not arr.landing_cleared
    assert sim.clearances[c1].state == "cancelled" and unable[c1]["data"]["tailwind_kt"] == 14.0
    assert any(v.speaker == "pilot" and v.text.startswith("Unable to land") for v in sim.voice)
    # turn the airport round: the arrival flies round to the other end and is accepted there
    assert sim.set_runway_config(["07R", "08"], ["07L", "08"])["ok"]
    east = sim.graph.other_end(west)
    sim.issue_clearance(arr.callsign, "contact_tower_or_ground", {"facility": "tower", "runway": east})
    c3 = sim.issue_clearance(arr.callsign, "cleared_to_land", {"runway": east})["clearance_id"]
    c4 = sim.issue_clearance(dep.callsign, "cleared_for_takeoff", {"runway": "07L"})["clearance_id"]
    _resolve_all(sim, 40)
    unable = {e["data"]["clearance_id"] for e in sim.log.events if e["kind"] == "pilot.unable"}
    assert c3 not in unable and c4 not in unable
    assert arr.landing_cleared and arr.runway == east and arr.wp is not None
    assert any(e["kind"] == "arrival.reversed" and e["entity_id"] == arr.callsign for e in sim.log.events)
    sim.close()


def test_controller_sees_the_level_and_weather(graph) -> None:
    sim = Simulation(level="light_rain", seed=21, graph=graph, controller=NoopController())
    sim.step()
    obs, snap = sim.observe(), sim.snapshot(mark_sent=False)
    assert obs["level"] == snap["level"] and obs["level"]["id"] == "light_rain" and obs["level"]["seed"] == 21
    assert obs["level"]["tailwind_limit_kt"] == levels.TAILWIND_LIMIT_KT
    assert set(obs["level"]["effects"]) == {"taxi_mult", "occupancy_mult", "approach_mult", "go_around_rate"}
    assert obs["weather"]["wx"] == "-RA BR" and obs["weather"]["vis_sm"] == 4.0
    assert set(obs["weather"]) == {"t", "metar", "wind_dir", "wind_kt", "gust_kt", "vis_sm", "wx", "temp_f"}
    with pytest.raises(ValueError):
        Simulation(level="haboob", graph=graph)
    sim.close()

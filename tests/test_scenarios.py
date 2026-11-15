from __future__ import annotations

from sim.core.engine import Simulation
from sim.metrics.score import compute_score
from tests.conftest import HABOOB, SMOKE


def test_smoke_runs_to_completion_with_no_conflicts(tmp_path, graph) -> None:
    sim = Simulation(SMOKE, run_dir=tmp_path / "smoke", graph=graph)
    sim.run_to_completion()
    sim.close()
    assert sim.finished and len(sim.scenario.flights) == 6
    bad = [e for e in sim.log.events if e["kind"] in ("conflict", "separation_loss", "deadlock")]
    assert not bad, bad
    score = compute_score(sim.log.events, sim.t)
    assert score["completed"] == 6 and score["readback_errors_accepted"] == 0
    assert (tmp_path / "smoke" / "events.jsonl").exists()


def test_every_event_uses_the_envelope(tmp_path, graph) -> None:
    sim = Simulation(SMOKE, run_dir=tmp_path / "env", graph=graph)
    sim.run_to_completion()
    sim.close()
    required = {"schema_version", "run_id", "mode", "event_id", "airport_icao", "kind", "entity_id", "source_id", "sim_time_ms",
                "source_sequence", "quality", "quality_reason", "data"}
    assert all(required <= set(e) for e in sim.log.events)


def test_haboob_scenario_weather_replan_and_clean_run(tmp_path, graph) -> None:
    sim = Simulation(HABOOB, run_dir=tmp_path / "hab", graph=graph)
    sim.run_to_completion()
    sim.close()
    assert sim.finished
    kinds = [e["kind"] for e in sim.log.events]
    assert "weather.haboob_triggered" in kinds and kinds.count("weather.update") > 20
    assert any(e["data"].get("low_vis") for e in sim.log.events if e["kind"] == "weather.update")
    cfg = [e["data"] for e in sim.log.events if e["kind"] == "runway.config"]
    assert len(cfg) >= 2 and cfg[0]["departure_runways"] == ["25R"]               # replanned, then restored
    actions = {d["action"] for d in sim.decisions.values()}
    assert {"weather_replan", "weather_recover"} <= actions
    assert [e for e in sim.log.events if e["kind"] in ("conflict", "separation_loss", "deadlock")] == []
    score = compute_score(sim.log.events, sim.t)
    assert score["completed"] == 12 and score["recovery_time_s"] is not None


def test_low_visibility_slows_taxi_and_extends_occupancy(tmp_path, graph) -> None:
    sim = Simulation(HABOOB, run_dir=tmp_path / "wx", graph=graph)
    assert sim.weather.taxi_mult == 1.0 and sim.weather.occupancy_mult == 1.0
    sim.control("trigger_haboob")
    while not sim.weather.low_vis and sim.t < 400:
        sim.step()
    assert sim.weather.low_vis and sim.weather.taxi_mult == 0.6 and sim.weather.occupancy_mult == 1.2
    sim.close()


def test_scores_expose_the_contract_fields(tmp_path, graph) -> None:
    sim = Simulation(SMOKE, run_dir=tmp_path / "sc", graph=graph)
    sim.run_to_completion(max_s=100)
    sim.close()
    score = sim.snapshot(mark_sent=False)["score"]
    assert {"separation_losses", "runway_conflicts", "deadlocks", "readback_errors_accepted", "completed", "avg_delay_s",
            "throughput_per_10min"} <= set(score)


def test_ground_vehicle_and_ga_aircraft_are_in_the_haboob_scenario(tmp_path, graph) -> None:
    sim = Simulation(HABOOB, run_dir=tmp_path / "veh", graph=graph)
    assert len(sim.scenario.vehicles) == 1 and any(f.airline == "GA" for f in sim.scenario.flights)
    veh = next(a for a in sim.aircraft.values() if a.kind == "vehicle")
    start = (veh.x, veh.y)
    for _ in range(600):
        sim.step()
    assert (veh.x - start[0]) ** 2 + (veh.y - start[1]) ** 2 > 100.0       # it drives
    sim.close()

from __future__ import annotations

from sim.core.engine import Simulation
from tests.conftest import BUSY, SMOKE


def test_smoke_runs_to_completion_with_no_conflicts(tmp_path, graph) -> None:
    sim = Simulation(SMOKE, run_dir=tmp_path / "smoke", graph=graph)
    sim.run_to_completion()
    sim.close()
    assert sim.finished and len(sim.scenario.flights) == 6
    bad = [e for e in sim.log.events if e["kind"] in ("conflict", "separation_loss", "deadlock")]
    assert not bad, bad
    score = sim.score()
    assert score["completed"] == 6 and score["readback_errors_accepted"] == 0
    assert (tmp_path / "smoke" / "events.jsonl").exists()


def test_every_event_uses_the_envelope(tmp_path, graph) -> None:
    sim = Simulation(SMOKE, run_dir=tmp_path / "env", graph=graph)
    sim.run_to_completion()
    sim.close()
    required = {"schema_version", "run_id", "mode", "event_id", "airport_icao", "kind", "entity_id", "source_id", "sim_time_ms",
                "source_sequence", "quality", "quality_reason", "data"}
    assert all(required <= set(e) for e in sim.log.events)


def test_busy_scenario_runs_clean_with_calm_static_weather(tmp_path, graph) -> None:
    sim = Simulation(BUSY, run_dir=tmp_path / "busy", graph=graph)
    assert sim.scenario.readback_error_rate > 0
    w0 = sim.weather_view()
    sim.run_to_completion()
    sim.close()
    assert sim.finished and sim.score()["completed"] == 12
    assert sim.referee_report()["verdict"] == "PASS", sim.referee_report()["fails"]
    assert [e for e in sim.log.events if e["kind"] in ("conflict", "separation_loss", "deadlock")] == []
    assert sim.weather_view() == w0 and w0["vis_sm"] == 10.0 
    assert set(w0) == {"t", "metar", "wind_dir", "wind_kt", "gust_kt", "vis_sm", "wx", "temp_f"}
    assert not any(e["kind"].startswith("weather.") for e in sim.log.events)
    assert sim.weather.taxi_mult == 1.0 and sim.weather.occupancy_mult == 1.0
    assert any(e["kind"] == "readback.rejected" for e in sim.log.events)      # the tower caught wrong readbacks itself


def test_scores_expose_the_contract_fields(tmp_path, graph) -> None:
    sim = Simulation(SMOKE, run_dir=tmp_path / "sc", graph=graph)
    sim.run_to_completion(max_s=100)
    sim.close()
    score = sim.snapshot(mark_sent=False)["score"]
    assert {"separation_losses", "runway_conflicts", "deadlocks", "readback_errors_accepted", "completed", "avg_delay_s",
            "throughput_per_10min"} <= set(score)


def test_ground_vehicle_and_ga_aircraft_are_in_the_busy_scenario(tmp_path, graph) -> None:
    sim = Simulation(BUSY, run_dir=tmp_path / "veh", graph=graph)
    assert len(sim.scenario.vehicles) == 1 and any(f.airline == "GA" for f in sim.scenario.flights)
    veh = next(a for a in sim.aircraft.values() if a.kind == "vehicle")
    start = (veh.x, veh.y)
    for _ in range(600):
        sim.step()
    assert (veh.x - start[0]) ** 2 + (veh.y - start[1]) ** 2 > 100.0       # it drives
    sim.close()


def test_reset_mid_run_gives_a_fresh_tower_and_the_run_still_completes(tmp_path, graph) -> None:
    sim = Simulation(SMOKE, run_dir=tmp_path / "reset", graph=graph)
    while sim.t < 40:
        sim.step()
    sim.control("reset")
    sim.run_to_completion()
    assert sim.finished and sim.score()["completed"] == 6       # before the fix one departure stayed at its gate forever
    sim.close()

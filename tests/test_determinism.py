from __future__ import annotations

import hashlib
from pathlib import Path

from sim.core.engine import Simulation
from tests.conftest import HABOOB, SMOKE


def run_hash(scenario: Path, run_dir: Path, seed=None, graph=None) -> str:
    sim = Simulation(scenario, run_dir=run_dir, seed=seed, graph=graph)
    sim.run_to_completion()
    sim.close()
    assert sim.finished
    return hashlib.sha256((run_dir / "events.jsonl").read_bytes()).hexdigest()


def test_same_seed_gives_identical_event_log(tmp_path, graph) -> None:
    a = run_hash(SMOKE, tmp_path / "a" / "run", graph=graph)
    b = run_hash(SMOKE, tmp_path / "b" / "run", graph=graph)
    assert a == b


def test_same_seed_identical_with_weather_and_readback_errors(tmp_path, graph) -> None:
    a = run_hash(HABOOB, tmp_path / "a" / "run", graph=graph)
    b = run_hash(HABOOB, tmp_path / "b" / "run", graph=graph)
    assert a == b


def test_different_seed_changes_the_log(tmp_path, graph) -> None:
    a = run_hash(SMOKE, tmp_path / "a" / "run", seed=1, graph=graph)
    b = run_hash(SMOKE, tmp_path / "b" / "run", seed=2, graph=graph)
    assert a != b

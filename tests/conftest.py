from __future__ import annotations

from pathlib import Path
from typing import Any, List

import pytest

from sim.core.engine import Simulation
from sim.graph.model import AirportGraph

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "sim/scenarios/kphx_smoke.json"
HABOOB = ROOT / "sim/scenarios/kphx_haboob.json"


class NoopController:
    """Controller that never acts: lets tests drive the simulation tools by hand."""
    name = "noop"
    status = "idle"

    def on_tick(self, sim: Any, events: List[Any]) -> None:
        return None


@pytest.fixture(scope="session")
def graph() -> AirportGraph:
    return AirportGraph.load()


@pytest.fixture()
def manual_sim(tmp_path: Path, graph: AirportGraph) -> Simulation:
    sim = Simulation(SMOKE, run_dir=tmp_path / "manual", controller=NoopController(), graph=graph)
    for _ in range(3):
        sim.step()
    yield sim
    sim.close()

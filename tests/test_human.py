"""The Human controller: repeatable, slower than the tower by its reaction time, within its transmission budget,
missing wrong readbacks at its documented rate, and working from observe() and the public tools only."""
from __future__ import annotations

import hashlib
from typing import Any, List

import numpy as np

from sim.core.engine import Simulation
from sim.human import controller as human
from sim.human.controller import HumanController
from tests.conftest import BUSY, SMOKE

PUBLIC = {"observe", "issue_clearance", "resolve_readback", "set_runway_config", "record_decision",
          "graph", "choose_entry", "choose_exit", "runway_label", "runway_pavement"}


class ObserveOnly:
    """A simulation as a controller may see it: observe(), the airport graph and the tool methods. Anything else raises."""

    def __init__(self, sim: Simulation) -> None:
        self._sim = sim

    def __getattr__(self, name: str) -> Any:
        if name not in PUBLIC:
            raise AssertionError("controller read hidden simulator state: sim.%s" % name)
        return getattr(self._sim, name)


class Through:
    """Hands the wrapped controller an ObserveOnly view of the simulation."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.name, self.status = inner, inner.name, "idle"

    def on_tick(self, sim: Simulation, events: List[Any]) -> None:
        self.inner.on_tick(ObserveOnly(sim), events)


def _run(path, run_dir, graph, ctl: Any = "human") -> Simulation:
    sim = Simulation(path, run_dir=run_dir, controller=ctl, graph=graph)
    sim.run_to_completion()
    sim.close()
    return sim


def test_human_runs_are_repeatable(tmp_path, graph) -> None:
    a = _run(BUSY, tmp_path / "a" / "run", graph)
    b = _run(BUSY, tmp_path / "b" / "run", graph)
    assert a.controller_name == "human" and a.finished
    digest = [hashlib.sha256((p / "events.jsonl").read_bytes()).hexdigest() for p in (tmp_path / "a" / "run", tmp_path / "b" / "run")]
    assert digest[0] == digest[1]
    assert a.referee_report()["verdict"] in ("PASS", "FAIL")


def test_human_sees_only_observe_and_the_tools(tmp_path, graph) -> None:
    for name, ctl in (("human", HumanController()), ("tower", None)):
        if ctl is None:
            from sim.tower.controller import TowerController
            ctl = TowerController()
        sim = _run(BUSY, tmp_path / name, graph, Through(ctl))   # raises if either reads past observe()
        assert sim.finished and sim.score()["completed"] == 12


def test_reaction_delay_comes_before_every_instruction(tmp_path, graph) -> None:
    sim = Simulation(BUSY, run_dir=tmp_path / "r", controller="human", graph=graph)
    sim.run_to_completion()
    sim.close()
    sent = [e for e in sim.controller.log if e["event"] == "sent"]
    assert len(sent) > 20
    assert all(e["waited_s"] >= e["reaction_s"] - 1e-9 and e["reaction_s"] > 0.0 for e in sent)
    delays = [e["delay_s"] for e in sim.controller.log if e["event"] == "noticed" and not e["late"]]
    assert 1.8 < float(np.median(delays)) < 3.4                # about REACTION_MEDIAN_S
    # the tower, same traffic, answers sooner: the Human's instructions trail it
    tower = _run(BUSY, tmp_path / "t", graph, "tower")
    first = lambda s, act: min(e["sim_time_ms"] for e in s.log.events if e["kind"] == "clearance.issued" and e["data"]["action"] == act)
    assert first(sim, "pushback_approved") > first(tower, "pushback_approved")


def test_transmission_budget_queues_instructions(tmp_path, graph, monkeypatch) -> None:
    monkeypatch.setattr(human, "TX_PER_MIN", 3)
    sim = Simulation(BUSY, run_dir=tmp_path / "cap", controller="human", graph=graph)
    sim.run_to_completion()
    sim.close()
    issued = sorted(e["sim_time_ms"] / 1000.0 for e in sim.log.events if e["kind"] == "clearance.issued")
    worst = max(sum(1 for u in issued if t <= u < t + human.TX_WINDOW_S) for t in issued)
    assert worst <= 3
    assert any(e["event"] == "frequency_busy" for e in sim.controller.log)   # the cap actually made some wait


class _Rec:
    def __init__(self) -> None:
        self.calls: List[Any] = []

    def resolve_readback(self, rid: str, ok: bool) -> None:
        self.calls.append((rid, ok))

    def record_decision(self, **kw: Any) -> None:
        pass


def test_wrong_readbacks_missed_at_the_documented_rate() -> None:
    h = HumanController(seed=5)
    h.rng = np.random.default_rng([human.RNG_TAG, 5])
    n = 4000
    for i in range(n):
        h.expected["c-%d" % i] = "25R"
    sim = _Rec()
    obs = {"t": 1.0, "pending_readbacks": [{"id": "rb-%d" % i, "callsign": "X%d" % i, "clearance_id": "c-%d" % i,
                                            "text": "Roger", "parsed": {"runway": "25L"}} for i in range(n)]}
    h._readbacks(sim, obs)  # type: ignore[arg-type]
    accepted = sum(1 for _, ok in sim.calls if ok)
    assert len(sim.calls) == n                                   # each readback answered exactly once
    assert abs(accepted / n - human.MISS_WRONG_READBACK_P) < 0.025
    assert h.readbacks_missed == accepted and h.readbacks_caught == n - accepted

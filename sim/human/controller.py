"""A human tower controller: the built-in tower's rulebook, worked by a person with human limits.

The Human decides exactly what the tower would, from observe() alone, but it:
  - acts only after a reaction time, once the need for an instruction has lasted that long (occasionally much later);
  - has a transmission budget per minute, so in busy spells instructions wait their turn on the frequency;
  - keeps extra spacing above the tower's minimums;
  - now and then misses a wrong readback (hears what it expected) and lets it stand.
Every number is in the block below with its source; docs/HUMAN_MODEL.md has the full table. Its random draws come from
its own generator, seeded from the observed level seed, so a run is repeatable and the world's RNG is never touched.
"""
from __future__ import annotations

import copy
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np

from sim.core.engine import Simulation
from sim.tower.controller import DECIDE_EVERY_TICKS, TowerController

# -- human factors (sources in docs/HUMAN_MODEL.md) ---------------------------------------------------------------------
REACTION_MEDIAN_S = 2.5         # perception-reaction time, median (AASHTO Green Book design value 2.5 s; assumption for ATC)
REACTION_SIGMA = 0.4            # lognormal spread of that time (assumption: about 1.3-4.8 s for the middle 90%)
REACTION_MAX_S = 8.0            # a single reaction never takes longer than this (assumption)
LATE_P = 0.05                   # chance an instruction is late: attention was elsewhere (assumption)
LATE_EXTRA_S = (5.0, 15.0)      # how much later, uniform (assumption)
TX_PER_MIN = 10                 # fresh instructions per rolling minute on one frequency (assumption: ~6 s per exchange)
TX_WINDOW_S = 60.0
EXTRA_DEP_GAP_S = 15.0          # extra seconds between departures above the tower's wake/one-minute gap (assumption)
EXTRA_GUARD_S = 10.0            # extra seconds of landing guard before line-up, takeoff and crossings (assumption)
MISS_WRONG_READBACK_P = 0.2     # chance a wrong readback is not caught (hearback error; assumption, see the doc)
RNG_TAG = 7349                  # mixes into the seed so this stream never equals the world's


class _NotYet(Exception):
    """The Human is not ready to transmit this one yet (still reacting, or the frequency budget is used up)."""


class HumanController(TowerController):
    name = "human"
    status = "idle"

    def __init__(self, seed: int = 0) -> None:
        super().__init__()
        self._seed = seed
        self.rng: Optional[np.random.Generator] = None
        self.sent: Deque[float] = deque()                     # times of instructions transmitted (rolling minute)
        self.want: Dict[Tuple[str, str], Tuple[float, float]] = {}   # (callsign, action) -> (first wanted, ready at)
        self._wanted_now: set = set()
        self._kept: Optional[Tuple[Any, ...]] = None
        self._t = 0.0
        self.log: List[Dict[str, Any]] = []                  # what the Human did and why it waited (for tests)
        self.readbacks_missed = 0
        self.readbacks_caught = 0

    # -- one pass ---------------------------------------------------------------------------------------------------
    def on_tick(self, sim: Simulation, events: List[Dict[str, Any]]) -> None:
        if self.rng is None:
            lvl = sim.observe().get("level") or {}
            self.rng = np.random.default_rng([RNG_TAG, int(lvl.get("seed", self._seed))])
        self._wanted_now = set()
        super().on_tick(sim, events)
        if self._ticks % DECIDE_EVERY_TICKS == 0:   # after a decision pass: a need that went away resets its reaction clock
            self.want = {k: v for k, v in self.want.items() if k in self._wanted_now}

    def _arrival(self, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any]) -> None:
        self._guarded(super()._arrival, sim, obs, a)

    def _departure(self, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any]) -> None:
        self._guarded(super()._departure, sim, obs, a)

    def _ground_probe(self, sim: Simulation, by_cs: Dict[str, Dict[str, Any]], busy: set, obs_t: float) -> None:
        keep = (dict(self.ground_hold), dict(self.hold_since), dict(self.released))
        try:
            super()._ground_probe(sim, by_cs, busy, obs_t)
        except _NotYet as e:        # the scan stops at the instruction it could not send; it is planned again next pass
            cs = e.args[0]
            for now, old in zip((self.ground_hold, self.hold_since, self.released), keep):
                now.pop(cs, None)
                if cs in old:
                    now[cs] = old[cs]

    def _unjam(self, sim: Simulation, obs: Dict[str, Any], by_cs: Dict[str, Dict[str, Any]], busy: set) -> None:
        keep = dict(self.unjammed)
        try:
            super()._unjam(sim, obs, by_cs, busy)
        except _NotYet:
            self.unjammed = keep

    def _note(self, cs: str) -> None:
        self._kept = (cs, copy.deepcopy(self.assigned.get(cs)), dict(self.last_arrival_slot), self.taxi_wait.get(cs))

    def _guarded(self, fn: Any, sim: Simulation, obs: Dict[str, Any], a: Dict[str, Any]) -> None:
        """Run the tower's decision for one aircraft; if the Human cannot transmit yet, undo what that decision noted
        since its last instruction actually sent."""
        cs = a["callsign"]
        self._note(cs)
        try:
            fn(sim, obs, a)
            return
        except _NotYet:
            _, st, slots, tw = self._kept
            if st is None:
                self.assigned.pop(cs, None)
            else:
                self.assigned[cs] = st
            self.last_arrival_slot = slots
            if tw is None:
                self.taxi_wait.pop(cs, None)
            else:
                self.taxi_wait[cs] = tw
        finally:
            self._kept = None

    # -- transmitting --------------------------------------------------------------------------------------------------
    def _issue(self, sim: Simulation, a: Dict[str, Any], action: str, params: Dict[str, Any], summary: str, facts: List[str],
               rules: List[str], alternatives: List[Dict[str, str]], review_s: float) -> Optional[Dict[str, Any]]:
        assert self.rng is not None
        t = self._t
        key = (a["callsign"], action)
        self._wanted_now.add(key)
        if key not in self.want:
            delay = min(float(self.rng.lognormal(np.log(REACTION_MEDIAN_S), REACTION_SIGMA)), REACTION_MAX_S)
            late = bool(self.rng.random() < LATE_P)
            if late:
                delay += float(self.rng.uniform(*LATE_EXTRA_S))
            self.want[key] = (t, t + delay)
            self.log.append({"t": t, "callsign": a["callsign"], "action": action, "event": "noticed", "delay_s": delay, "late": late})
        if t < self.want[key][1]:
            raise _NotYet(a["callsign"])
        while self.sent and t - self.sent[0] >= TX_WINDOW_S:
            self.sent.popleft()
        if len(self.sent) >= TX_PER_MIN:
            self.log.append({"t": t, "callsign": a["callsign"], "action": action, "event": "frequency_busy"})
            raise _NotYet(a["callsign"])
        res = super()._issue(sim, a, action, params, summary, facts, rules, alternatives, review_s)
        self.sent.append(t)
        first, ready = self.want.pop(key)
        self.log.append({"t": t, "callsign": a["callsign"], "action": action, "event": "sent", "waited_s": t - first,
                         "reaction_s": ready - first})
        if self._kept is not None and self._kept[0] == a["callsign"]:
            self._note(a["callsign"])          # sent: a later instruction in the same decision must not undo it
        return res

    # -- conservative spacing -----------------------------------------------------------------------------------------
    def _spacing_ok(self, sim: Simulation, st: Dict[str, Any], a: Dict[str, Any], t: float) -> bool:
        return super()._spacing_ok(sim, st, a, t - EXTRA_DEP_GAP_S)

    def _landing_due(self, obs: Dict[str, Any], pav: str, within_s: float, me: str) -> Optional[Dict[str, Any]]:
        return super()._landing_due(obs, pav, within_s + EXTRA_GUARD_S, me)

    # -- hearing readbacks --------------------------------------------------------------------------------------------
    def _readbacks(self, sim: Simulation, obs: Dict[str, Any]) -> None:
        self._t = obs["t"]
        assert self.rng is not None
        rest = []
        for rb in obs["pending_readbacks"]:
            want = self.expected.get(rb["clearance_id"])
            if want is not None and rb["parsed"].get("runway") != want:
                if self.rng.random() < MISS_WRONG_READBACK_P:     # heard what it expected to hear
                    self.readbacks_missed += 1
                    sim.resolve_readback(rb["id"], True)
                    continue
                self.readbacks_caught += 1
            rest.append(rb)
        super()._readbacks(sim, dict(obs, pending_readbacks=rest))   # accepted or corrected as the tower does

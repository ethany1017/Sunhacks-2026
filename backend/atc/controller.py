"""Our controller (the Agent). The simulator calls on_tick(sim, events) every tick once it is plugged in.

It never reads `sim` or the raw sim events: every decision is made from the simif adapter's controller-safe views
(get_state / get_readbacks) and every instruction goes through the planner's safety-checked proposals.
"""
from .planner import Planner

DECIDE_EVERY_TICKS = 5   # 2 Hz decision pass (a tick is 0.1 sim s)


class Controller:
    name = "agent"

    def __init__(self, si=None, emit=None):
        self.status = "idle"
        self.si = si
        self.emit = emit or (lambda *a, **k: None)
        self.planner = None
        self._n = 0

    def on_tick(self, sim, events):
        if self.si is None:
            return
        self._n += 1
        if self.planner is None:
            self.planner = Planner(self.si, self.emit)
        if self._n % DECIDE_EVERY_TICKS:
            if getattr(self.si, "readback_waiting", lambda: False)():
                self.planner.answer_readbacks(self.si.get_state())   # answered on the tick they arrive
            return
        self.status = "planning"
        self.planner.tick(self.si.get_state())

"""Our controller (the Agent). The simulator calls on_tick(sim, events) every tick once it is plugged in.

It never reads `sim` or the raw sim events: every decision is made from the simif adapter's controller-safe views
(get_state / get_readbacks) and every instruction goes through the planner's safety-checked proposals.

A4: at the planner's key decisions (runway_flow, arrival_runway, departure_gap) Claude picks one of the planner's valid
options through `reasoning.Advisor`, which never blocks: None while Claude answers (the planner waits a pass), the
deterministic default after the 3 s timeout, and the default at once with no API key or no running event loop (the
headless harness). The choice is then proposed and safety-checked exactly like the default would be.
"""
from .planner import Planner
from .reasoning import Advisor

DECIDE_EVERY_TICKS = 5   # 2 Hz decision pass (a tick is 0.1 sim s)
PROCEDURES = {
    "runway_flow": "FAA JO 7110.65 3-5-1: use the runway most nearly into the wind. Pilots refuse a tailwind above the "
                   "level's limit (Unable). Turning the airport round costs a gap in the arrival stream.",
    "arrival_runway": "FAA JO 7110.65 3-10: assign the arrival runway with the shortest slot wait and the fewest runway "
                      "crossings to the gate; keep an aircraft on the final approach control sequenced it onto.",
    "departure_gap": "FAA JO 7110.65 3-9: departures normally go first come, first served at a runway entry; wake "
                     "separation behind the previous departure is applied by the planner either way.",
}


class Controller:
    name = "agent"

    def __init__(self, si=None, emit=None, advisor=None):
        self.status = "idle"
        self.si = si
        self.emit = emit or (lambda *a, **k: None)
        self.planner = None
        self.advisor = advisor or Advisor()   # reads ANTHROPIC_API_KEY once
        self._n = 0

    def on_tick(self, sim, events):
        if self.si is None:
            return
        self._n += 1
        if self.planner is None:
            self.planner = Planner(self.si, self.emit)
            self.planner.choose = self.choose
        if self._n % DECIDE_EVERY_TICKS:
            if getattr(self.si, "readback_waiting", lambda: False)():
                self.planner.answer_readbacks(self.si.get_state())   # answered on the tick they arrive
            return
        self.status = "planning"
        self.planner.tick(self.si.get_state())

    def choose(self, decision):
        """The planner's `choose` hook: an option id, or None while Claude is still answering (re-checked next pass)."""
        options, default, kind = decision["options"], decision["default"], decision["kind"]
        if len(options) < 2:
            return default   # nothing to choose: no Claude call, no event
        # a departure gap is one decision for the whole queue, whichever queued aircraft asks
        ask = dict(decision, callsign=None) if kind == "departure_gap" else decision
        res = self.advisor.choose(ask, self._situation(), PROCEDURES.get(kind, ""))
        if res is None:
            return None
        choice = res["choice"] if res["choice"] in {o["id"] for o in options} else default
        if res["first"]:   # one ai_decision per decision (the Advisor reuses an answer for ANSWER_TTL_S)
            cs = choice if kind == "departure_gap" else decision.get("callsign")
            self.emit(self.planner.t, "ai_decision", cs, {
                "kind": kind, "options": [{"id": o["id"], "label": o.get("label", o["id"])} for o in options],
                "default": default, "choice": choice, "explanation": res["explanation"], "source": res["source"]},
                res["explanation"])
        return choice

    def pending(self):
        """True while a Claude answer is outstanding (the headless harness with CLAUDE_IN_HARNESS=1 waits on it)."""
        return any(hasattr(v, "done") and not v.done() for v in self.advisor.answers.values())

    def _situation(self):
        obs = self.planner.obs
        cfg, w = obs["runway_config"], obs["weather"]
        kinds = [a["kind"] for a in obs["aircraft"]]
        return ("Sim time %.0f s. METAR: %s. Arrival runways %s, departure runways %s. %d arrivals and %d departures "
                "in the picture." % (obs["t"], w.get("metar") or "n/a", ", ".join(cfg["arrival_runways"]),
                                     ", ".join(cfg["departure_runways"]), kinds.count("arrival"), kinds.count("departure")))

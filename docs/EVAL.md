# Evaluation harness (A2)

`backend/app/evaluate.py` runs controllers (`human`, `agent`) through levels and seeds headless, as fast as the
simulator steps, in parallel worker processes. Each run is the backend's own loop (`Runner.run_headless`) with a
throwaway event store; PASS/FAIL, the fails and the score come only from the Referee (`simif` `metrics()["referee"]`).

```
cd backend
.venv/bin/python -m app.evaluate                                   # human + agent, every level and practice scenario, training seeds
.venv/bin/python -m app.evaluate --levels levels                   # Normal Day + Light Rain only
.venv/bin/python -m app.evaluate --levels practice                 # practice scenarios only
.venv/bin/python -m app.evaluate --controllers human --levels normal_day --seeds 1,2
.venv/bin/python -m app.evaluate --seeds heldout                   # final grading only; prints a note
... --workers 4 --out /path/results.json --publish
```

Output: a table per section (training seeds, held-out seeds, practice scenarios), the fails of each run (rule,
callsigns, reason, time; first 5 per run, all of them in the JSON), and totals per controller (pass rate, fails by
rule, average delay, go-arounds, diversions, readback errors caught/missed). Full results go to
`backend/eval_results/latest.json` (not committed). `--publish` merges the per-section totals into
`backend/eval_results/summary.json` (committed, small) and writes a `metrics` event `{comparison: summary}` into the
dashboard's current run; every new run starts with the saved one. The dashboard shows it as "Agent vs Human".

**Held-out seeds** (`sim/levels.py HELD_OUT_SEEDS`) run only with `--seeds heldout`. An explicit seed list that contains
a held-out seed is refused. Tune on training seeds and practice scenarios only.

## Practice scenarios

Run by id (`--levels busy_arrivals`), all with `--levels practice`. Scenario files are in `backend/practice/` (the
simulator's scenario format; scenario files always have calm, static weather).

| id | what it exercises | how |
|---|---|---|
| `busy_arrivals` | 10 arrivals about 110 s apart on both finals: sequencing, landing clearances, exits, taxi-in | scenario file |
| `departure_push` | 10 departures ready inside 90 s plus 2 arrivals: pushback order, taxi conflicts, departure spacing | scenario file |
| `runway_crossing` | 5 arrivals on 25L must cross 25R while 5 departures use it: hold short, crossing vs takeoff timing | scenario file |
| `wrong_readback` | 8 flights, `readback_error_rate` 0.5: catch and reject wrong readbacks | scenario file |
| `runway_config_change` | the wind shift to 090/14 forces west to east flow (at 330 s) | Normal Day, seed 1, 600 s day |
| `rain_peak` | rain spacing, wet runway occupancy, slow taxi, rain go-arounds (plus the wind shift) | Light Rain, seed 21, 750 s day |
| `smoke`, `busy` | the original 6 and 12 aircraft scenarios | scenario files in `sim/scenarios/` |

Not exact without a simulator change: a scenario file cannot carry rain or a wind shift, and a level cannot start
mid-day or be made denser than its real hourly rates. So `runway_config_change` is a short Normal Day (the shift lands at
55% of the day) rather than a window around the shift, and `rain_peak` is a short Light Rain day at the level's normal
demand rather than a denser-than-real peak.

## Claude at key decisions (A4): how to wire it

`backend/atc/reasoning.py` is built and tested (`tests/test_reasoning.py`, fake client) but not wired into the planner
yet. The planner already builds key decisions as `{kind, options: [{id, label}], default}` and calls
`self.choose(decision)` (runway flow change in `_flow`, which departure takes a gap in `_takeoff`). Wiring is:

```python
from .reasoning import Advisor
self.advisor = Advisor()      # Planner.__init__; reads ANTHROPIC_API_KEY once
self.choose = self._choose    # instead of `lambda decision: decision["default"]`

def _choose(self, decision):
    res = self.advisor.choose(decision, situation=..., procedures=...)   # never blocks
    if res is None:           # Claude still answering (at most 3 s): act on nothing this pass, re-checked in 0.5 s
        return None
    if res["first"]:          # one ai_decision event per decision, not one per re-check
        self.emit(self.t, "ai_decision", decision.get("callsign"), {
            "kind": decision["kind"], "options": decision["options"], "default": decision["default"],
            "choice": res["choice"], "explanation": res["explanation"], "source": res["source"]}, res["explanation"])
    return res["choice"]
```

`None` needs no change at either call site: both act only when the choice is theirs. The `ai_decision` event comes
before the proposal it leads to, and the dashboard shows the following `safety_check` on the decision card. The chosen
option still goes through the safety engine. With no key, or no running event loop (this headless harness),
`Advisor.choose` returns the default at once with source "fallback", so harness scores are unchanged by the wiring.
To pass decisions with a callsign (departure gap), add `"callsign": cs` to the decision dict.

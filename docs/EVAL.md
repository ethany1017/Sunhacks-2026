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

## Claude at key decisions (A4)

Wired in `backend/atc/controller.py`: `Controller.choose` is the planner's `choose` hook and asks
`backend/atc/reasoning.py`'s `Advisor` (tests: `backend/tests/test_reasoning.py`, `backend/tests/test_a4.py`, fake
clients). The planner builds each key decision as `{kind, options: [{id, label}], default, callsign}`: `runway_flow`
(turn the airport round on a tailwind, in `_flow`), `arrival_runway` (which arrival runway, in
`_assign_arrival_runway`) and `departure_gap` (which queued departure lines up next, in `_line_up`).

- Claude picks one of the offered option ids and gives a one-line summary (JSON). An id outside the list, invalid
  JSON, an API error or no answer within 3 s gives the planner's default with source "fallback".
- The hook never blocks: while Claude is answering it returns None and the planner does nothing for that decision
  this pass (re-checked 0.5 s later). The chosen option is then proposed and safety-checked exactly like the default.
- One `ai_decision` event per decision `{kind, options, default, choice, explanation, source}`, before the proposal
  it leads to (callsign: the arrival for `arrival_runway`, the chosen departure for `departure_gap`, none for
  `runway_flow`). The same decision re-checked every pass reuses its answer (no new event) for 30 s wall clock, then
  is asked again. A decision with a single option is not a decision: the default, no call, no event.
- A `departure_gap` is one decision for the whole queue, whichever queued departure asks, so every aircraft at the
  entry sees the same choice.
- No `ANTHROPIC_API_KEY`: the default at once, source "fallback", explanation "Planner default: ... (no Claude API
  key)". The run is identical to the planner alone.
- The harness (`Runner.run_headless`, so `python -m app.evaluate` and the tests) runs without an event loop, so it
  always takes the deterministic default, key or no key: scores are repeatable and grading never touches the
  network. `CLAUDE_IN_HARNESS=1` runs headless days on an event loop with Claude choosing; sim time is held while an
  answer is pending (at most 3 s per decision), so such runs are slow and depend on Claude's answers.
- The backend reads the key from the process environment: `run_demo.sh` loads `.env`; a hand-started uvicorn needs
  it exported first.

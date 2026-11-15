# Tower Zero: an AI tower controller for a simulated Phoenix Sky Harbor

SunHacks 2026, by Ethan and Gus.

> **SIMULATION ONLY.** Every aircraft, pilot, radio call and controller here is simulated. Nothing connects to real
> air traffic control, radios or aircraft.

A realistic KPHX simulator runs two days of traffic: **Normal Day** (an average controller can keep up) and **Light
Rain** (lower visibility, longer runway occupancy and bigger spacing; an average controller can not). Two controllers
can run it, and you can switch between them mid-run: a **Human** model of an average tower controller (the simulator's
rule-based tower worked with human limits: reaction time, a workload cap, extra spacing, missed wrong readbacks), and
our **Agent**. An independent **Referee** inside the simulator watches the true state and calls PASS or
FAIL. The goal: the Agent passes both days and scores better than the Human.

## Tech stack

React, TypeScript and Vite; Tailwind CSS and shadcn/ui; MapLibre GL JS for the live map. Python and FastAPI backend,
SQLite event store, WebSockets to the dashboard. Claude API (reasoning, optional), a deterministic Python safety engine,
NOAA Aviation Weather API (live METAR, display only). ElevenLabs and faster-whisper voice are planned, not built yet.

## Architecture

```
            sim/ (simulator, in-process)                          backend/ (FastAPI, port 8100)
  traffic · physics · pilots · readbacks · weather       app/runner.py steps the sim in real time
  Referee (sim/referee/): PASS / FAIL, score    <---->   simif/ adapter  <--  atc/ Agent: planner -> safety -> issue
                                                         app/db.py SQLite events  --WS /ws-->  frontend/ (port 5180)
```

Three layers, never merged:

1. **Agent safety engine** (`backend/atc/safety.py`): every clearance the Agent proposes is checked by deterministic
   rules (runway occupancy, arrival/departure and wake spacing, taxi conflicts, hold short, tailwind limit, rain
   spacing) before it is sent. A rejected proposal is never sent; Claude can not override it.
2. **Simulator physics** (`sim/`): what actually happens to the aircraft.
3. **Referee** (`sim/referee/`): judges the outcome from the true state, whoever is controlling. It never imports
   agent code.

The Agent (`backend/atc/planner.py`) sequences arrivals, slots departures into gaps, routes taxiing on the airport graph
(Dijkstra, avoiding head-on conflicts), checks every pilot readback against the clearance and turns the airport round
when the wind shifts. Every proposal, safety check, clearance and readback is stored with a plain-English reason and
shown in the dashboard's feed. The Agent only talks to the simulator through `backend/simif/` and only sees what the
Human sees (the sim's `observe()`).

The dashboard is read-only. It can only pick a level and a controller, start or reset a run, and switch controller
mid-run. The backend keeps running with the dashboard closed.

**Claude** (`backend/atc/reasoning.py`) can pick among the planner's already-valid options at key decisions (runway
flow change, which departure takes a gap) and give a one-line summary. Any failure (no key, ~3 s timeout, API error,
bad answer) falls back to the planner's default. It is built and tested but not yet wired into the planner, so today's
runs are fully deterministic.

**Live weather** (`backend/atc/weather_live.py`): the real KPHX METAR from aviationweather.gov, refreshed every 5
minutes and cached in `backend/weather_cache/`. It is shown in the top bar as "Live KPHX" and never drives decisions:
the Agent flies the level's simulated weather.

## Run it

Needs Python 3.11 (`backend/.venv`) and Node 20+ (`frontend/node_modules`).

```bash
python3.11 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
(cd frontend && npm install)
cp .env.example .env        # optional keys, see below
./run_demo.sh               # backend :8100 + dashboard :5180, opens the browser; Ctrl-C stops both
```

Other ports: `BACKEND_PORT=8111 FRONTEND_PORT=5191 ./run_demo.sh`; `NO_BROWSER=1` skips opening the browser. If a
port is taken the script says which process holds it and stops (it never kills anything). The backend starts Normal
Day with the Agent on its own (`AUTOSTART=0` to disable); `SIM_SPEED=4` runs the sim 4x real time. `run_demo.sh`
loads `.env`.

Tests and the evaluation harness (details in [docs/EVAL.md](docs/EVAL.md)):

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/python -m app.evaluate --levels levels                 # Human + Agent, Normal Day + Light Rain, training seeds
.venv/bin/python -m app.evaluate --levels practice               # practice scenarios
.venv/bin/python -m app.evaluate --seeds heldout                 # final grading only
cd ../frontend && npm run build
```

## .env

Copy `.env.example` to `.env` (gitignored). Every key is optional; each service has an offline fallback.

| Variable | Used for | Without it |
|---|---|---|
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Claude picking among planner options (default model `claude-haiku-5-5`) | planner default, source "fallback" |
| `ELEVENLABS_*` | voices (planned) | - |

NOAA needs no key; without network the top bar shows the last cached METAR. If the map tiles fail, the map falls back
to a plain dark background with the airport layers.

## Levels, the Human and the Referee

- [docs/LEVELS.md](docs/LEVELS.md): both days, seeds (training vs held-out), traffic from BTS PHX data, rain effects,
  and the calibration. Levels, Referee and fail limits are frozen as `levels-v1`.
- [docs/HUMAN_MODEL.md](docs/HUMAN_MODEL.md): the Human controller's reaction time, workload limit, extra spacing and
  missed wrong readbacks, with the source or the stated assumption behind each number.
- [sim/referee/limits.py](sim/referee/limits.py): every Referee limit with its source (FAA JO 7110.65 separation, wake
  categories, runway rules).

The Referee fails a run on any collision, loss of separation, two aircraft on a runway, runway incursion, diversion,
stuck aircraft or gridlock, or traffic still unhandled at the end of the day. PASS means a whole day with zero fails.
Delay, go-arounds, diversions, throughput and readback errors caught vs missed make up the score.

## Results

**Final grading (held-out seeds): not run yet.** To be filled from `python -m app.evaluate --seeds heldout`.

| Level | Controller | Pass | Fails | Avg delay | Diversions |
|---|---|---|---|---|---|
| Normal Day | Human | TBD | TBD | TBD | TBD |
| Normal Day | Agent | TBD | TBD | TBD | TBD |
| Light Rain | Human | TBD | TBD | TBD | TBD |
| Light Rain | Agent | TBD | TBD | TBD | TBD |

Training seeds, in progress (from [docs/TRAINING_LOG.md](docs/TRAINING_LOG.md), deterministic agent, commit 442d8a7):
Agent Normal Day 4/5 pass, Light Rain 0/5, practice 6/8, zero safety fails on all 18 runs. Human on the same levels:
Normal Day 5/5, Light Rain 1/5, practice 6/8.

## Repository

- `backend/`, `frontend/`: the demo (our backend, agent and dashboard).
- `sim/`: the simulator, levels, Human and Referee.
- `api/`, `web/`: Gus's original simulator viewer (`make demo`, ports 8000/5173), kept for simulator work.
- `agent/`: an earlier Claude agent, parked; not used by the demo.
- `data/`, [data-catalog.md](data-catalog.md): data sources.

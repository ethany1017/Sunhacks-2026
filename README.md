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
NOAA Aviation Weather API (live METAR, display only). ElevenLabs voices for the tower and the pilots, with macOS `say`
as the offline fallback. faster-whisper readback recognition (phase A8) is not built: readbacks are checked from the
simulator's readback text.

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

The Agent (`backend/atc/planner.py`) sequences arrivals, slots departures into gaps, routes taxiing on the airport graph,
probes for ground conflicts and holds or re-routes aircraft, checks every pilot readback against the clearance, turns
the airport round when the wind shifts and opens a second departure runway late in the day. Its procedures are the
same rule base as the simulator's built-in tower (which is also the base of the Human model), without the human limits,
plus our own fixes (see [docs/TRAINING_LOG.md](docs/TRAINING_LOG.md)); what makes it ours is the independent safety
engine in front of every instruction and the training rounds measured on seeds it was not tuned on. Every proposal, safety check, clearance and readback is stored with a plain-English reason and
shown in the dashboard's feed. The Agent only talks to the simulator through `backend/simif/` and only sees what the
Human sees (the sim's `observe()`).

The dashboard is read-only. It can only pick a level and a controller, start or reset a run, and switch controller
mid-run. The backend keeps running with the dashboard closed.

**Claude** (`backend/atc/reasoning.py`) can pick among the planner's already-valid options at key decisions (runway
flow change, which departure takes a gap) and give a one-line summary. Any failure (no key, ~3 s timeout, API error,
bad answer) falls back to the planner's default, and the choice still goes through the safety engine. Without a key the
dashboard shows these decisions with a "Fallback" badge. The evaluation harness always uses the deterministic default
(unless `CLAUDE_IN_HARNESS=1`), so scores are repeatable and never depend on the network. A real Claude answer has not
been verified on this machine (no API key was available during the build).

**Voice** (`backend/atc/voice_out.py`): every clearance and readback is spoken (tower voice and two or three pilot
voices), cached by a hash of voice and text under `backend/audio_cache/`. ElevenLabs is used when a key is set
(unverified: no key was available), otherwise macOS `say`. `VOICE=0` turns it off. At the 10x demo pace the dashboard
plays roughly one line in eight: only the newest clearance and its own readback, so speech starts about 10 sim seconds
after the event (a line itself lasts about 48 sim seconds), and it stops on a reset. The map is satellite imagery
(USGS); without network it falls back to the dark airport map.

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
Day with the Agent on its own (`AUTOSTART=0` to disable). `run_demo.sh` loads `.env` and runs the sim at 10x real time
by default (`SIM_SPEED`; a day then takes about four minutes). The dashboard has no seed control: a run started from it
uses the level's first training seed, or the seed in `DEMO_SEED_NORMAL_DAY` / `DEMO_SEED_LIGHT_RAIN` (held-out seeds
are refused). The click-by-click demo is in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

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
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_*` | tower and pilot voices | cached audio, else macOS `say` |
| `VOICE`, `SIM_SPEED`, `DEMO_SEED_*` | voice on/off, sim pace, demo seeds | voice on, 10x in `run_demo.sh`, first training seed |

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

All numbers are the simulator Referee's, from `python -m app.evaluate`, agent at commit `639b0ac`, deterministic
(no Claude key, see above). PASS means a whole day with zero Referee fails.

**Final grading: held-out seeds** (normal_day 9001-9003, light_rain 9021-9023; never used for tuning; three days per
level, so each day is a third of the score):

| Level | Controller | Days passed | Referee fails | Avg delay | Diversions |
|---|---|---|---|---|---|
| Normal Day | Human | 2/3 | 2 (collisions) | 102 s | 0 |
| Normal Day | Agent | 2/3 | 1 (collision) | 90 s | 0 |
| Normal Day | plain tower rules | 2/3 | 1 (collision) | 82 s | 0 |
| Light Rain | Human | 1/3 | 2 (stuck, unhandled at day end) | 224 s | 0 |
| Light Rain | Agent | **3/3** | **0** | 191 s | 0 |
| Light Rain | plain tower rules | 1/3 | 2 (stuck, unhandled at day end) | 194 s | 0 |

- **Light Rain: the goal is met.** The Agent passes all three held-out days with zero fails and lower delay than the
  Human, who passes one.
- **Normal Day: the goal is not met.** The Agent has fewer fails and lower delay than the Human, but it does not pass
  all three days. On seed 9003 an arriving A320 assigned gate B16 clips a GA aircraft standing at the neighbouring
  stand PB16 as it turns into the gate. The Human and the plain tower rules collide with the same aircraft on the same
  day. Stand pairs like this share one lane node in the simulator's airport layout; we found no controller instruction
  that avoids it, and the simulator is frozen, so we did not touch it.
- "Plain tower rules" is the simulator's built-in rule-based tower without the Human's limits. The Agent's procedures
  started from the same rule base, so that row shows what the Agent adds: on Light Rain two more days passed.

**Wider picture: 30 validation days per controller** (normal_day 41-55, light_rain 61-75; never tuned on, used only to
accept or reject changes):

| Level | Human | Agent | plain tower rules |
|---|---|---|---|
| Normal Day | 7/15 passed, avg delay 88 s | 7/15 passed, avg delay 77 s | 8/15 passed |
| Light Rain | 4/15 passed, avg delay 199 s | 6/15 passed, avg delay 181 s | 7/15 passed |

Read this table before trusting any single demo run: every controller, including the Human the levels were calibrated
against, fails many unseen days in this simulator, mostly on ground gridlock ("stuck"), one aircraft still taxiing when
the day ends, and ground collisions. The Agent is not yet a reliable all-day controller; on these days it is level
with the Human on Normal Day and ahead on Light Rain, and behind the plain tower rules on the validation days of both
levels (and on Normal Day held-out delay, 90 s against 82 s).

**Training seeds** (normal_day 1-5, light_rain 21-25; the dashboard's Agent vs Human panel shows these and the held-out
grading): Normal Day Human 5/5 (avg delay 84 s), Agent 3/5 (69 s; two days end with an aircraft not handled);
Light Rain Human 1/5 (202 s; 4 collisions), Agent 3/5 (177 s; no safety fails). Practice scenarios: Human 6/8,
Agent 8/8. The levels were calibrated so that the Human passes the five Normal Day training seeds.

How the Agent got here, round by round, with every failure review and every rejected idea:
[docs/TRAINING_LOG.md](docs/TRAINING_LOG.md). The demo runs Light Rain seed 25 and Normal Day seed 3, chosen from the
training seeds as days where the two controllers differ; the tables above are the honest comparison.

## Repository

- `backend/`, `frontend/`: the demo (our backend, agent and dashboard).
- `sim/`: the simulator, levels, Human and Referee.
- `api/`, `web/`: Gus's original simulator viewer (`make demo`, ports 8000/5173), kept for simulator work.
- `agent/`: an earlier Claude agent, parked; not used by the demo.
- `data/`, [data-catalog.md](data-catalog.md): data sources.

# Build: Autonomous Airport Traffic Control Agent (SunHacks 2026)

Ethan is building a hackathon demo due Nov 15, 2026 at 11am. The goal is a
working, reliable live demo. Not a product. Keep it simple.
Repo: ethany1017/Sunhacks-2026, at ~/Sunhacks-2026.
Project name: [DECIDE — placeholder "Tower Zero"]

## Ownership
Gus built the simulator and is on a break. Ethan owns all the code. Anything
can be changed, including Gus's files, but simulator changes happen only on
the `sim` branch and agent changes only on the `agent` branch.

## Direction
1. Simulator (sim/): a realistic KPHX airport. It simulates the world only:
   aircraft, pilots, readbacks, weather, airport, traffic. It has two
   levels:
   - Normal Day: what a normal ATC worker handles on an average day.
   - Light Rain: rain causes delays and lower capacity. An average ATC
     worker can NOT keep up with all the traffic. Harder for the agent.
2. A Referee inside the simulator judges whoever is controlling and shows
   PASS or FAIL. It doesn't care who the controller is.
3. Two controllers, switchable:
   - Human: simulates an average ATC worker, based on real data (typical
     delays, workload limits, error rates).
   - Agent: our AI controller.
4. The goal: the agent passes BOTH levels and scores better than the Human.

## The golden rule: train the agent, never bend the simulator
- The simulator and the two levels are calibrated against the Human
  controller only (S5). After calibration they are FROZEN.
- Never change the simulator, levels, Referee, or fail rules to make the
  agent pass. If the agent fails, the agent gets better.
- The agent gets better through: better planning logic, real ATC procedures
  and separation standards (e.g. FAA JO 7110.65 wake and runway rules),
  data from the simulator, and a failure-review loop (A6).
- "Training" means improving the agent from data and measured results.
  Claude does not learn from runs; its knowledge comes from the rules,
  procedures, and data we give it.
- Tune on training seeds and practice scenarios. Final grading uses
  held-out seeds of both levels that were never used while tuning.

## What exists and how we treat it
- sim/ (core, graph, scenarios, weather, voice, metrics, tower): the
  simulator. Python 3.9 root .venv today. Changes only on the `sim` branch.
- sim/tower/controller.py: Gus's rule-based controller. Becomes the base
  of the Human controller (S4). Also a reference for timing rules.
- agent/ (top-level): Gus's parked Claude agent. Leave it alone, don't
  import it. Lessons only.
- api/ + web/: Gus's simulator viewer. Keep it for watching the sim during
  sim work. Not part of our demo.
- backend/ + frontend/: OUR backend and dashboard. This is the demo.

## Branch plan
- `main`: stable, merged work only.
- `sim`: simulator work (Track A).
- `agent`: our backend, agent (backend/atc/) and dashboard (Track B).
- Both start from main after Step 0 and run in parallel. Merge `sim` into
  `agent` whenever a sim change lands that the agent needs.
- Merge sim → main and agent → main when each track's phases pass. Final
  integration happens on main.
- Ask before creating extra worktrees. Never push without asking Ethan.

## Tech stack (fixed — don't substitute)
- Frontend: React, TypeScript, Vite
- UI design: Tailwind CSS, shadcn/ui
- Live traffic map: MapLibre GL JS
- Backend: Python, FastAPI
- AI reasoning: Claude API
- Safety engine: Python deterministic rule-based validation
- Voice recognition: faster-whisper
- Voice generation: ElevenLabs
- Weather data: NOAA Aviation Weather API (aviationweather.gov)
- Database: SQLite
- Real-time communication: WebSockets
- Development: VS Code, GitHub
- BluebirdATC will NOT be used.

## Architecture
A headless FastAPI backend runs the simulator in-process and plugs in the
chosen controller (Human or Agent), handling traffic planning, safety
validation, clearances, and communications. The React dashboard displays
aircraft, weather, decisions, alerts, the Referee's PASS/FAIL, and a
scoreboard over WebSockets. Claude assists the agent with reasoning and
explanations, while independent Python safety rules validate every agent
instruction before it is issued. The backend keeps running with the
dashboard closed.

The dashboard may only: pick a level, pick a controller, start/reset a run,
and switch controller mid-run. It never sends clearances or traffic
commands.

Three separate layers, never merged:
1. Agent safety engine: stops the agent from SENDING unsafe clearances.
2. Simulator physics: what actually happens to the planes.
3. Referee: independently judges the outcome, PASS or FAIL.

## Hard rules
- Simulation only. Never connect to real ATC, radios, or aircraft.
- Every clearance from our agent passes the safety engine first. Claude and
  the planner propose; the safety engine approves or rejects. No bypass.
  Claude never overrides a rejection.
- The Referee is independent: it never imports agent code and never trusts
  the controller's own report.
- Every decision, including rejections, is stored with a plain-English reason.
- Our agent talks to the sim only through backend/simif/.
- Every external service has a fallback so the demo can't die on stage:
  - Claude API fails or times out (~3s) → deterministic planner + template reason
  - ElevenLabs fails → cached audio, else macOS `say`
  - NOAA fails → last cached METAR
  - Map basemap fails → plain dark background with airport layers
- Secrets in `.env` (gitignored), with a committed `.env.example`.
- Only build what the current phase asks for. If something else seems needed, ask.
- One phase at a time. At the end of each phase: commit, stop, show what
  works, and wait for Ethan's go-ahead.

## Simulator Interface (backend/simif/)
In-process adapter: our backend owns the `Simulation`, steps it in our own
loop (one step = 0.1 sim s; we pace it), and plugs a controller in with
`sim.set_controller(obj)` (an object with `name`, `status`,
`on_tick(sim, events)`). `connect()` picks the real sim by default, the old
stub only via a flag (offline fallback, no more work on it).
- `get_airport()` → {config, geojson, graph}: ICAO "KPHX", map center,
  runways (both ends, designators, headings, lat/lon), taxiway graph, hold
  points, stands, runway crossings, final approach per runway end.
- `get_state()` → sim time + aircraft: callsign, arrival/departure, ICAO
  type, wake category, lat/lon, heading, speed, altitude, phase, gate,
  runway, current and planned route, hold point, ETA, fuel/holding time,
  current clearance. Use observe() for decisions; snapshot() only for
  metrics and the Referee.
- `issue(clearance)` → issue_clearance. Returns transmitted/error. The real
  acknowledgement is the readback.
- `get_readbacks()` / `resolve_readback()` → readback text linked to the
  clearance id; accept or reject.
- `set_runway_config()`, `get_level()`, `get_referee()`; events: spawn,
  departed, diverted, referee_fail, sim_end.
Document every field in backend/simif/README.md.

## Event contract (backend → dashboard)
SQLite `events` table, pushed over WebSocket `/ws`:
{"t": 123.4, "seq": 17, "type": "...", "callsign": "N123AB", "data": {...}, "reason": "plain English"}
Types: run_start (level, controller, seed), controller_switch, airport,
weather, runway_change, aircraft_spawn, position (~1/sec), proposal,
ai_decision (options, choice, explanation, source: "claude" | "fallback"),
safety_check (approved/rejected, rule, reason), clearance, audio, readback
(transcript, matched), readback_correction, sequence, alert, metrics,
referee_fail (rule, callsigns, reason), referee_result (PASS/FAIL, score),
sim_end.
On WebSocket connect: full history for the current run, then live events.

## Out of scope
Live traffic (ADS-B), historical replay of real traffic, gate/ramp
integration, multiple airports, user accounts, deployment, BluebirdATC,
the haboob/sandstorm, Gus's parked agent/.

---

# Step 0: Branch setup (do this now, then stop)
If I'm already on main with tower-zero merged in, skip the merge and just
run the checks on main.
1. Clean .env.example: remove stale comments (CONTROLLER=baseline, the
   duplicate "Copy to .env" header). One clear section per service.
2. Commit (including this CLAUDE.md).
3. Make sure tower-zero is merged into main. Run everything on main: our
   backend tests, Gus's sim tests, his agent tests, both frontend builds.
4. Create branches `sim` and `agent` from main.
5. Ask Ethan whether to push main now as a backup.
Report and stop.

---

# Track A — Simulator (branch: sim)

## S1: Remove the sandstorm + simulator fixes
1. Remove the haboob/sandstorm completely: scenarios, trigger_haboob,
   replay data, related controls and tests. Weather now comes from the
   level (S3).
2. taxi_via follows the full explicit node route.
3. Remove error_truth from readback events (don't leak which are wrong).
4. Observation noise gets its own random generator (repeatable runs).
5. Confirm or fix: an accepted wrong hold-short readback shouldn't make the
   pilot ignore every later hold; a wrong-phase clearance shouldn't stay
   "acknowledged" forever.
6. Find out why Gus's test count dropped from 68 to 53 and confirm nothing
   important was lost.
Done when: each change has a test, all sim tests pass, the tower still runs
the smoke scenario conflict-free. Merge sim → agent.

## S2: Referee (PASS / FAIL)
Part of the simulator, not a setting. Watches the true state (snapshot)
every tick, whoever is controlling.
1. FAIL (safety) — any one of these ends the day as FAIL:
   - Collision or crash (aircraft overlap, on the ground or in the air).
   - Loss of separation in the air (below standard radar/wake separation).
   - Two aircraft on the same runway at once when not allowed (landing on
     an occupied runway, takeoff roll with another aircraft on it).
   - Runway incursion: an aircraft enters a runway or crosses a hold point
     without a clearance.
2. FAIL (traffic not handled):
   - An aircraft is diverted because it held too long (holding/fuel limit).
   - An aircraft is stuck (no progress beyond a time limit) or a gridlock
     forms.
   - Traffic still unhandled when the day ends.
3. Delays don't fail a run on their own; they go in the score.
4. PASS = the whole day completes with zero fails.
5. Score (for comparing controllers): fails, total and average delay,
   go-arounds, diversions, runway throughput, readback errors caught vs.
   missed.
6. Emits referee_fail the moment something goes wrong (rule, callsigns,
   reason) and referee_result at the end.
Put all limits as named constants in one file with a comment on where each
number comes from. [DECIDE — Ethan reviews the limits before S5.]
Done when: unit tests force each fail type and the Referee catches it;
a clean tower run on the smoke scenario passes.

## S3: Levels
1. Normal Day: a compressed average KPHX controller day (configurable
   length; default ~20–30 min sim time): realistic arrival/departure volume
   with peaks and quiet periods, mixed aircraft types and wake categories,
   realistic readback error rate, one runway configuration change. Calm
   weather.
2. Light Rain: same day shape, plus rain effects: lower visibility, longer
   runway occupancy, larger required spacing, slower taxi and approach
   speeds, more go-arounds, and departure/arrival delays rippling through
   the day. Capacity drops below demand during peaks.
3. Both seeded and repeatable. Separate training seeds and held-out
   grading seeds for each level.
4. docs/LEVELS.md: every assumption and where the numbers come from.
Done when: both levels run end to end with the tower and produce a score.

## S4: Human controller (average ATC worker)
1. Built on Gus's tower controller plus realistic human factors: reaction
   time before acting, limited attention (a cap on how many aircraft get
   fresh instructions per minute), conservative extra spacing, occasional
   missed wrong readbacks and late instructions at realistic rates.
2. Calibrate those numbers from real data where possible: BTS on-time and
   delay data for PHX, FAA operations and delay statistics, published
   controller error rates. Where data isn't available, use a documented
   assumption. Record sources in docs/HUMAN_MODEL.md.
3. The Human never gets information the agent doesn't have (it uses
   observe(), same as the agent).
Done when: the Human runs both levels and produces a score.

## S5: Calibrate and freeze
1. Tune the LEVELS (not the Human, not the Referee) so that:
   - Normal Day: the Human PASSES, with typical real-world delays.
   - Light Rain: the Human can NOT keep up: delays pile up and it FAILS
     on traffic not handled (diversions/backlog), while still being a
     realistic day.
2. Show Ethan the Human's scores on both levels. After his OK, the levels,
   Referee, and fail limits are FROZEN. Record a version tag in
   docs/LEVELS.md. Any later change to them needs Ethan's explicit OK and a
   written reason that isn't "so the agent passes".
Done when: Ethan approves and the freeze is committed. Merge sim → agent.

---

# Track B — Agent (branch: agent)

## A1: Connect to the simulator + controller switch
1. simif adapter for the in-process sim. First check that numpy, networkx,
   pyproj (and anything else sim/ needs) work on our Python 3.11 backend
   env; tell Ethan if not.
2. Our controller object (backend/atc/controller.py) plugs in via
   set_controller. For A1 it does nothing (aircraft hold).
3. Controller switch: Human ↔ Agent, at run start and mid-run, via
   set_controller. Emits controller_switch.
4. Dashboard: level picker (Normal Day / Light Rain), controller picker
   (Human / Agent), start/reset button, live PASS/FAIL banner (big and red
   on FAIL, with the reason and the aircraft involved), live scoreboard.
5. Backend loop steps the sim in real time and emits run_start, airport,
   aircraft_spawn, position, referee events, sim_end.
Done when: dashboard shows KPHX and live aircraft; switching Human ↔ Agent
works mid-run; the do-nothing agent quickly FAILS and the banner shows why.

## A2: Evaluation harness
1. Runs a controller (Human or Agent) through a level/seed headless and
   fast, using the Referee's score. Reports PASS/FAIL, fail reasons, and
   all score numbers.
2. Practice scenarios for training: busy arrival bank, departure push,
   runway crossing conflict, wrong readback, runway config change, rain
   peak.
3. Report: Agent vs. Human per level and seed, on training seeds and on
   held-out seeds separately. Emit a `metrics` event for the dashboard.
Done when: the Human runs everything and produces the report.

## A3: Deterministic planner and safety engine (no Claude yet)
1. backend/atc/safety.py: each rule returns (ok, rule_name, reason);
   constants at top with sources; unit tests per rule. Runway occupancy;
   arrival/departure spacing; wake-turbulence separation by category; taxi
   conflicts; hold short at hold points and crossings; tailwind limit;
   rain-adjusted spacing when visibility drops.
2. backend/atc/planner.py: uses all observed data (positions, speeds,
   phases, ETAs, fuel/holding time, wake categories, graph) to sequence
   arrivals, slot departures, route taxiing (explicit routes once taxi_via
   is fixed), manage holding so nobody diverts, and handle readbacks
   (accept matching, reject wrong). Every proposal has a reason. Key
   decisions produce valid options + a default for A4.
3. Flow: proposal → safety_check → issue() → clearance → readback check.
Done when: PASS on Normal Day training seeds, zero Referee safety fails on
everything; tests pass.

## A4: Claude reasoning layer
1. Async Claude client, model from ANTHROPIC_MODEL (fast model default).
2. At key decisions, Claude picks from the planner's valid options and gives
   a one-line summary, as JSON. Its prompt includes the relevant ATC
   procedures and the current situation. Don't ask "why / what you weighed"
   (Gus found that tripped an API classifier).
3. The chosen option still goes through the safety engine; rejected → next
   valid option, logged.
4. Never blocks the loop; ~3s timeout → deterministic default, "fallback".
Done when: scores with Claude are no worse than without; a no-key run works
identically on fallback.

## A5: Weather and rain handling
1. The agent reads the level's weather (visibility, wind, rain) from the
   sim and adapts: rain-adjusted spacing, runway choice by headwind within
   the tailwind rule (via set_runway_config), smarter departure metering,
   and holding management so nobody diverts.
2. Live NOAA METAR for KPHX: display-only in the top bar, labeled
   "Live KPHX", cached, never drives decisions.
Done when: big improvement on Light Rain training seeds vs. A3.

## A6: Training loop (make the agent pass)
Repeat until the agent passes both levels:
1. Run the harness on training seeds of both levels.
2. For every FAIL and every big delay, write a short failure review: what
   happened, which decision caused it, what a good controller would have
   done (based on ATC procedures and the data).
3. Improve the AGENT only: planner logic, safety rules, Claude prompt and
   procedures, holding/metering strategy. Never the simulator, levels,
   Referee, or fail limits.
4. Keep a log in docs/TRAINING_LOG.md: each round's change and the score
   before and after.
5. Every few rounds, check the held-out seeds without tuning on them.
Done when: the agent PASSES Normal Day and Light Rain on held-out seeds and
beats the Human's score on both.

## A7: Voice generation (ElevenLabs)
Tower voice + 2–3 pilot voices; speak the sim's clearance and readback text.
Cache by hash of (voice, text); fallback to `say`. Async. Dashboard audio
queue with mute and a "now speaking" indicator.
Done when: in-order playback without lag; second run fully cached.

## A8: Voice recognition and readback check (faster-whisper)
Transcribe each pilot readback audio (small/base, CPU int8, loaded once,
worker thread). Parse callsign/runway/instruction with tolerant matching;
compare to the clearance; mismatch → reject via resolve_readback, spoken
correction, alert.
Done when: wrong readbacks are caught from the audio in the harness.

---

# Integration (on main, after sim and agent are merged)

## I1: Final grading
Run Human and Agent on the held-out seeds of both levels. Report PASS/FAIL
and scores side by side.
Done when: the agent PASSES both levels with zero fails and beats the Human.

## I2: Demo polish
1. ~3–5 minute live demo: start Light Rain with the Human → delays pile up
   → FAIL banner. Reset, same level and seed with the Agent → it handles
   the rush, the safety engine blocks one unsafe proposal, a wrong readback
   gets caught → PASS. End on the scoreboard: Agent vs. Human on both
   levels.
2. Pre-warm audio cache. "SIMULATION ONLY" badge. Readable on a projector.
3. run_demo.sh: one command starts everything and opens the browser.
4. README: pitch, tech stack, architecture, how to run, .env setup, how
   levels and the Referee work, evaluation results, simulation-only
   disclaimer.
Done when: run_demo.sh works three times in a row, once with wifi off.

## Demo safety net
If it's 7am and Light Rain isn't passing yet, demo Normal Day: Human vs.
Agent with the Referee and scoreboard, and show Light Rain as "in progress"
with the honest score.

## Priority order tonight
Step 0 → S1 → S2 → A1 → S3 → S4 → A2 → A3 → S5 → A5 → A6 → A4 →
I1 → I2. Voice (A7, A8) only if there's time.

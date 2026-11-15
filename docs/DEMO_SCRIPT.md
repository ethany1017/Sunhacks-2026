# Demo script (Ethan + Gus)

Length: Step 1 takes about 1.5 min and the Agent's full day in Step 2 about 4-5 min, so the main flow is about 6-7
minutes with Step 3. To fit 5 minutes, talk over the runs and skip the optional step.

Everything below is a simulation. Nothing connects to real ATC, radios or aircraft.

Pace: `run_demo.sh` runs the sim at 10x. Demo seeds: Light Rain **25**, Normal Day **3** (`run_demo.sh` defaults; the
shell or `.env` can override `DEMO_SEED_LIGHT_RAIN` / `DEMO_SEED_NORMAL_DAY`). These seeds were picked from the
**training** seeds as days where the two controllers differ. They are not a fair sample: the honest comparison is the
multi-seed tables in the README.

The dashboard clock (top right, "SIM TIME") is minutes:seconds of sim time. Times below are sim time first, then the
wall time measured in rehearsal from the click on **Reset run**. Rehearsal ran on a busy laptop (other simulator jobs on
all cores), where 10x came out at about 7.7x for the Agent run; on an idle laptop expect wall times closer to sim time
/ 10.

## Before going on stage (10 min before)

1. Stop stale servers on the demo ports. See what holds them:
   `lsof -nP -iTCP:8100 -iTCP:5180 -sTCP:LISTEN`. Stop them with `kill $(lsof -ti :8100 -ti :5180)`. Or leave them and
   use other ports: `BACKEND_PORT=8111 FRONTEND_PORT=5191 ./run_demo.sh`.
2. Check the **Agent vs Human** panel data: it shows `backend/eval_results/summary.json`, a local file written by
   `python -m app.evaluate --publish`. An old file shows old numbers on stage. Republish it after final grading, or move
   it aside (`mv backend/eval_results/summary.json /tmp/`), which hides the panel.
3. `./run_demo.sh`. It opens the browser at `http://localhost:5180`. The backend starts a Normal Day Agent run by
   itself. Full screen the browser (projector: 1920x1080 or 1280x720 both fit).
4. Check: the amber **SIMULATION ONLY** badge top left, **ONLINE** next to the sim time, the map with runways.
5. Sound: click **SOUND OFF · Enable** once (browsers need one click before playing audio). At 10x only about one spoken
   line in eight is played, on purpose: lines that would lag behind the map are skipped. Audio comes from the
   pre-warmed cache (macOS `say` voices; no ElevenLabs key).

## Step 1: Light Rain with the Human (about 1.5 min)

Click: **Level → Light Rain**, **Controller → Human**, **Reset run**.

Say while it runs:
- "This is Phoenix Sky Harbor on a light-rain day: lower visibility, longer runway occupancy, bigger spacing. The demand
  is real-day traffic from BTS data, compressed."
- "The Human is a simulated average controller: reaction time, a workload limit, extra spacing, the occasional missed
  wrong readback. Numbers and sources are in docs/HUMAN_MODEL.md."
- "The Referee is part of the simulator. It watches the true state and does not care who is controlling."

The **FAIL** banner appears at sim **12:49**, **77 s** after Reset (same in rehearsal runs 1-3). It reads:

> FAIL · Light Rain · seed 25 · Human
> AAL2014, ENY2973 · collision: AAL2014 and ENY2973 collided on the ground (their footprints overlap)

(It shows "2 fails": the Referee logs the same pair twice, 3 sim seconds apart.) Say: "Two aircraft collided on a taxiway.
The run is over."

## Step 2: Reset, same level and seed, with the Agent (about 4-5 min, talk over it)

Click: **Controller → Agent**, **Reset run**. Same level, same seed 25, same traffic.

Point at:
- The decisions feed on the right: every proposal, its **SAFETY OK / SAFETY REJECTED** card, the clearance, the pilot's
  readback. AI decision cards show "Fallback" (no Claude key on this laptop: the deterministic planner decides).
- **Safety engine blocks an unsafe proposal**: first **SAFETY REJECTED · hold short** at sim **13:22** (a runway crossing
  for an aircraft while DAL1383 is on its takeoff roll on 07L/25R). More at 19:09 (taxi conflict, head-on route), 23:35
  and 25:57 (hold short), 24:30 (taxi conflict). The first one appears **81 s** after Reset. At 10x the feed scrolls
  fast: the red card is near the top for only a few seconds, so watch for sim 13:20 and point right away. Say: "The
  planner proposed it, the safety engine said no, nothing was transmitted." A few seconds later the same crossing is
  proposed again, approved and cleared once the runway is free.
- **No wrong readback happens in Light Rain seed 25.** The wrong-readback catch is on Normal Day seed 3 (optional step).
- The scoreboard: Fails stays 0.

**PASS** at sim **38:58** (all 25 aircraft handled, 0 fails, avg delay 184 s, 0 diversions). Rehearsal wall time: about
**5 min** on the busy laptop; expected about 4 min on an idle one (not measured). The banner reads "PASS · Light Rain · seed 25 · Agent".

Rehearsal wall times (seconds after Reset):

| Run | Human FAIL banner | Agent first SAFETY REJECTED | Agent PASS banner |
|---|---|---|---|
| 1 (online) | 77.5 | (not timed) | 302.4 |
| 2 (offline: no DNS for the browser, proxy to nowhere for the backend) | 77.4 | 80.8 | 291.1 |
| 3 (online) | 77.2 | 80.8 | 305.7 |

Same verdicts as the headless harness in all three (Human FAIL at sim 12:49, Agent PASS, 25 handled, avg delay 184 s).

## Step 3: Scoreboard and Agent vs Human (30 s)

Point at the scoreboard (this run) and the **Agent vs Human** panel (all seeds, from the evaluation harness, judged only
by the Referee).

<!-- RESULTS: filled in by the coordinator after final grading -->

## Optional: Normal Day, wrong readback, hand-over

Normal Day seed 3 is where a wrong readback gets caught (Light Rain 25 has none). Click: **Level → Normal Day**,
**Controller → Agent**, **Reset run**. Measured in rehearsal (online, busy laptop; "est." = sim time divided by that run's pace of 9.7x, not timed):

| Sim time | Wall time after Reset | Feed card |
|---|---|---|
| 07:10 | ~45 s (est.) | SAFETY REJECTED · hold short (crossing while FFT3069 is on its takeoff roll) |
| 26:20 | 166 s | **READBACK WRONG** HAL2355 (read back "runway seven right", cleared via 07L), then **CORRECTION**: "read it back again" |
| 28:31 | ~177 s (est.) | **READBACK WRONG** AAL2641 ("runway seven right, cleared for takeoff"), then CORRECTION |
| 36:42 | 228 s | **PASS** · Normal Day · seed 3 · Agent (25 handled, 0 fails, avg delay 61 s, 2 wrong readbacks caught) |

The READBACK WRONG and CORRECTION cards scroll away within seconds; the scoreboard's **Readback errors caught** counter
(1, then 2) stays, so point at that.

Hand-over: start with **Human**, then click **Switch to Agent** mid-run; the feed shows "CONTROLLER · Now controlled by
Agent" and the Agent takes over the traffic where it is. Rehearsed once: Normal Day seed 3, Human, switched at sim 06:39
(40 s after Reset): PASS at sim 36:32, 225 s after Reset (25 handled, avg delay 60 s, 1 wrong readback caught).

## The honest framing (say this)

"Everything here is a simulation; it never touches real ATC. The two seeds you saw were picked from our training days
because the controllers differ there; across all seeds, results are in the README, and the Agent still fails some
training days (Light Rain 21, 22 and 23, where the Human fails too, and Normal Day 2 and 4, where aircraft are left at
the end of the day)."

## Safety net: if Light Rain misbehaves

Demo Normal Day instead: **Normal Day + Human → Reset** (Human passes seed 3, avg delay 83 s, misses 1 wrong readback),
then **Normal Day + Agent → Reset** (passes, avg delay 61 s, catches 2 wrong readbacks). Show Light Rain as "in progress"
with the honest score.

## If something breaks on stage

- **Port busy**: `run_demo.sh` says which process holds the port and stops (it never kills anything). Stop it
  (`kill $(lsof -ti :8100 -ti :5180)`) or use `BACKEND_PORT=8111 FRONTEND_PORT=5191 ./run_demo.sh`.
- **Blank map / no wifi**: works offline. The map falls back to a dark background with the airport layers; the top bar
  shows the last cached METAR or "unavailable". Rehearsal run 2 was fully offline.
- **No sound**: click **SOUND OFF · Enable** (the browser needs a click). Mute with the same button. Sound is optional.
- **Dashboard frozen**: reload the page. The full history of the current run replays; the run kept going in the backend.
- **A run goes differently from rehearsal**: say what the Referee shows, as it shows it. Do not explain it away.
- **Backend crash**: Ctrl-C (stops both), then `./run_demo.sh` again and start the run again.
- **Last resort**: rehearsal screenshots (1920x1080) are not committed: they show an outdated Agent vs Human panel (see
  "Before going on stage", step 2). Retake them after the panel data is republished.

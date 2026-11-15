# Final report: overnight run, Nov 15 2026

Written by Claude while Ethan was away. Everything here was measured; nothing is estimated unless it says so.

## The short version

- **The demo works.** One command starts it. The planned flow (Light Rain, Human fails, reset, Agent passes) ran
  three times in a row from a cold start, once fully offline, and once more on the final commit.
- **Light Rain: goal met.** On the held-out seeds the Agent passes 3 of 3 days with zero Referee fails. The Human
  passes 1 of 3.
- **Normal Day: goal NOT met.** The Agent passes 2 of 3 held-out days, the same as the Human. It has fewer fails and
  less delay than the Human, but one day (seed 9003) fails for every controller we have.
- The simulator, levels, Referee and fail limits were not touched after the freeze (`levels-v1`).

## Run the demo

```bash
kill 15007 15022                      # old servers from Saturday 20:35 still hold ports 8100 and 5180
cd ~/Sunhacks-2026-agent && ./run_demo.sh
```

Run it from `~/Sunhacks-2026-agent`. That checkout has the pre-warmed audio cache (it is gitignored, so a fresh clone
speaks through macOS `say` live instead). `~/Sunhacks-2026` is still on the `sim` branch and does not have the demo.
If you would rather not kill the old servers: `BACKEND_PORT=8111 FRONTEND_PORT=5191 ./run_demo.sh`.
The click-by-click talk track is [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

## Final scores (held-out seeds, Referee numbers, agent at commit 639b0ac)

| Level | Controller | Days passed | Referee fails | Avg delay |
|---|---|---|---|---|
| Normal Day | Human | 2/3 | 2 (collisions on 9003) | 102 s |
| Normal Day | **Agent** | 2/3 | 1 (collision on 9003) | 90 s |
| Normal Day | plain tower rules | 2/3 | 1 (collision on 9003) | 82 s |
| Light Rain | Human | 1/3 | 2 (stuck on 9021, unhandled on 9023) | 224 s |
| Light Rain | **Agent** | **3/3** | **0** | 191 s |
| Light Rain | plain tower rules | 1/3 | 2 (stuck, unhandled) | 194 s |

Overall: Human 3/6 days, 4 fails, 163 s. Agent 5/6 days, 1 fail, 141 s.

The one Agent fail, Normal Day 9003 at t=1190.7: arriving A320 AAL545 is assigned gate B16 and clips N2701BK, a
small GA aircraft that has been standing at the neighbouring stand PB16 since the start of the day. The Human and the
plain tower rules hit the same pair. The two stands share one lane node in the airport layout. We found no controller
instruction that avoids it, and fixing the layout would mean changing the frozen simulator.

"Plain tower rules" is Gus's built-in tower with no human limits. The Agent's procedures started from that rule base,
so this row is the honest baseline for what our work added.

Other measurements, same agent version:

| Set | Human | Agent | plain tower rules |
|---|---|---|---|
| Training, Normal Day 1-5 | 5/5, 84 s | 3/5, 69 s | not run |
| Training, Light Rain 21-25 | 1/5, 202 s | 3/5, 177 s | not run |
| Practice scenarios | 6/8 | 8/8 | not run |
| Validation, Normal Day 41-55 | 7/15, 88 s | 7/15, 77 s | 8/15 |
| Validation, Light Rain 61-75 | 4/15, 199 s | 6/15, 181 s | 7/15 |

Read the validation rows before trusting any one run. Every controller fails many unseen days in this simulator. The
Agent is level with the Human on Normal Day and ahead on Light Rain, and it is behind the plain tower rules on the
validation days of both levels.

## Checklist

| # | Item | Result |
|---|---|---|
| 1 | Finish the brief | **Partly.** S1-S5, A1-A7, I1 and I2 are built. A8 (faster-whisper) is not built; readbacks are checked from the simulator's text. A6's goal is met on Light Rain only. |
| 2 | Pull Gus's code | **Done.** `origin/main` has nothing new. Gus pushed a branch `origin/voice`; it is not merged (see judgment calls). |
| 3 | Integrate | **Done.** `agent` already contains `sim`; `main` is fast-forwarded to `agent`. The Human / Agent switch and both levels work from the dashboard. |
| 4 | Check everything | **Pass.** Backend 89 tests, simulator 75 tests, Gus's agent 18 tests, both frontend builds. Starts with no keys. WebSocket replay works. Same seed gives the same run on both levels. Fallbacks work with no Claude key, no ElevenLabs key and no network. |
| 5 | Final grading | **Fail on the stated requirement.** Light Rain meets it. Normal Day does not (2/3, one fail). |
| 6 | Training loop | **Done; stopped at about 08:15, before the 09:30 cutoff, because the last round found nothing to adopt.** Every round is logged in [TRAINING_LOG.md](TRAINING_LOG.md). The best version (639b0ac) is kept. |
| 7 | Demo ready | **Pass.** See "Demo checks" below. The main flow takes 6 to 7 minutes at 10x, not 3 to 5. |
| 8 | Ship | **Done.** Tests and builds pass on the shipped code. `main`, `sim` and `agent` were pushed to origin at 08:22 with a normal push (no force); `origin/main` matched local `main` and both checkouts were clean. Backup tags `backup-*-1115-0822` are local only. This row was added in a follow-up docs commit, pushed the same way. |
| 9 | This report | Done. |

### Demo checks

- Three cold `./run_demo.sh` runs in a row, driven through headless Chrome, one with the network cut. Each time:
  Light Rain seed 25 with the Human shows the FAIL banner, Reset, same seed with the Agent ends in PASS. No backend
  tracebacks. These three were on the planner one round before the final one (before 639b0ac).
- One more full cold run on the final commit, online: Human FAIL banner at 77.5 s, Agent PASS at 272.6 s (sim 39:10,
  25 aircraft handled, 0 fails, avg delay 183 s). The offline run was not repeated on the final planner; the
  fallbacks it exercises (voice, weather, map, Claude) did not change.
- On the final commit, also: Normal Day seed 3 with the Agent (PASS, two wrong readbacks caught) and a mid-run
  Human to Agent hand-over (PASS).
- The Agent vs Human panel shows the published grading: training 6/10 each, held-out Human 3/6 and Agent 5/6.
- "SIMULATION ONLY" badge is visible and the layout was checked at 1280x720.
- Audio cache is pre-warmed for both demo seeds (macOS `say` voices); every line in the last runs played from cache.
- Exact times and what to say are in DEMO_SCRIPT.md.

## Errors found, the approved plan, and the fix

Every plan went to the review council before any code changed. Where the council sent a plan back, that is noted.

| Error | Cause | Plan the council approved | Outcome |
|---|---|---|---|
| The Agent only worked on the 5 seeds it was tuned on (Normal Day 3/15, Light Rain 0/15 on fresh seeds; held-out 1/6) | Rules had been fitted to five days | Re-base the planner on the tower's procedures, keep the safety engine in front of every instruction, and split seeds into development, validation and held-out | Done (ca8c06b) |
| Ground collisions while taxiing | The ground probe missed some conflicts | Round G1 probe fixes | Done (424d1b5) |
| Four follow-up ideas (re-issue reroutes, no line-up on a tailwind end, takeoff for any lined-up aircraft, avoid blocked exits) | n/a | Approved on condition they measured better | They measured worse. Not adopted, recorded (109e496) |
| Claude layer was not wired into the controller | A4 unfinished | Non-blocking advisor, 3 s fallback, deterministic in the harness | Done (25a5691) |
| Gridlock after a mid-run Human to Agent switch | The Agent did not take over aircraft already cleared | `Planner._adopt` hand-over | Done (2ea8fa3) |
| A Human run was silent in the feed and the voice | Only Agent clearances were published | Publish the Human's clearances and readbacks too | Done (d3bbf74) |
| Demo too slow, no way to pick the demo day, layout broke at 1280x720, seed not shown | Demo polish gaps | `SIM_SPEED` 10 default, `DEMO_SEED_*`, layout fix, seed in the status bar | Done (ad109cb, 36e0b7a, 4a39f92) |
| Aircraft waiting to cross a runway starved; departures backed up late in the day; un-jam turned aircraft into neighbours | Missing procedures | Rounds G2 and G3. The first plan was sent back (the diff file did not match the measured code and the text misdescribed one condition); corrected and approved | Done (5abf8a6) |
| Collisions next to a pushback | The probe predicted a push at taxi speed, held the crossing aircraft inside the push's turn | Round G4: predict a push at its own speed, with a unit test | Done (639b0ac) |
| Stale README and stale Agent vs Human panel | Written before the final agent | Rewrite from the final grading files; re-publish the summary | Done (0a6abd2) |
| DEMO_SCRIPT told the presenter the Agent fails Light Rain 21 (it passes), and its times pre-dated round G4 | Written one commit too early | Council review caught it; corrected and re-measured on the final commit | Done (7adcef6) |

## Judgment calls made without you

1. **Did not merge `origin/voice`.** It is not on `main`, it changes `sim/core/engine.py` (frozen simulator), and it
   duplicates the voice we already built. It is untouched on origin for you and Gus to decide.
2. **Re-based the Agent on the tower's rule base.** The first agent did not generalise. The README says this plainly.
3. **Seed protocol.** Development seeds for workers, validation seeds only for accept or reject decisions, held-out
   seeds only for grading.
4. **Adopted round G2 although it missed one condition** (Normal Day seeds 1-20: 12/20 where the plan asked for
   13/20). The council agreed to the override because every other set improved. It is recorded in the training log.
5. **Held-out seeds were run four times, not once.** Three aggregate checks during the night (1/6, 5/6, 5/6) and the
   final grading. No change was chosen from them.
   After the final grading I read the one failing held-out day (9003) for the failure review, and one last idea for
   that kind of collision was then tested on development seeds only. It did not help and was not adopted, so the
   graded agent is unchanged.
6. **Ran the plain tower on the held-out seeds** so the results have an honest baseline, even though it shows the
   tower beating the Agent on Normal Day delay.
7. **Demo seeds are Light Rain 25 and Normal Day 3.** Chosen from the training seeds as days where the two
   controllers differ. The README and the demo script say so.
8. **Demo runs at 10x.** One day takes about four minutes, so the two-run flow takes 6 to 7 minutes, longer than
   the 3 to 5 asked for. `SIM_SPEED` changes it; the rehearsals were all at 10x.
9. **A8 was skipped** as optional.
10. **Left the old servers on ports 8100 and 5180 running.** They were started Saturday 20:35 and may be yours.
11. **`sim` stays at be43ff5.** No simulator change was needed after the freeze.

## Not finished, and things to know

- Normal Day does not pass all held-out days (above). On stage, use the honest framing in DEMO_SCRIPT.md.
- A real Claude answer and real ElevenLabs voices were never exercised: there are no keys on this machine. Both
  fall back cleanly. If you add keys to `.env`, do one rehearsal first.
- A8 (speech recognition of readbacks) is not built.
- The Agent is behind the plain tower rules on validation days. The safety engine's vetoes sometimes hold aircraft
  that the tower would have moved.
- For Gus, in the frozen simulator: stand pairs that share a lane node (B16/PB16, E2/PE2, C11/PC11) collide whoever
  controls; gate F4's push track passes 8 m from stand PF4; the Referee logs the same collision pair twice.

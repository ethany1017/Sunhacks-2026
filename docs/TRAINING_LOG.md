# Training log: agent controller (A3, deterministic planner + safety engine)

Training seeds only (normal_day 1-5, light_rain 21-25) and the practice scenarios. Held-out seeds never run.
Scores are the simulator Referee's, via `python -m app.evaluate`. "Safety fails" = collision, loss_of_separation,
runway_occupied, runway_incursion.

## Before levels-v1 (old, unfrozen traffic: 21-35 flights per day)

| Round | Change | ND pass | ND safety fails | LR safety fails | Practice pass |
|---|---|---|---|---|---|
| 0 | No controller logic (do-nothing agent) | 0/5 | - | - | 0/8 |
| 1 | Landing clearances at 120 s, pushback, taxi routes (Dijkstra, no head-on), takeoff queue, crossings | 0/5 | many (collisions, incursions) | many | 2/8 |
| 2 | Taxi routes committed on readback, lead-in for re-routes, pushback lanes, turn-round check | 1/5 | 5 | 9 | 4/8 |
| 3 | Departures at their own runway's hold line, taxi-in from any stopped arrival, crossing clearances protected from resume/re-route (fixed ENY1280 incursion) | 1/5 | 3 | 8 | 5/8 |
| 4 | Exit choice avoids routes back onto the landing runway (fixed N4669JV incursion), issue-time convergence check, pushback sweep | 1/5 | 3 (collision 2, LoS 1) | 7 | 5/8 |
| 5 | Stale runway-exit estimate distrusted + last-resort go-around at 20 s (fixed LR21 runway_occupied); stopped-fuselage and see-and-avoid freeze holds; wait-cycle guard; unjam re-route; sharp-turn sweep check (fixed departure_push collision); runway flows derived from the layout | 2/5 | 2 (collision 1, LoS 1) | 5 | 5/8 |

## levels-v1 (frozen, 24-25 flights per day, earlier wind shift): re-baseline

Re-run after merge 1cdcf25, same agent code as round 5 plus the path-sampled stopped-aircraft check (commit 90f832f).

| Round | Change | ND pass | ND safety fails | LR pass | LR safety fails | Practice pass |
|---|---|---|---|---|---|---|
| R0 (re-baseline) | as above | 3/5 (1, 3, 5) | 1 (ND4 collision) | 0/5 | 0 | 6/8 |
| R1 | Convergence in-trail test uses route headings at the conflict point (fixed ND4 collision); tight-window fallback hold (fixed ND4 gridlock) | 4/5 | 0 | 0/5 | 0 | 6/8 |
| R2 | Old-flow departures only within 60 s of the turn (LR22: a departure cleared on 25R after the turn was refused for tailwind once lined up and blocked 07L/25R all day; completed 4 -> 17); re-route round an aircraft stopped in the way for 90 s | 4/5 | 0 | 0/5 | 0 | 6/8 |
| R3 (rejected) | Stopped-aircraft path rule in the safety engine + uncleared arrival routes ignored: fixed ND2's planning deadlock but ND fell to 1/5 and LR24 got 3 incursions. Reverted. | 1/5 | 0 | 0/5 | 3 | 6/8 |

Final (commit 442d8a7, run after the last code edit): ND 4/5 (ND2 fails: stuck 4, unhandled_at_end 1), LR 0/5, practice 6/8,
zero safety fails on all 18 runs. Human on the same frozen levels: ND 5/5, LR 1/5, practice 6/8.

Human reference on levels-v1 (not tuned toward): ND 5/5, LR 1/5 (4 collisions over LR).

## A5 / A6 (levels-v1, training seeds and practice only; held-out seeds never run)

Every round re-ran all ten level seeds plus the eight practice scenarios (`python -m app.evaluate --controllers agent
--levels levels practice`). "LR fails" = Referee fails summed over Light Rain 21-25. Average delay = mean of the per-run
averages (Referee delay over completed flights).

| Round | Change | ND pass | LR pass | LR fails | Safety fails (all 18) | Practice | ND / LR avg delay (s) |
|---|---|---|---|---|---|---|---|
| A3 end (5fca234) | baseline, re-run | 4/5 | 0/5 | 33 (stuck 27, unhandled 5, diversion 1) | 0 | 6/8 | 100 / 248 |
| R1 (a877ae4) | release a hold once the aircraft it waits for has stopped for the holder (the Referee calls 60 s of mutual blocking a gridlock); keep a ready departure at the gate (up to 200 s) while another aircraft's taxi route passes its pushback lane; no taxi route or resume into the sweep of a pushback that may be moving; super-to-heavy departure wake (2 min); an `alert` per rejection episode; readbacks for clearances issued before a switch are checked against the observed clearance | 4/5 | 0/5 | 26 | 0 | 6/8 | 108 / 261 |
| R2 (21820ea) | the convergence checks (planner and safety) count two aircraft heading at each other (more than 120 deg apart) as converging out to the sim's see-and-avoid range + 12 m; an approved pushback protects its stand-to-lane sweep and the spot where it swings round at the end for the whole pushback | 3/5 | 0/5 | 16 | 0 | 5/8 | 122 / 275 |
| R3 (fea9c0f) | line up and wait as soon as the preceding departure has lifted off, takeoff clearance at the minimum spacing; a runway clearance issued in a pass is visible to the rest of that pass (fixed a crossing and a line-up approved in the same tick, which deadlocked) | 4/5 | 0/5 | 16 | 0 | 5/8 | 115 / 268 |
| R4 (9ab00ba) | opposite use of one taxiway edge allowed as a second-choice route when the two aircraft are well apart in time on it (earliest entry at 10 m/s, latest exit at 3 m/s after a 10 s start, 60 s margin); first choice is still a route with no opposite use at all | **5/5** | 0/5 | **12** | 0 | 5/8 | 117 / 270 |
| R5 (1990bd5) | an arrival past half its holding limit (`holding_s` / `holding_limit_s`) and within 240 s lands before any crossing or line-up on its runway | 5/5 | 0/5 | 12 | 0 | 5/8 | 117 / 270 |

Tried and rejected (each reverted, numbers from its own full run):
- Nose-to-nose taxiway pairs as a static head-on rule (close parallel legs used the other way, +20 m or +12 m margin): ND 0/5, 3-8 runway incursions, collisions. Far too restrictive; replaced by the time-based R2 check.
- Arrivals stopped for their taxi clearance claim no route (the A3 planning-deadlock idea, planner part only): fixed ND2 but ND3/4/5 failed and ND2 had a collision.
- Holding the stopped aircraft first when a hold times out (swap the order): ND4 stuck 6, ND1 collision unchanged.
- Stopped-fuselage clearance check on resumes (all resumes, or only resumes past the aircraft waited for): turned the ND1 collision into holds that never ended (ND1 stuck 2, ND4 stuck 5).
- Mixed operations (departures also from the arrival runways, queue penalty): 31 go-arounds, 11 diversions, 5 losses of separation. Departures queued next to the arrival runway's exits.
- Turning the airport round later (tailwind threshold 7 kt instead of 4 kt): 12 go-arounds, 2 collisions, 2 losses of separation.
- Narrower / longer gate holds (45 m within 800 m, 120 s; 90 m within 800 m, 150 s; 200 s -> 270 s): each lost more than it gained.
- Re-routing an aircraft whose resume was refused for 150 s: no effect.

### Failure reviews (final code, R5)

- **LR21, unhandled 1 (SWA4063, SWA537 taxiing to 07L, UAL4708 taxiing in at t=2400).** The day simply ran out:
  after the turn to east flow (t=495, 5.4 kt tailwind on west flow, above the 5 kt runway-selection practice) every
  departure taxis 2-3 km to the 07L intersection instead of 0.4-0.8 km to 25R, at 0.8x taxi speed in rain. The last
  pushbacks were also held at the gate for the full 200 s because inbound routes passed their lanes. A good controller
  would have started those pushbacks earlier with a lane-level plan (one-way flows in the alleys) rather than a blanket
  hold.
- **LR22, stuck 4 + unhandled 1.** An arrival (AAL1632) taxiing into the A-gate alley was held for a departure
  (SWA4745) that had pushed into its path; the departure's only way out was head-on with the arrival's route, the
  arrival's way in ran through the departure's body: a single-lane alley gridlock. SWA4745 pushed after the 200 s gate
  hold ran out. A good controller sequences the alley: either the arrival goes in first (the departure waits at the
  gate, up to the 300 s limit) or it holds outside the alley until the departure is out. Then SWA1629 at A25 could not
  push (the stuck SWA4745 sat in its push path, the 300 s gate limit followed).
- **LR23, stuck 1 + unhandled 1.** AAL1585 at gate F4 cannot push back: the GA aircraft N4779RO at the adjacent stand
  PF4 stands 5 m from F4's pushback path until it departs itself (ready about t=763). AAL1585 is ready at t=182, so
  the 300 s gate limit expires at 482 with the push still blocked. The Human pushes anyway and collides with that GA
  aircraft at t=443 (docs/LEVELS.md, and the Human's LR23 collision above). See "unavoidable" below. The unhandled
  1 is two departures still taxiing / holding at t=2400.
- **LR24, stuck 2 + unhandled 1.** DAL1858 (D1) and AAL1126 (next stand) pushed early; each one's route out met the
  other's push and then a continuous stream of inbound arrivals (each arrival's whole remaining route is a claim), so
  neither ever got a taxi route (600 s). The R4 time-separated routes did not open a window because the inbound stream
  kept the near part of the routes busy. A good controller would hold one inbound arrival short of the alley for 30 s
  to let the two departures out.
- **LR25, unhandled 1 (AAL438 cleared for takeoff at t=2400).** It taxied 2.8 km from t=1828 to 2347 (one 40 s
  convergence hold), lined up behind UAL1194 and was cleared at 2350, still lining up at the day end. A minute saved
  anywhere in its taxi would have passed the day.
- **departure_push (practice), stuck 5.** Three departures merging into one taxiway are held for each other in turn;
  one resume is then refused by the sharp-turn sweep rule (another aircraft 58 m from the corner) and that hold never
  ends; the queue behind it stops for 600 s. Regressed in R2 (it passed before); not fixed.
- **runway_config_change (practice), stuck 1 + unhandled 1.** A departure turned round for the new flow is held
  indefinitely behind traffic of the new flow.

### Unavoidable for any controller (frozen simulator, evidence)

- LR23: AAL1585 (gate F4, ready t=182) can only push through the parked N4779RO's footprint (stand PF4, the safety
  engine measures it 5 m from F4's pushback path), and N4779RO does not become ready before t=763. Pushing before 482
  collides (the Human does, t=443); not pushing by 482 is the Referee's 300 s gate-stuck fail. So LR23 cannot PASS for
  any controller on levels-v1 (it can at best fail on the one gate-stuck fail).

Final (commit 1990bd5, run after the last code edit; same run as the table below): agent ND 5/5, LR 0/5 (12 fails:
stuck 7, unhandled 5), zero safety fails on all 18 runs, practice 5/8. Human in the same run: ND 5/5, LR 1/5 (11
fails including 4 collisions), practice 6/8.

| level | seed | Human verdict, fails | Agent verdict, fails | done H / A | avg delay H / A (s) | GA H / A | div | rb caught/missed H / A |
|---|---|---|---|---|---|---|---|---|
| normal_day | 1 | PASS | PASS | 25 / 25 | 74 / 117 | 0 / 0 | 0 / 0 | 0/0 / 0/0 |
| normal_day | 2 | PASS | PASS | 24 / 24 | 84 / 130 | 0 / 0 | 0 / 0 | 0/0 / 2/0 |
| normal_day | 3 | PASS | PASS | 25 / 25 | 83 / 106 | 1 / 1 | 0 / 0 | 0/1 / 0/0 |
| normal_day | 4 | PASS | PASS | 25 / 25 | 96 / 123 | 0 / 0 | 0 / 0 | 1/0 / 1/0 |
| normal_day | 5 | PASS | PASS | 25 / 25 | 82 / 108 | 0 / 0 | 0 / 0 | 3/0 / 2/0 |
| light_rain | 21 | FAIL unhandled 1 | FAIL unhandled 1 | 24 / 22 | 182 / 263 | 0 / 0 | 0 / 0 | 1/0 / 0/0 |
| light_rain | 22 | FAIL collision 1, stuck 4, unhandled 1 | FAIL stuck 4, unhandled 1 | 19 / 19 | 179 / 274 | 0 / 1 | 0 / 0 | 0/0 / 0/0 |
| light_rain | 23 | FAIL collision 1 | FAIL stuck 1, unhandled 1 | 25 / 23 | 182 / 355 | 0 / 0 | 0 / 0 | 0/0 / 0/0 |
| light_rain | 24 | PASS | FAIL stuck 2, unhandled 1 | 24 / 21 | 192 / 193 | 1 / 1 | 0 / 0 | 0/0 / 0/0 |
| light_rain | 25 | FAIL collision 2, unhandled 1 | FAIL unhandled 1 | 23 / 24 | 276 / 267 | 0 / 0 | 0 / 0 | 2/0 / 1/0 |

## Re-base on tower procedures

**Why.** The A3-A6 planner was tuned on 5 seeds per level and did not generalise: on development seeds normal_day 6-20 /
light_rain 26-40 it passed ND 3/15 (20 collisions) and LR 0/15. Gus's rule-based tower (`sim/tower/controller.py`, the
base of the Human without its handicaps) did much better on normal_day 1-20 / light_rain 21-40, so the planner's
decision logic is now the tower's, ported into `backend/atc/planner.py` with its constants, decision order and ground
probe, behind our safety engine and through the simif adapter (`backend/atc/` imports nothing from `sim`; the static
airport graph comes from the new `si.layout()`).

What the port changes from the tower: every instruction is proposal -> safety_check -> issue -> clearance; what the
tower read from raw sim events (takeoff roll, airborne, parked, a pilot's "unable") is read from the observation;
readbacks are answered on the tick they arrive (`si.readback_waiting()`; answering them up to 0.4 s later made crossing
clearances systematically later than the tower's); runway flow, arrival runway and departure line-up order are
`choose` decisions (default = the tower's choice). One fix beyond the tower is in the port: a flow-change reroute that
the sim dropped (`clearance.no_effect`, the aircraft had passed the route's start) is planned again when the observed
`entry_node` shows the old route is still flown (ND17: a departure lined up on 25R after the turn-round and blocked the
runway for the rest of the day).

No safety rule was changed. Safety rejections in the port are few per run (typically 3-9: crossings refused while a
takeoff roll is still observed on the runway or another crossing clearance is live; a few head-on taxi routes; a few
pushbacks through a neighbour's footprint).

**After** (agent: commit with this section, run after the last code edit; tower: `python -m sim.core.run --controller
tower` on the same frozen simulator, same seeds, run tonight):

normal_day: agent pass 13/20, safety fails 1, stuck 14; tower pass 14/20, safety fails 1, stuck 39.
light_rain: agent pass 5/20, safety fails 5, stuck 16; tower pass 8/20, safety fails 6, stuck 29.
Collisions over the 40 runs: agent 6, tower 7. No loss_of_separation, runway_occupied or runway_incursion for either.

The port is below the tower on pass count (ND -1, LR -3). The runs diverge chaotically from the tower's within the first
few minutes (observation noise draws and timing differ), so single seeds flip both ways (agent passes ND11, ND20, LR29,
LR39 that the tower fails; fails ND1, ND4, ND13, LR25, LR31, LR35, LR36, LR37 that the tower passes). Remaining failure
classes seen: ground collisions between a taxi-in and a taxi-out at a junction near a runway crossing (LR21, LR31, LR35,
LR38, ND1); a departure stopped on its push lane whose only way out is head-on into an arriving aircraft's route, which
the safety engine refuses, while the arrival waits for it (ND13, LR34: gridlock, the tower would have sent the head-on
route); arrivals stuck holding short of 07L after the turn-round (LR36, LR37); one unhandled aircraft at day end.

| level | seed | agent verdict | agent fails by rule | done | avg delay (s) | tower verdict | tower fails by rule | tower avg delay (s) |
|---|---|---|---|---|---|---|---|---|
| normal_day | 1 | FAIL | collision 1 | 25 | 85 | PASS | - | 60 |
| normal_day | 2 | PASS | - | 24 | 91 | PASS | - | 88 |
| normal_day | 3 | PASS | - | 25 | 83 | PASS | - | 72 |
| normal_day | 4 | FAIL | unhandled_at_end 1 | 24 | 70 | PASS | - | 78 |
| normal_day | 5 | PASS | - | 25 | 61 | PASS | - | 58 |
| normal_day | 6 | PASS | - | 25 | 106 | PASS | - | 83 |
| normal_day | 7 | PASS | - | 25 | 103 | PASS | - | 94 |
| normal_day | 8 | PASS | - | 24 | 74 | PASS | - | 65 |
| normal_day | 9 | PASS | - | 25 | 92 | PASS | - | 89 |
| normal_day | 10 | PASS | - | 25 | 78 | PASS | - | 70 |
| normal_day | 11 | PASS | - | 25 | 102 | FAIL | stuck 14, unhandled_at_end 1 | 26 |
| normal_day | 12 | FAIL | diversion 2, stuck 1, unhandled_at_end 1 | 21 | 94 | FAIL | diversion 2, stuck 1, unhandled_at_end 1 | 94 |
| normal_day | 13 | FAIL | stuck 2, unhandled_at_end 1 | 23 | 97 | PASS | - | 92 |
| normal_day | 14 | FAIL | unhandled_at_end 1 | 24 | 76 | FAIL | unhandled_at_end 1 | 74 |
| normal_day | 15 | FAIL | stuck 1, unhandled_at_end 1 | 24 | 108 | FAIL | collision 1 | 95 |
| normal_day | 16 | PASS | - | 25 | 92 | PASS | - | 84 |
| normal_day | 17 | PASS | - | 25 | 94 | PASS | - | 93 |
| normal_day | 18 | PASS | - | 25 | 111 | PASS | - | 71 |
| normal_day | 19 | FAIL | stuck 10, unhandled_at_end 1 | 12 | 52 | FAIL | stuck 13, unhandled_at_end 1 | 45 |
| normal_day | 20 | PASS | - | 25 | 102 | FAIL | stuck 11, unhandled_at_end 1 | 138 |
| light_rain | 21 | FAIL | collision 2 | 25 | 197 | FAIL | collision 2 | 197 |
| light_rain | 22 | FAIL | unhandled_at_end 1 | 23 | 165 | FAIL | unhandled_at_end 1 | 173 |
| light_rain | 23 | FAIL | stuck 1 | 25 | 202 | FAIL | collision 1 | 163 |
| light_rain | 24 | PASS | - | 24 | 165 | PASS | - | 163 |
| light_rain | 25 | FAIL | unhandled_at_end 1 | 23 | 201 | PASS | - | 225 |
| light_rain | 26 | FAIL | stuck 1, unhandled_at_end 1 | 22 | 196 | FAIL | unhandled_at_end 1 | 223 |
| light_rain | 27 | PASS | - | 25 | 171 | PASS | - | 159 |
| light_rain | 28 | PASS | - | 25 | 215 | PASS | - | 200 |
| light_rain | 29 | PASS | - | 25 | 157 | FAIL | stuck 12, unhandled_at_end 1 | 107 |
| light_rain | 30 | FAIL | unhandled_at_end 1 | 23 | 149 | FAIL | stuck 1, unhandled_at_end 1 | 175 |
| light_rain | 31 | FAIL | collision 1, unhandled_at_end 1 | 22 | 237 | PASS | - | 248 |
| light_rain | 32 | FAIL | stuck 6, unhandled_at_end 1 | 16 | 163 | FAIL | stuck 7, unhandled_at_end 1 | 114 |
| light_rain | 33 | FAIL | unhandled_at_end 1 | 21 | 229 | FAIL | unhandled_at_end 1 | 256 |
| light_rain | 34 | FAIL | stuck 1, unhandled_at_end 1 | 23 | 187 | FAIL | stuck 3, unhandled_at_end 1 | 184 |
| light_rain | 35 | FAIL | collision 1 | 25 | 205 | PASS | - | 172 |
| light_rain | 36 | FAIL | stuck 1, unhandled_at_end 1 | 24 | 230 | PASS | - | 249 |
| light_rain | 37 | FAIL | stuck 6, unhandled_at_end 1 | 16 | 176 | PASS | - | 173 |
| light_rain | 38 | FAIL | collision 1, unhandled_at_end 1 | 24 | 212 | FAIL | unhandled_at_end 1 | 195 |
| light_rain | 39 | PASS | - | 25 | 177 | FAIL | collision 3, stuck 6, unhandled_at_end 1 | 122 |
| light_rain | 40 | FAIL | unhandled_at_end 1 | 23 | 277 | FAIL | unhandled_at_end 1 | 253 |

normal_day: agent pass 13/20, safety fails 1, stuck 14; tower pass 14/20, safety fails 1, stuck 39

light_rain: agent pass 5/20, safety fails 5, stuck 16; tower pass 8/20, safety fails 6, stuck 29

### Analyst fixes 1-4 on the port (measured, NOT adopted)

The analyst's four fixes (measured on the tower: ND 14 -> 18, LR 8 -> 9) were re-implemented in the ported planner and
measured on the same 40 development seeds. Gate for adoption: stuck must not go up, and the Referee safety-fail total
(collision + loss_of_separation + runway_incursion + runway_occupied) must not exceed the port's on either level.

| variant | ND pass | ND safety | ND stuck | LR pass | LR safety | LR stuck |
|---|---|---|---|---|---|---|
| port (committed) | 13/20 | 1 | 14 | 5/20 | 5 | 16 |
| fixes 1-4 + crossing re-issue guard | 13/20 | 2 (collision 1, runway_incursion 1) | 6 | 6/20 | 3 | 27 |
| fixes 1-3 + guard (no fix 4) | 13/20 | 1 (runway_incursion 1) | 6 | 6/20 | 3 | 27 |
| fixes 2-4 + guard, the port's own reroute retry instead of fix 1 | 14/20 | 2 (collision 2) | 4 | 6/20 | 5 | 10 |

Fix 2 (no line-up on a mismatched or over-tailwind end) held 0 aircraft over the 40 runs with fixes 1-4. No variant
meets the gate (fix 1's re-issue raises LR stuck 16 -> 27, LR24 alone 12; the best variant has one more ND collision
than the port), so none is committed; the code is kept outside the repo for the next round. Single-seed ablation
(LR24, LR21, ND20) shows fix 1 swings outcomes both ways (LR24 PASS without it, 12 stuck with it). LR35 (pass ->
taxi-in/taxi-out collision in the analyst's tower prototype) is a known regression of the collision class; in the port
LR35 already fails on one collision before any fix.

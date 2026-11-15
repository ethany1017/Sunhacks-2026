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

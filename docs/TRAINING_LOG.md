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

Human reference on levels-v1 (not tuned toward): ND 5/5, LR 1/5 (4 collisions over LR).

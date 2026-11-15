# The Human controller (`sim/human/controller.py`)

`Simulation(..., controller="human")`, `sim.set_controller("human")` or `python -m sim.core.run --level normal_day --seed 1 --controller human`.

The Human is the built-in tower's rulebook (`sim/tower/controller.py`) worked by a person. It makes the same decisions
from the same inputs (`observe()`, the published airport graph, the tool methods; nothing else), with four human limits
added. Every number is a named constant at the top of `sim/human/controller.py`; this table is the record of where each
one comes from. None of them were tuned to make a run pass or fail.

| Constant | Value | What it does | Source |
|---|---|---|---|
| `REACTION_MEDIAN_S` | 2.5 s | Median time from an instruction becoming due to the Human sending it. The need must last that long (a need that goes away resets the clock). | AASHTO *A Policy on Geometric Design of Highways and Streets* (Green Book), design perception-reaction time 2.5 s. This is a road-driver figure used as a stand-in. **Assumption** for a tower controller; no ATC-specific value was found in the repo, and the FAA reports looked up (below) are scanned PDFs whose text could not be read. |
| `REACTION_SIGMA` | 0.4 (lognormal) | Spread of that time: middle 90% about 1.3 to 4.8 s. | **Assumption.** Lognormal is the usual shape for human response times. |
| `REACTION_MAX_S` | 8 s | Cap on one ordinary reaction. | **Assumption.** |
| `LATE_P` | 5% | Chance that an instruction is late: attention was on something else. | **Assumption.** |
| `LATE_EXTRA_S` | 5 to 15 s (uniform) | How much later a late instruction comes, on top of the reaction time. | **Assumption.** |
| `TX_PER_MIN`, `TX_WINDOW_S` | 10 per rolling 60 s | Transmission budget: the most fresh instructions the Human sends in any minute. Above it, instructions wait their turn in the tower's priority order (aircraft holding short first, then first come, first served). | **Assumption** derived from the sim's own timings. An instruction takes about 3 s to say (assumption). The pilot's readback comes 1.5 to 3.0 s later (`docs/ASSUMPTIONS.md`, scenario readback delay) and takes about 2 s. So one exchange is about 6 s, and 10 a minute keeps the frequency about 100% busy. Studies of transmission density exist (Burki-Cohen 1995, DOT/FAA/AR-96/19, tower ground; Cardosi 1994, tower local), but their numbers could not be read from the scans. |
| `EXTRA_DEP_GAP_S` | +15 s | Added to the tower's departure gap (60 / 90 / 120 s from roll to roll). | **Assumption:** controllers keep a buffer above the minimum rather than working to it. |
| `EXTRA_GUARD_S` | +10 s | Added to the tower's landing guards: no line-up within 110 s of a landing, no takeoff roll within 65 s, no crossing within 100 s. | **Assumption** (same reason). |
| `MISS_WRONG_READBACK_P` | 20% | Chance that a wrong readback (wrong runway, or "Roger" with no runway) is not caught: the Human hears what it expected and lets it stand. Caught ones are corrected exactly as the tower does. | **Assumption.** Cardosi, Falzarano & Han 1998 (DOT/FAA/AR-98/17, ASRS reports) found readback/hearback errors were 47% of the reported communication errors: missed readback errors are the biggest single kind. That is a share of *reported errors*, not a per-readback miss rate, so the 20% value is not taken from it. How often pilots read back wrong in the first place is the level's `READBACK_ERROR_RATE` (1%, Cardosi FAA/Volpe, `docs/LEVELS.md`). |
| `RNG_TAG` | 7349 | Mixed into the seed so the Human's random stream never coincides with the world's. | Implementation. |

## How it works

- **Reaction time and late instructions.** The first time the tower logic wants to send instruction *X* to aircraft
  *A*, the Human draws a delay. It sends *X* only once *X* has stayed due for that whole time (one draw per need). If
  the need lapses first, the draw is forgotten. Holding back an instruction undoes what the decision noted about that
  aircraft, so the rulebook simply decides again on the next pass (2 Hz).
- **Transmission budget.** It counts every instruction sent (taxi, hold, cross, line-up, takeoff, landing, go-around,
  pushback, frequency change) in a rolling minute. Readback corrections ("say again") and runway-configuration
  changes are not counted. When the budget is used up, nothing new is sent until a slot frees up; the ground probe's
  holds wait their turn like everything else.
- **Missed readbacks.** It draws once per wrong readback. An accepted wrong hold-short readback can end in a runway
  incursion; the Referee judges that from the true state.
- **Randomness.** It uses its own `numpy` generator, seeded from `observe()["level"]["seed"]` (scenario files: seed 0),
  so a run is repeatable per seed. It never touches the world's generators.
- **Observe only.** `tests/test_human.py` runs both the Human and the tower behind a proxy that raises on any
  simulation attribute other than `observe`, the tool methods and `graph`.

## Results (S4, training seeds only)

See the S4 report for the tower and Human tables on Normal Day 1-5 and Light Rain 21-25. The Human passes the same
seeds as the tower on Normal Day (2, 4, 5) and Light Rain (23). Its delays are higher on the seeds that pass, and it
missed one wrong readback on each of Normal Day 5 and Light Rain 21.

S5 (level calibration proposal, not yet frozen): the Human's results on the calibrated levels are in
`docs/LEVELS.md`, "S5 calibration proposal". The S4 numbers above were measured on the earlier levels.

## Sources looked up

- Cardosi, Falzarano, Han (1998), *Pilot-Controller Communication Errors: An Analysis of ASRS Reports*, DOT/FAA/AR-98/17 (via search summary).
- Burki-Cohen (1995), *An Analysis of Tower (Ground) Controller-Pilot Voice Communications*, DOT/FAA/AR-96/19, https://rosap.ntl.bts.gov/view/dot/8641 (scanned; abstract only).
- Prinzo & Hendrix (2009), *The Outcome of ATC Message Length and Complexity on En Route Pilot Readback Performance*, DOT/FAA/AM-09/2 (scanned; not read).

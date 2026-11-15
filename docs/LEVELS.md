# Levels (S3)

Two seeded KPHX days, built into the simulator: **Normal Day** (`normal_day`) and **Light Rain** (`light_rain`). A level
is `(level_id, seed, duration_s)` -> a `Scenario` (the traffic) plus a `LevelWeather` (scheduled METAR reports and the
physical effect multipliers). Everything lives in `sim/levels.py`; every tunable value there is a plain named constant
with its source. S5 calibration proposal below (**NOT YET FROZEN, awaiting Ethan's approval**; no version tag yet).

Run one: `python -m sim.core.run --level normal_day --seed 1 [--duration 1500]`, or in code
`Simulation(level="light_rain", seed=21, duration_s=None, controller="tower")`. Scenario files (`kphx_smoke.json`,
`kphx_busy.json`) still load exactly as before, with calm static weather.

## Seeds

| Level | Training (tune on these) | Held-out (grading only) |
|---|---|---|
| normal_day | 1, 2, 3, 4, 5 | 9001, 9002, 9003 |
| light_rain | 21, 22, 23, 24, 25 | 9021, 9022, 9023 |

`sim/levels.py: TRAINING_SEEDS, HELD_OUT_SEEDS`. The traffic depends on the seed only, so the same seed gives the same
day in both levels (Light Rain seed N = Normal Day seed N plus rain). A level without `seed` uses its first training seed.

## Traffic (same for both levels)

| Name | Value | Source |
|---|---|---|
| `DEFAULT_DURATION_S` | 1500 s (25 min) of scheduled traffic; the Referee's day ends 900 s later | assumption (spec: 20-30 min) |
| `HOURLY_CSV` | scheduled arrivals and departures per local hour | BTS On-Time Performance, PHX, Aug 2026 (`data/kphx_traffic/bts_otp_2026_8/derived/phx_hourly_summary.csv`) |
| `DAY_HOURS` | local 06-17 (12 hours, each shown as `duration/12` = 125 s at that hour's real rate) | assumption; keeps the real banks (arrivals 09h 45/h, departures 08h 57/h) and lulls (11h, 13h) |
| `DEMAND_SCALE` | 1.0 (= the real BTS rate) | S5: kept (0.9, 1.1 and 1.2 all made the Human fail more Normal Day seeds) |
| counts | expected count per hour slice, rounded with a carried random remainder (systematic sampling), not Poisson: 11-12 arrivals and 13 departures per default day (24-25 flights) | S5. BTS Aug 2026, PHX 06-17 local: 373 +- 9 departures and 340 +- 7 arrivals per day (sd 2-3%); a Poisson draw gave the compressed day 21-35 flights (sd about 20%), which is not "an average day" |
| `ARRIVAL_LEAD_S` | 150 s from the hour slot to touchdown (an arrival enters the 11 km final at its slot) | assumption |
| `AIRLINES` | weights WN 9955, AA 8219, OO 3947, MQ 2200, DL 1672, UA 1438, F9 1079, AS 916; fleets from the KPHX aircraft CSV | BTS Aug 2026 carrier counts (`phx_summary.txt`) |
| `HEAVY_SHARE`, `HEAVIES` | 4%: Hawaiian A332, FedEx B763 | assumption (BTS HA about 2 flights/day; cargo is not in BTS) |
| `LIGHT_SHARE`, `LIGHTS` | 5%: C172, SR22, PC12, C208 from GA stands | assumption: Sky Harbor reports about 1,335 operations/day vs 952 BTS flights/day; the rest is GA, cargo and non-reporting carriers |
| `MIN_HEAVY_ARRIVALS`, `MIN_LIGHT_ARRIVALS` | 1 each per day | assumption, so the CWT wake table always matters on final |
| `READBACK_ERROR_RATE` | 1% of runway-bearing clearances (wrong side or "Roger") | Cardosi, FAA/Volpe en route and TRACON studies: fewer than 1% of clearances led to a communication error. Same in rain (assumption) |
| `PUSHBACK_MEAN_S` | 282 s (sigma 10%) | NASA NTRS 20190027149 (Charlotte, 4.72 min) |
| `GATE_SPACING_M` | gates at least 70 m apart, one flight per gate (44 airline gates, 80 GA stands) | assumption: OSM gate points are 9-40 m apart, closer than an airliner is long |
| `ARRIVAL_STREAMS` | 25L and 26 (default west flow); a spawn goes to its stream's runway in the active flow (25L -> 07R, 26 -> 08) | KPHX west/east flow pairs |
| `SPAWN_MARGIN_NM` | 1.0 NM above the radar / CWT wake minimum, used for the schedule (both levels, so the traffic stays the same) | assumption |

**Arrival spacing at spawn.** Each arrival gets the earliest slot on either stream that is at least
`arrival_gap_s(leader, follower) = (max(3 NM, CWT wake NM) + 1 NM) / min(Vref leader, Vref follower)` behind the previous
arrival on that stream (wake from `sim/referee/limits.py WAKE_NM`, the Referee's own table). At run time approach
control also holds an arrival back (event `arrival.held`) while a missed approach is being resequenced on that final or
a dependent one, or while an arrival ahead is closer than `arrival_gap_s(leader, follower, spacing_margin_nm)` times
(follower Vref + 20 m/s), with the level's `spacing_margin_nm` (1 NM dry, 2 NM in rain); a held arrival enters
at the full 11 km final once clear. Time held this way is approach control's, not the tower's: it does not count toward
the diversion clock. The 25L and 26 finals are 4,373 ft apart, independent under the Referee's 5-9-7 rule.

## Weather and the runway configuration change (both levels)

| Name | Value | Source |
|---|---|---|
| `WIND_START` | 270 at 8 kt (headwind for west flow) | assumption |
| `WIND_SHIFT_FRAC` | the shift lands at 45% of the scheduled day (675 s by default, about 11:24 local); the tower turns the airport round at the -180 s step (about 495 s, 10:00 local, after the 09h arrival bank) | assumption; S5 moved it from 0.55 (see below) |
| `WIND_STEPS` | shift -300 s: 180/06 (crosswind); -180 s: 130/07 (5.4 kt tailwind on west flow); 0 s: 090/14 (14 kt tailwind) | assumption (an easterly wind shift; the 2025 haboob replay data shows KPHX in east flow) |
| `TAILWIND_LIMIT_KT` | 10 kt | typical transport-category AFM tailwind limit (pilot behaviour, not a Referee rule) |
| METAR | built from the fields; the clock runs 06:00-18:00 local (13:00-01:00Z) over the day | |

The tailwind component is `-wind_kt * cos(wind_dir - runway heading)` on the steady wind. Above the limit pilots refuse:
a `cleared_to_land`, `line_up_and_wait` or `cleared_for_takeoff` is answered "Unable ..., tailwind N knots" instead of
a readback (event `pilot.unable`, a pilot voice line, the clearance is cancelled); an arrival already cleared goes
around at the 1,800 m decision point; a departure already cleared for takeoff will not start its roll. A controller
that does not turn the airport round therefore collects go-arounds, unable calls, delays and finally diversions and
stuck departures under the existing Referee rules. Switching direction (`set_runway_config` with the other ends,
then each arrival's new runway through `contact_tower_or_ground` / `cleared_to_land`, each departure's through a new
`taxi_via`) fixes it. An arrival given the opposite direction flies round at 3,000 ft (`REVERSAL_ALT_M`, assumption) to
the new final's 8 km re-entry point (event `arrival.reversed`).

## Per level

| Name | Normal Day | Light Rain | Source |
|---|---|---|---|
| visibility / weather / sky | 10 SM, none, FEW200 | 4 SM, `-RA BR`, BKN015 OVC030 | assumption (MVFR light rain and mist) |
| temperature / dew point | 29 / 02 C | 21 / 18 C | assumption |
| `taxi_mult` | 1.0 | 0.8 (all taxi speed limits) | assumption |
| `occupancy_mult` | 1.0 | 1.15: landing roll slower by 1.15, takeoff acceleration / 1.15^2 | 14 CFR 121.195(d): wet-runway landing distance is 115% of dry |
| `approach_mult` | 1.0 | 0.6 of the extra speed above Vref on final (Vref + 12 m/s easing to Vref instead of + 20) | assumption |
| `go_around_rate` | 0.002 per approach | 0.05 per approach | Flight Safety Foundation go-around study (2017): about 1-3 per 1,000 approaches; rain value is an assumption |
| `spacing_margin_nm` | 1.0 NM | 2.0 NM above the radar / wake minimum when approach control hands an arrival to the tower | assumption: with a BKN015 ceiling approach control runs instrument approaches and adds miles-in-trail; the Referee minimum is unchanged |

A pilot-initiated go-around is drawn once per approach at the 1,800 m decision point, from its own random stream
(`[seed, 2]`), only for an arrival cleared to land (reason `pilot: unstable approach in -RA BR`, pilot voice line
"Going around"). Measured with the built-in tower (default day): average landing occupancy 69-77 s dry (seeds 1-3) vs
82-91 s wet (seeds 1, 2, 21-23); taxi top speed 0.8x and approach speed above Vref 0.6x (tests/test_levels.py). Visibility itself has no further physical effect beyond these
multipliers (assumption); it is reported to controllers.

The rain spacing is applied at run time only (`_spawn_clear` in `sim/core/engine.py`), so the schedule, and therefore
the traffic, stays the same for one seed in both levels. Because the gap is converted to distance at Vref + 20 m/s,
2 + 3 NM comes to about 11.9 km for a jet pair (more for a light follower or a heavy leader), longer than the 11 km final:
in rain approach control hands over one arrival per final at a time; dry, a second jet can enter about 9.5 km behind.

## Measured: Light Rain vs Normal Day, same traffic (before S5)

Measured before S5 (Poisson counts, `WIND_SHIFT_FRAC` 0.55), kept for the record; the traffic per seed has changed since.

Built-in tower, default day (1500 s, Referee day end 2400 s), same seed number in both levels, after the rain spacing
margin. "Held" = arrivals approach control held back (`arrival.held`) and the total time from their first spawn attempt
to entering the final (or to the day end); this includes holds behind a missed approach, which dominate the totals.
"Airborne holding" = sum of `holding_s` (time on final past the scheduled landing time). "Late" = touchdown minus
scheduled landing time, average / max over arrivals that landed. "Peak" = arrivals scheduled in the 07h and 09h slices
(the two arrival banks, 36.7 and 50.1 arrivals/h in BTS; 275-400 s and 525-650 s of sim time).

| seed | level | held (n / s) | airborne holding (s) | late avg / max (s) | peak arrivals: n, held, late avg (s) | completed / flights | avg / total delay (s) | avg landing occupancy (s) | go-arounds |
|---|---|---|---|---|---|---|---|---|---|
| 1 | dry | 2 / 1715 | 1218 | 3.0 / 7 | 3, 0, 2.8 | 19 / 29 | 138 / 2630 | 69.4 | 4 |
| 1 | rain | 5 / 1230 | 1548 | 62.8 / 354 | 3, 1, 13.6 | 18 / 29 | 162 / 2907 | 82.0 | 4 |
| 2 | dry | 3 / 689 | 1528 | 48.0 / 291 | 2, 0, 2.5 | 17 / 28 | 62 / 1056 | 76.9 | 5 |
| 2 | rain | 5 / 807 | 1661 | 67.2 / 326 | 2, 0, 8.4 | 17 / 28 | 196 / 3327 | 90.8 | 5 |
| 21 | dry | 5 / 2669 | 2492 | 197.5 / 968 | 3, 1, 11.2 | 20 / 34 | 146 / 2926 | 74.4 | 7 |
| 21 | rain | 6 / 2777 | 2549 | 125.3 / 667 | 3, 1, 26.0 | 19 / 34 | 237 / 4496 | 88.3 | 7 |
| 22 | dry | 4 / 705 | 1261 | 9.1 / 58 | 3, 0, 2.5 | 14 / 25 | 75 / 1053 | 76.1 | 4 |
| 22 | rain | 4 / 783 | 1417 | 22.2 / 103 | 3, 0, 8.5 | 18 / 25 | 159 / 2869 | 90.1 | 4 |

What this shows: rain holds as many or more arrivals at the handover (4-6 vs 2-5), adds airborne holding on every seed
(+57 to +330 s), raises landing occupancy by 12-15 s and the average delay by 23-134 s, and completes one flight fewer
on seeds 1 and 21 (the same on seed 2, four more on seed 22). What it does **not** show: capacity below demand in the peaks. The default
day has only 11-14 arrivals, 2-3 of them in each measured peak; in rain peak arrivals land 6-15 s later than dry and
at most one per peak is held (13-27 s). So the rain effects are real and measurable but, at `DEMAND_SCALE` 1.0, the
peak demand does not exceed the rain capacity (one arrival per final at a time, about 24 per hour per final). Raising
`DEMAND_SCALE` (S5) is what would make the peaks exceed it. All eight runs end `REFEREE: FAIL` (stuck departure queues
at the hold-short points after the flow change, a diversion and unhandled traffic, plus ground collisions on some),
in both levels (Normal Day is unchanged by the rain margin; Light Rain seeds 1 and 21 also failed before it), so the totals above mix the rain effect with the built-in tower's failures.

## S5 calibration proposal (NOT YET FROZEN, awaiting Ethan's approval)

Calibrated against the Human controller only (`--controller human`), on training seeds only. Nothing about the Human,
the tower, the Referee or its limits, or the physics changed.

**Choice: the two levels share the traffic per seed and differ only in the rain effects** (as S3 built them). The brief
says Light Rain is the same day shape; sharing the traffic keeps the comparison clean (rain is the only difference).

What was tuned and why:

| Parameter | Old | New | Why | Source |
|---|---|---|---|---|
| hourly counts (`build_scenario`) | Poisson per hour slice | expected count, carried random remainder | a Poisson draw made "an average day" anything from 21 to 35 flights (Normal Day seed 3 had 22 arrivals against an expected 12 and diverted one); real scheduled traffic barely varies day to day | BTS Aug 2026 PHX, 06-17 local: sd 2-3% of the daily count |
| `WIND_SHIFT_FRAC` | 0.55 | 0.45 | with the new counts the Human passed Normal Day on 4/5 seeds at 0.55; the failures were gridlocks and stuck departures at the 07L hold point after the flow change. Human Normal Day passes by shift time: 0.35: 3/5, 0.40: 5/5, 0.45: 5/5, 0.50: 3/5, 0.55: 4/5, 0.70: 3/5, 0.85: 4/5. 0.45 is the middle of the 5/5 range. This sweep ran on the same five training seeds the results table below reports, and the pass count moves without a trend (it looks like seed noise), so the Normal Day 5/5 is partly fitted to these seeds | assumption (when an easterly shift comes is a choice; one shift per day as S3 asked) |

Tried and not adopted (Human, training seeds, new counts): `DEMAND_SCALE` 0.9 (Normal Day 2/5 pass), 1.1 (1/5),
1.2 (1/5), all at the old shift time 0.55; at shift 0.45, Light Rain `spacing_margin_nm` 2.5 (Light Rain 5/5 fail, but 3.0 gives 4/5
again: seed 24 flips by chance, not by capacity), `go_around_rate` 0.03, `taxi_mult` 0.75, `approach_mult` 0.4 (each
still 4/5 fail, seed 24 passes). The rain values stay as S3 set them.

Measured, Human, default day (1500 s, Referee day end 2400 s). Delay = the score's delay: departures from ready at the
gate to airborne, arrivals from touchdown to the gate, each minus the unimpeded time. Readback errors = caught / missed
wrong readbacks.

| level | seed | verdict | fails by rule | completed / flights | avg / total delay (s) | dep / arr avg delay (s) | go-arounds | diversions | readback errors caught / missed |
|---|---|---|---|---|---|---|---|---|---|
| normal_day | 1 | PASS | - | 25 / 25 | 74 / 1848 | 88 / 58 | 0 | 0 | 0 / 0 |
| normal_day | 2 | PASS | - | 24 / 24 | 84 / 2005 | 83 / 85 | 0 | 0 | 0 / 0 |
| normal_day | 3 | PASS | - | 25 / 25 | 83 / 2075 | 109 / 55 | 1 | 0 | 0 / 1 |
| normal_day | 4 | PASS | - | 25 / 25 | 96 / 2405 | 109 / 83 | 0 | 0 | 1 / 0 |
| normal_day | 5 | PASS | - | 25 / 25 | 82 / 2050 | 110 / 52 | 0 | 0 | 3 / 0 |
| light_rain | 21 | FAIL | unhandled_at_end 1 | 24 / 25 | 182 / 4369 | 201 / 163 | 0 | 0 | 1 / 0 |
| light_rain | 22 | FAIL | collision 1, stuck 4, unhandled_at_end 1 | 19 / 25 | 179 / 3398 | 188 / 168 | 0 | 0 | 0 / 0 |
| light_rain | 23 | FAIL | collision 1 | 25 / 25 | 182 / 4542 | 187 / 176 | 0 | 0 | 0 / 0 |
| light_rain | 24 | PASS | - | 24 / 24 | 192 / 4605 | 209 / 171 | 1 | 0 | 0 / 0 |
| light_rain | 25 | FAIL | collision 2, unhandled_at_end 1 | 23 / 25 | 276 / 6336 | 378 / 182 | 0 | 0 | 2 / 0 |

For reference, the built-in tower on the same seeds: Normal Day PASS on 1-5 (avg delay 58-88 s); Light Rain FAIL on 21
(collision 2), 22 (unhandled 1), 23 (collision 1), PASS on 24 and 25 (avg delay 163-225 s).

What this shows:
- Normal Day: the Human passes all five training seeds, but `WIND_SHIFT_FRAC` 0.45 was chosen by sweeping these same
  five seeds. Across the sweep the pass count moved without a trend (0.35: 3/5, 0.40: 5/5, 0.45: 5/5, 0.50: 3/5,
  0.55: 4/5, 0.70: 3/5, 0.85: 4/5), which looks like seed noise, so the 5/5 is partly fitted to these seeds. Across
  all 35 sweep runs the Human passed 27 (about 77%), so on unseen seeds expect an occasional Human fail; the failures
  are its own ground gridlocks after the flow change.
- The S5 Light Rain target is only partly met: the spec wants the Human to fail Light Rain on traffic not handled
  (diversions / backlog), but only 3 of 5 training seeds fail on traffic not handled (21, 22, 25; 22 and 25 also
  include collisions), 1 of 5 fails on a collision only (23), and 1 of 5 passes (24).
- Light Rain: the Human fails four of five. Delays roughly double (average 179-276 s against 74-96 s dry). The fail
  mix is honest, not all traffic handling: seed 21 is backlog only (unhandled at the day end); 22 and 25 mix ground
  collisions with stuck / unhandled traffic; 23 is a ground collision only (AAL1585 and a GA aircraft at t=443, before
  the flow change; the same collision also happens at shift 0.55, and seed 23 has a collision at every shift time tried). Seed 24 passes with its last flight done 69 s
  before the day end. No diversions: the rain bottleneck is the ground (slower taxi, longer occupancy), not the finals.
- Rain capacity on final (one arrival per final at a time with the 2 NM margin, about 24 an hour per final) is only
  just below the 09h bank (50 arrivals an hour over two finals) at `DEMAND_SCALE` 1.0, so the rain failures come from
  taxi and runway time piling up through the day, not from arrival holding.

Options for Ethan on Light Rain:
- Accept the levels as they are (3 of 5 fail on traffic not handled, 1 on a collision only, 1 passes).
- Use `spacing_margin_nm` 2.5, which gave 5/5 fails in the sweep, but it is knife-edge: 3.0 gave 4/5 again (seed 24
  flips by chance).
- Calibrate on more training seeds before freezing.
- Change the target (for example, count a ground collision as an acceptable Light Rain fail).

**Compared with real PHX** (BTS Aug 2026, scheduled 06-17 local). The FAA's unimpeded taxi times are not in the repo,
so the 10th percentile stands in for unimpeded (assumption): taxi-out mean 16.9 min vs p10 10 min, about 7 min (415 s)
excess; taxi-in mean 7.2 vs p10 4 min, about 3 min (190 s) excess. The Human's Normal Day delays (departures 83-110 s,
arrivals 52-85 s) are about a quarter of the average excess out and a third in: a taxi-out of about 11.7 min, between
the real p10 (10) and p25 (12), and a taxi-in of about 5 min (p25). That is a good real day, below the average. In
Light Rain (departures 187-378 s, arrivals 163-182 s) the delays reach the real average. Two reasons the sim runs
lighter than the real airport: it has no ramp or gate congestion, and it carries the BTS airline traffic only (about
952 of Sky Harbor's 1,335 operations a day). `DEMAND_SCALE` 0.9, 1.1 and 1.2 all gave more Human Normal Day fails
than 1.0 (2/5, 1/5 and 1/5 pass against 4/5, all at shift 0.55), so the extra fails cannot be put down to demand alone.

## What a controller sees

`observe()["level"]` and `snapshot()["level"]` (None for scenario files):
`{"id": "light_rain", "name": "Light Rain", "seed": 21, "duration_s": 1500.0, "tailwind_limit_kt": 10.0,
"effects": {"taxi_mult": 0.8, "occupancy_mult": 1.15, "approach_mult": 0.6, "go_around_rate": 0.05,
"spacing_margin_nm": 2.0}}`.
`observe()["weather"]` is the current METAR-like report (same keys as before), updated at the scheduled times (event
`weather.update` with the new report). The future wind is not shown: a controller sees the wind change as it happens,
as from the ATIS.

## Simulator changes made for the levels (physics and pilots, not the Referee)

- Hold points: nose 30 m short of the runway pavement (was 8 m), so a wide-body on the runway clears a holding aircraft
  (an A332/B77W wingtip reached aircraft held 8 m short). Real hold lines are about 250 ft from the centreline for
  Category C/D runways (FAA AC 150/5300-13, not re-verified), about 53 m from a 150 ft runway's edge.
- See and avoid on the ground: a taxiing aircraft stops for any aircraft right ahead closer than the two half lengths
  plus 15 m, on its path or not (aircraft drove through each other when one turned off a shared taxiway).
- A stopped aircraft may be given an explicit route that starts at the node behind it (it turns round where it stands;
  `observe()` field `prev_node`). Needed to send a queued departure to the other end after a flow change.
- An aircraft rejoining after a go-around holds the re-entry altitude instead of descending toward the far threshold;
  its `eta_s` now counts the distance round to the re-entry point (it was negative, which made the tower order a
  go-around every time an arrival rejoined).
- Referee wording only: a separation fail prints the distance truncated to 2 decimals, so 2.996 NM reads 2.99, not 3.00.

## Known hazards and limits

- Go-arounds and reversals cannot be vectored: the rejoin turn (3 deg/s, about 1 km radius for a slow type) can drift
  toward the parallel final. Seen once in six runs (normal_day seed 3: a C208 rejoining 08 and a 737 on final 07R).
- Arrival delay in the score is measured from touchdown to the gate (unchanged); airborne delay shows as `holding_s`,
  go-arounds and diversions.
- Gate reuse starts only beyond 44 airline flights per day (`DEMAND_SCALE` above about 1.4).

# Levels (S3)

Two seeded KPHX days, built into the simulator: **Normal Day** (`normal_day`) and **Light Rain** (`light_rain`). A level
is `(level_id, seed, duration_s)` -> a `Scenario` (the traffic) plus a `LevelWeather` (scheduled METAR reports and the
physical effect multipliers). Everything lives in `sim/levels.py`; every tunable value there is a plain named constant
with its source. Not yet calibrated or frozen: S5 tunes these values against the Human controller and records a version
tag here.

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
| `DEMAND_SCALE` | 1.0 (= the real BTS rate) | S5 tunes |
| counts | Poisson per hour slice: about 11-14 arrivals and 13-20 departures per default day | follows from the above |
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
| `WIND_SHIFT_FRAC` | the shift lands at 55% of the scheduled day (825 s by default, about 12:36 local, the mid-day lull) | assumption |
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

## Measured: Light Rain vs Normal Day, same traffic

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

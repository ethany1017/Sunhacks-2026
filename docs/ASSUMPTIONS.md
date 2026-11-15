# Assumptions and placeholders (backend)

Every number below is a placeholder chosen to make the simulation behave plausibly. None is a measured KPHX value unless it says so. Constants live in the file named in the last column.

## Graph (`sim/graph/build_graph.py`)

| Item | Value | Why |
|---|---|---|
| Local metric frame | azimuthal equidistant at 33.4343 N, 112.0116 W | distances in meters without UTM scale error |
| Hold position attach distance | 15 m (BUILD_PROMPT 5.1); holds 15 to 40 m away are still attached and counted | spec; keeps holds that sit slightly off the OSM line |
| Dead-end snap distance | 6 m | joins a way end to a neighbouring way when OSM missed the shared node |
| Stand attach distance | 250 m, nearest taxilane/taxiway, taxilane preferred by 20 m | gates in OSM are points inside buildings; a straight "lead" edge connects each to the lane |
| Stands | all 111 `gate` nodes plus `parking_position` nodes/ways with a ref that are more than 25 m from a gate (190 `parking` stands) | parking positions cover GA, cargo and remote ramps |
| Runway centerline | OSM ways merged by `ref`; 08/26 includes the two displaced-threshold ways, so it is 3,626 m (FAA pavement about 3,502 m) | spec says merge by ref |
| Runway pavement | FAA polygons; a taxi edge is "on pavement" when its midpoint is inside the polygon (+0.5 m) | one occupancy resource per runway |
| Crossing nodes | where a taxi way meets the pavement boundary (142) | a hold short stop point sits just before one |
| Hold `runway` | nearest runway polygon within 250 m for `runway` and `ILS` hold types, otherwise null | OSM does not name the protected runway |
| Defect counts | dead ends 44 (spec expected 82), unnamed taxiway ways 57 (56 inherited a neighbour's letter, 1 labelled unknown), holds detached beyond 15 m: 4 (spec expected 28) | measured on the file on disk; the spec numbers do not reproduce with these definitions |
| Region subset | `south`: lon -112.04..-111.98, lat 33.42..33.4375 | the two south runways plus terminal 4 |

## Kinematics (`sim/graph/model.py`, `sim/core/physics.py`, `sim/core/aircraft_types.py`)

| Item | Value | Why |
|---|---|---|
| Tick | 100 ms fixed; speeds 1, 2, 5, 10, 20x | spec |
| Taxi speed | 15 kt straights, 8 kt in turns and on stand leads, 5 kt across runway pavement | spec |
| Turn detection | heading change over 15 m before and after a point greater than 25 degrees | derives the turn speed limit from geometry |
| Taxi acceleration, planned braking, emergency braking | 0.9, 1.3, 3.0 m/s^2 | smooth motion with no instant stops |
| Car-following gap | 60 m bumper to bumper, corridor 30 m either side of the path | spec |
| Crossing right of way | lower scenario sequence wins; a stopped aircraft always blocks; head-on traffic blocks both | prevents symmetric stalls; head-on gridlock is left to be logged as a deadlock |
| Hold line offset | nose 8 m short of the pavement | keeps the tail and wingtips clear |
| Pushback | mean 282 s (4.7 min, spec default), 10% sigma, 1.1 m/s over the lead; the demo scenarios override the mean (smoke 60 s, haboob 75 s) so a run stays under about 15 minutes | demo pacing |
| Heading rate | 25 deg/s on the ground, 3 deg/s in the air | smooth turns |
| Final approach | starts 11 km out on a 3 degree glide, speed Vref + 20 m/s easing to Vref over the last 9 km; Vref from the CSV (`approach_speed_kt_acd_vref_proxy`); touchdown at the threshold | no flare model |
| Landing roll | touchdown at 0.96 Vref, constant deceleration to a 10 m/s exit speed at the chosen exit (2.2 m/s^2 medium, 2.0 heavy, 1.4 light as the planning value); exit chosen at 1.25x the needed distance | measured landing runway occupancy is about 55 to 65 s (spec target 55 s) |
| Takeoff roll | liftoff at 1.13 x Vref; mean acceleration 2.4 m/s^2 medium, 2.1 heavy, 1.5 light; climb 8 m/s; removed 40 s after liftoff | scales roll with type |
| Go-around | triggered when no landing clearance by 1,800 m from the threshold (or on a go-around clearance); 35 s straight climb, then back to a point 8 km out | demo pacing |
| Aircraft size | wingspan and length from the CSV (no-winglet span preferred); a ground vehicle is 3 x 6 m | icon size |
| Observation | delay 1 s; position noise 3 m rms (2.1 m per axis) | spec defaults; per-scenario dropouts are not implemented |

## Separation and conflict logging (`sim/core/engine.py`)

| Item | Value |
|---|---|
| Airborne separation loss | pair on final to the same runway closer than 2.5 nm (4,630 m); different runways closer than 1,500 m |
| Ground separation loss | wingtip clearance below 5 m |
| Runway conflict | two entities on one pavement at once (`runway_overlap`), or touchdown on an occupied runway |
| Deadlock | a wait-for cycle between stopped aircraft for 60 s |
| Ground aircraft vs reservations | entering pavement is blocked if someone is on it or holds an earlier crossing/line-up reservation, or a landing/takeoff reservation overlaps the next 25 s (physics, not a rule applied to the agent) |
| Reservation windows | landing: ETA-8 s to ETA+65 s; takeoff: 70 s; line-up 60 s (extended while waiting); crossing: time to cross plus 8 s |

## Weather (`sim/weather/replay.py`)

| Item | Value | Why |
|---|---|---|
| Before the trigger | first CSV row (calm, 10 SM) held constant | demo must not depend on a live storm |
| Replay | rows from 2025-08-26 00:20 UTC, 2 sim seconds per CSV minute, starting at the trigger (sim 180 s in the haboob scenario, or POST /control `trigger_haboob`) | compresses the storm to about 4 sim minutes |
| Low visibility | vis < 1 SM: taxi speed x0.6; runway occupancy +20% (landing roll speed /1.2, takeoff acceleration /1.44) | spec, flagged as assumptions |
| `haboob_progress` | 0 before the trigger, 0.5 when vis first drops below 1 SM, 1.0 when vis returns to 2 SM or better | drives the dust wall visual |
| Missing wind in a row | carried forward from the previous row | many 5-minute rows omit wind |

## Traffic (`sim/scenarios/generate.py`)

Seeded (smoke seed 11, haboob seed 25). Airline weights SWA 40, AAL 24, SKW 10, DAL 7, UAL 6, ENY 5, FFT 4, ASA 4 (placeholders for the BTS carrier mix). Fleets per airline are drawn from the CSV types. Gates are spread over concourses by first letter and must have a route to the 25R and 26 entries. Smoke: 3 arrivals (threshold times 95, 205, 315 s) and 3 departures (ready 5, 70, 140 s), no weather, no readback errors. Haboob: 6 arrivals (75 to 395 s) and 6 departures (ready 0 to 145 s) including one general aviation Cessna/Cirrus/PC-12 from the GA ramp, one ground vehicle patrolling 300 to 1,000 m of taxi route kept 70 m clear of the aircraft routes, trigger at 180 s, readback error rate 10%. Pilot readback delay 1.5 to 3.0 s. The scenario window (`duration_s` 480) is when flights are scheduled; a run continues until every flight is complete (about 14 minutes for the haboob because departures held for the storm clear afterwards).

## Baseline controller (`sim/agent/baseline.py`)

First come, first served, 2 Hz decision pass. Landing clearance inside 70 s ETA when the runway is free; go-around instruction at 22 s if still blocked; 100 s minimum slot between landings per runway; no line-up if a landing is due within 100 s; no takeoff roll if a landing is due within 55 s; no crossing if a landing is due within 90 s; crossing window 45 s. Departure spacing 60 s, 90 s for a light behind a medium, 120 s behind a heavy (a stand-in for the 7110.65 wake table, not the table). Runway choice weighs taxi distance, 40 s per crossing, 90 s per aircraft already queued, 60 s if the runway also takes arrivals. Default configuration: arrivals 25L and 26, departures 25R and 26. Low-visibility mode when vis < 3 SM or gust >= 40 kt: departures only on 25R, new pushbacks and takeoffs held; restored after 30 s of calm.

## Scoring (`sim/metrics/score.py`)

Throughput is touchdowns plus liftoffs in the last 600 s. Clearance reversal: a hold or go-around that supersedes an earlier unfinished movement clearance. Average delay is actual time minus free-flow time (arrivals: touchdown to gate; departures: ready to liftoff, including pushback). Recovery time is seconds from visibility returning to 1 SM or better until the next takeoff. Landing occupancy is touchdown until the tail clears the pavement.

## Not built

`python -m sim.replay` (the event log records state changes, not positions, so it cannot reconstruct the position stream), per-scenario observation dropouts, and wake-separation inside the baseline are not implemented. The Claude controller (`sim/agent/claude_agent.py`, `tools.py`, `prompt.md`) is built and tested only against a fake client; no real model call has been made.

## Claude agent (`sim/agent/claude_agent.py`)

| Item | Value |
|---|---|
| Model | `claude-opus-5-5` at effort `low` (thinking is always on; low effort for speed), override with env `KPHX_AGENT_MODEL` / `KPHX_AGENT_EFFORT` |
| Wake | on events plus a 5 sim-second timer; one wake in flight; at most 8 tool calls and 15 s wall time each; budget exhaustion triggers an immediate follow-up wake |
| Threading | worker thread talks to the API; tool calls are queued and run by the simulation thread, so no locks |
| Failure | any API error: log (key scrubbed), baseline (primed with current clearances) covers for 5 s wall, doubling to 60 s, then Claude retries |
| Wake distance table | TBL 5-5-4 values from research_notes; the table numbering mismatch noted in data-catalog 3.12 is unresolved |
| `KPHX_AGENT_PAUSE_WHILE_THINKING=1` | pauses the clock during a wake (default off) |

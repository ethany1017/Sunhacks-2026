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
| Pushback | mean 282 s (4.7 min, spec default), 10% sigma, 1.1 m/s over the lead; the demo scenarios override the mean (smoke 60 s, busy 75 s) so a run stays under about 15 minutes | demo pacing |
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

## Weather (`sim/weather/calm.py`)

| Item | Value | Why |
|---|---|---|
| Report | `KPHX 151800Z 27005KT 10SM CLR 24/02 A3002`, held constant for the whole run | placeholder until weather comes from a level (S3) |
| Effect | taxi speed and runway occupancy multipliers both 1.0 (no effect) | calm weather |

## Traffic (`sim/scenarios/generate.py`)

Seeded (smoke seed 11, busy seed 25). Airline weights SWA 40, AAL 24, SKW 10, DAL 7, UAL 6, ENY 5, FFT 4, ASA 4 (placeholders for the BTS carrier mix). Fleets per airline are drawn from the CSV types. Gates are spread over concourses by first letter and must have a route to the 25R and 26 entries. Smoke: 3 arrivals (threshold times 95, 205, 315 s) and 3 departures (ready 5, 70, 140 s), no weather, no readback errors. Busy: 6 arrivals (75 to 395 s) and 6 departures (ready 0 to 145 s) including one general aviation Cessna/Cirrus/PC-12 from the GA ramp, one ground vehicle patrolling 300 to 1,000 m of taxi route kept 70 m clear of the aircraft routes, readback error rate 10%. Pilot readback delay 1.5 to 3.0 s; every readback waits for the controller's verdict, and 5 s of silence counts as acceptance (assumption: a controller who does not object lets the readback stand). The scenario window (`duration_s` 480) is when flights are scheduled; a run continues until every flight is complete or the day ends at `duration_s` + 900 s (`END_OF_DAY_GRACE_S`, event `scenario.day_end`).

## Built-in tower (`sim/tower/controller.py`)

First come, first served, 2 Hz decision pass. Landing clearance inside 70 s ETA when the runway is free; go-around instruction at 22 s if still blocked; 100 s minimum slot between landings per runway; no line-up if a landing is due within 100 s; no takeoff roll if a landing is due within 55 s; no crossing if a landing is due within 90 s; crossing window 45 s. Departure spacing 60 s, 90 s for a light behind a medium, 120 s behind a heavy (a stand-in for the 7110.65 wake table, not the table). Runway choice weighs taxi distance, 40 s per crossing, 90 s per aircraft already queued, 60 s if the runway also takes arrivals. Default configuration: arrivals 25L and 26, departures 25R and 26.

## Scoring (`sim/metrics/score.py`)

Throughput is touchdowns plus liftoffs in the last 600 s. Clearance reversal: a hold or go-around that supersedes an earlier unfinished movement clearance. Average delay is actual time minus free-flow time (arrivals: touchdown to gate; departures: ready to liftoff, including pushback). Landing occupancy is touchdown until the tail clears the pavement.

## Referee (`sim/referee/`)

Always on; judges the true state every tick, whoever controls, and never reads controller code or reports. Every limit and its source is in `sim/referee/limits.py` [DECIDE: Ethan reviews before S5]. Rules: `collision` (length x wingspan rectangles overlap, within 15 m vertically), `loss_of_separation` (3 NM radar minimum, 5-5-4a; the 2.5 NM final reduction is not applied because the sim's average landing occupancy is 57-62 s, above the 50 s 5-5-4j needs; CWT wake table in trail; 1000 ft vertical; parallels under 2500 ft count as one runway, 2500-4300 ft need the 5-9-6 diagonal, 4300 ft or more are independent (5-9-7, KPHX 26/25L are 4373 ft apart); under 500 ft is always a near mid-air), `runway_occupied` (landing or takeoff roll while another aircraft is on that pavement, or behind a departure that lifted off less than 6000 ft ahead, 3-9-6/3-10-3), `runway_incursion` (passing a hold point without an acknowledged cross / line-up / takeoff clearance for that runway, or touching down without a landing clearance; an accepted wrong hold-short readback therefore ends in an incursion), `diversion` (an arrival still airborne 600 s after its scheduled landing time and not cleared to land diverts and is removed), `stuck` (ready at the gate 300 s without pushback, stopped on the movement area 600 s, or an engine `deadlock`), `unhandled_at_end` (anything not parked or airborne at day end). Not checked: airborne departures against other traffic, arrival/departure spacing on short final, daylight same-runway distance reductions, ground vehicles crossing runways. The sim's go-around (straight ahead, then a 3 deg/s turn back to final) cannot be vectored by a controller, so it is protected like a final for parallel runways. Delays never fail a run; they are in the score.

## Not built

`python -m sim.replay` (the event log records state changes, not positions, so it cannot reconstruct the position stream), per-scenario observation dropouts, and wake-separation inside the tower are not implemented. The Claude agent is not part of the simulator; see `agent/README.md`.

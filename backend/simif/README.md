# Simulator Interface (`backend/simif/`)

The only way our backend and agent talk to the simulator. `simif/real.py` is the only backend module that imports
`sim.*` (it appends the repo root to `sys.path`). `backend/atc` and `backend/app` never import `sim`.

`simif.connect()` returns `RealSimulator` (the in-process KPHX simulator) by default; `SIMIF=stub` returns the old
offline stub (`simif/stub/`, airport layout only, no further work on it).

Units: lat/lon degrees WGS84, headings degrees true (0 = north), `t` and `*_s` sim seconds since the run started.

## Lifecycle

| Call | What it does |
|---|---|
| `get_levels()` | `[{id, name, scenario_path}]`. For now the two scenarios: `smoke` "Smoke test (6 aircraft)" and `busy` "Busy (12 aircraft)". The one `LEVELS` table in `real.py` is repointed when Normal Day / Light Rain land. |
| `get_controllers()` | `{"human": "Human (built-in tower for now)", "agent": "Agent"}`. |
| `reset(level, seed=None)` | New `Simulation` for the level id (seed `None` = the scenario's seed). It starts with the built-in tower; call `set_controller` next. `ValueError` on an unknown level. |
| `set_controller(kind, obj=None)` | `"human"` plugs in the sim's built-in tower (`sim.set_controller("tower")`, primed with live clearances mid-run); `"agent"` plugs in `obj` (an object with `name`, `status`, `on_tick(sim, events)`). Works at run start and mid-run. `ValueError` otherwise. |
| `get_level()` | `{id, name, scenario_path, seed}` of the current run. |
| `controller` | `"human"` or `"agent"`. |
| `t`, `finished` | Sim time; true once every flight is done or the day ended (`day_end_s`). |
| `step()` | One 0.1 sim-second tick. Returns that tick's events in our names (below). |

### `step()` events
Each is `{t, type, callsign, data, reason}` (ready for `app.db.emit_many`); `sim_end` always comes last in a tick.

| type | from sim event | data |
|---|---|---|
| `aircraft_spawn` | `aircraft.spawned` (not vehicles) | `kind` arrival/departure, `aircraft_type` (ICAO), `airline`, `gate`, `runway` (arrival end or null), `lat`, `lon`, `heading`, `phase` |
| `aircraft_exit` | `aircraft.removed`, `aircraft.parked` | `reason`: `departed` / `diverted` / `parked` (+ `gate`) |
| `referee_fail` | `referee_fail` | `rule`, `callsigns`, `reason`, `t` (rules: collision, loss_of_separation, runway_occupied, runway_incursion, diversion, stuck, unhandled_at_end) |
| `referee_result` | `referee_result` | `verdict` PASS/FAIL, `fails`, `score` |
| `sim_end` | `scenario.complete` / `scenario.day_end` | `why`: `complete` / `day_end` |

## Controller-safe views (from `observe()`: delayed, noisy; use these for decisions)

### `get_airport()` → `{config, geojson, graph}`
- `config`: `name`, `icao` ("KPHX"), `placeholder` (false), `map: {center: [lon, lat], zoom, bearing}`.
- `geojson` FeatureCollection, `properties.kind`:
  - `runway` LineString threshold to threshold: `id` ("08/26", "07L/25R", "07R/25L"), `width_m`, `ends: [{designator, heading, threshold: [lon, lat]}]`.
  - `taxiway` LineString, one per graph edge that is not runway: `name` (taxiway ref, may be ""), `edge_kind` (taxiway / taxilane / lead), `length_m`, `label` (true on the longest edge of each lettered taxiway: the one the map labels).
  - `hold_short` Point: `id` (graph node), `runway` it protects.
  - `parking` Point: `id` (stand graph node), `name` (gate/stand name), `stand_kind` (gate / parking).
- `graph`:
  - `nodes: {id: {lat, lon, kind}}`, kind `runway` (on a runway centreline) / `hold_short` / `parking` (stand node) / `taxiway`.
  - `edges: [{id, from, to, kind, name, length_m, pavement}]`: kind runway / taxiway / taxilane / lead, `name` = taxiway ref, `pavement` = runway whose pavement the edge crosses (else null).
  - `approaches: {designator: {runway, threshold_node, path}}`: the straight 11 km final the sim flies, `path` [lon, lat] from 11 km out to the threshold.
  - `crossings: [{node, runway}]`: nodes where taxiways cross runway pavement.
  - `stands: {stand name: node id}`.

### `get_state()` → `observe()` plus a few derived fields
- `t`, `data_age_s` (how stale the picture is), `runway_config` `{arrival_runways, departure_runways}`, `weather` (as `get_weather()`), `referee` (as `get_referee()`).
- `runways: [{id, name, status: free/reserved/occupied, owner, occupancy: [{callsign, start_s, end_s, kind}]}]`.
- `pending_readbacks: [{id, callsign, clearance_id, text, parsed: {action, runway}}]`.
- `clearances: [{id, callsign, action, text, state: issued/acknowledged, issued_t, ack_t, done_t}]` (live ones only).
- `aircraft: [...]`, each:
  - identity: `id`/`callsign`, `kind` arrival/departure/vehicle, `type` (ICAO), `airline`, `wake` (FAA CWT category A..I, from the type table), `wtc` (ICAO wake letter), `wingspan_m`, `length_m`.
  - position: `lat`, `lon` (noisy), `heading_deg`, `speed_mps`, `altitude_ft`.
  - progress: `phase` (final, landing_roll, runway_exit, taxi_in, at_gate, pushback, taxi_out, hold_short, line_up, takeoff_roll, airborne, go_around, parked, vehicle_moving), `status_text`, `gate`, `runway` (pavement), `arrival_end`, `dep_end`, `eta_s` (to next milestone), `ready`, `done`, `landing_cleared`, `takeoff_cleared`, `frequency`.
  - routing: `route`, `planned_route` (**always empty in observe()**, see gaps), `hold` `{runway, lon, lat}` when holding short, `next_node` (where an explicit taxi route must start), `entry_node`, `blocked_by`, `stopped_s`.
  - fuel: `holding_s` / `holding_limit_s` (arrivals: seconds airborne past the scheduled landing; diverts at the limit, 600 s).
  - `clearance`: the aircraft's latest live clearance (shape as `clearances`) or null.

### Tools
- `issue({callsign, action, params?})` → `{ok, clearance_id, voice_id, text}` or `{ok: false, error}`. Actions: taxi_via, hold_short, cross_runway, line_up_and_wait, cleared_for_takeoff, cleared_to_land, go_around, hold_position, resume_taxi, pushback_approved, contact_tower_or_ground (params: docs/API_CONTRACT.md). "Transmitted" only: the real acknowledgement is the readback.
- `get_readbacks()` → pending readbacks (shape as `pending_readbacks`) not returned by an earlier call.
- `resolve_readback(readback_id, accept)` → accept, or say again. Unresolved readbacks are accepted after 5 s of silence.
- `set_runway_config(arrival_runways, departure_runways)` → `{ok}`; runway ends like "26", "25L".
- `get_weather()` → `{metar, wind_dir, wind_kt, gust_kt, vis_sm, wx, temp_f, t}` (calm and static for now).
- `get_referee()` → `{verdict: RUNNING/PASS/FAIL, fails: [{rule, callsigns, reason, t}], day_end_s}`. No score (it would leak readback truth).

## Metrics only (true state; the backend loop and dashboard, never controller decisions)
`metrics()` → `{t, aircraft: [true snapshot views, same fields as API_CONTRACT Aircraft incl. route/planned_route], referee: {verdict, fails, score, day_end_s}}`. `score`: fails, fails_by_rule, completed, avg_delay_s, total_delay_s, go_arounds, diversions, readback_errors_caught, readback_errors_accepted (missed), runway_operations, throughput_per_10min and more.

## Gaps in the simulator (worked around here; for the sim branch)
- `observe()` aircraft have no wake category: the adapter looks it up from the sim's type table by ICAO type.
- `observe()` aircraft have no current clearance: the adapter attaches the latest live clearance from `observe()["clearances"]`.
- `observe()` aircraft `route` / `planned_route` are always empty (`with_routes=False`); the controller has to remember the routes it issued.
- No final-approach geometry is exported: the adapter rebuilds the straight 11 km final (`FINAL_START_M`) per runway end.
- No "readbacks since last call" query: the adapter remembers which pending readbacks it already returned.

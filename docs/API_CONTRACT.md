# API contract (backend <-> dashboard) and engine interface

Both sides build against this file. If something must change, change it here first and keep it small.

Conventions: `lon`/`lat` in degrees (WGS84), `heading_deg` 0 = north, clockwise, `speed_mps` meters per second, `t` / `*_s` simulated seconds since scenario start (float). Ports: API `http://localhost:8000`, dashboard dev server `http://localhost:5173`. Python target: **3.9** (system Python; use `from __future__ import annotations`, `typing.Optional`, no `match`).

## REST

- `GET /graph` -> GeoJSON FeatureCollection. Top-level extra key `meta`: `{"center":[lon,lat],"bounds":[west,south,east,north]}`. Feature `properties.kind` is one of `taxiway`, `taxilane`, `runway`, `runway_pavement` (Polygon), `apron` (Polygon), `taxiway_pavement` (Polygon, FAA taxiway surfaces, render under the lines; optional to draw), `stand` (Point, `name`), `hold` (Point, `runway`), `crossing` (Point). Taxiway/taxilane/runway are LineStrings with `ref` (letter or "08/26") and `id`.
- `GET /state` -> a snapshot (below).
- `POST /control` body `{"action": "pause"|"resume"|"speed"|"trigger_haboob"|"reset", "value": ...}` -> `{"ok":true,"state":{paused,speed,controller}}`. `speed` value is 1, 2, 5, 10 or 20.
- `GET /decisions/{id}` -> a Decision.
- `GET /health` -> `{"ok":true}`.

## WebSocket `ws://localhost:8000/ws`

On connect the server sends one `snapshot`, then a `delta` about every 100 ms (10 Hz). Messages are JSON.

### snapshot
```json
{"type":"snapshot","t":123.4,"speed":1,"paused":false,
 "agent":{"status":"idle","controller":"tower"},   (the active controller; always the built-in tower in the simulator)
 "scenario":{"name":"kphx_haboob","duration_s":480,"haboob_triggered":false},
 "weather":{...Weather},
 "aircraft":[...Aircraft], "runways":[...Runway], "clearances":[...Clearance],
 "decisions":[...Decision], "voice":[...VoiceLine], "score":{...Score}}
```
### delta
Same keys, all optional except `type` and `t`; lists contain only changed or new items (`aircraft` items are always complete objects). Extra key `removed`: list of aircraft ids to delete. Extra key `events`: list of log events `{"id","t","kind","entity_id","data"}` (for logging/debug).

### Aircraft
```json
{"id":"SWA1234","callsign":"SWA1234","type":"B738","kind":"arrival","airline":"SWA",
 "lon":-112.0116,"lat":33.4343,"heading_deg":262.0,"speed_mps":6.4,
 "phase":"taxi_in","status_text":"Taxiing to gate B12",
 "wingspan_m":35.8,"length_m":39.5,"gate":"B12","runway":"07L/25R",
 "route":[[lon,lat],...],"planned_route":[[lon,lat],...],
 "hold":{"runway":"07L/25R","lon":-112.0,"lat":33.43},"eta_s":95.0,
 "altitude_ft":0}
```
`kind` is `arrival`, `departure` or `vehicle`. `route` is the remaining cleared route (may be empty); `planned_route` is the agent's intended route beyond the clearance (may be empty); `hold` is null unless holding short. `phase` values: `final`, `landing_roll`, `runway_exit`, `taxi_in`, `at_gate`, `pushback`, `taxi_out`, `hold_short`, `line_up`, `takeoff_roll`, `airborne`, `go_around`, `parked`, `vehicle_moving`. `eta_s` seconds to next milestone or null.

### Runway
`{"id":"07L/25R","name":"07L/25R","status":"free|reserved|occupied","owner":"SWA1234"|null,"occupancy":[{"callsign":"SWA1234","start_s":120.0,"end_s":175.0,"kind":"occupied|reserved"}]}` (past 180 s and planned next 180 s).

### Clearance
`{"id":"c-17","callsign":"SWA1234","action":"cross_runway","text":"Southwest 1234, cross runway 7 left at Foxtrot 9","state":"issued|acknowledged|complete|amended|cancelled","issued_t":120.0,"ack_t":null,"done_t":null}`

### Weather
`{"t":120.0,"active":false,"metar":"KPHX 252300Z ...","wind_dir":290,"wind_kt":9,"gust_kt":null,"vis_sm":10,"wx":null,"temp_f":null,"haboob_progress":0.0}`; `haboob_progress` 0..1 drives the dust-wall visual (0 = not started, 1 = fully passed over the airport).

### Decision
`{"id":"d-9","t":120.0,"callsign":"SWA1234","action":"hold_short","summary":"Holding SWA1234 short of 07L/25R for the arrival","facts":["AAL22 on 2 mile final","07L/25R free for 40 s"],"rules":["Runway occupied: no crossing (JO 7110.65 3-7-2)"],"alternatives":[{"option":"cross now","reason":"conflicts with AAL22 landing in 38 s"}],"next_review_s":15,"voice_id":"v-31"}`

### VoiceLine
`{"id":"v-31","t":120.0,"speaker":"agent|pilot","callsign":"SWA1234","text":"Southwest 1234, hold short of runway 7 left","status":"ok|corrected|pending"}`. The browser speaks new lines aloud (agent voice vs pilot voice).

### Score
`{"separation_losses":0,"runway_conflicts":0,"deadlocks":0,"readback_errors_accepted":0,"completed":4,"avg_delay_s":18.2,"throughput_per_10min":6}` plus extra keys the UI may ignore: `clearance_reversals`, `clearance_amendments`, `go_arounds`, `avg_landing_occupancy_s`, `recovery_time_s` (null until known), `decisions`.

## Engine interface (Python)

```python
# sim/core/engine.py
class Simulation:
    def __init__(self, scenario_path, graph_path, seed=None, controller="tower", run_dir=None): ...
    def step(self) -> list: ...            # advance one 100 ms tick, return events
    def snapshot(self) -> dict: ...        # contract snapshot
    def delta(self) -> dict: ...           # changes since last delta()/snapshot()
    def control(self, action, value=None) -> dict: ...
    # used by controllers (the built-in tower, or any plugged-in controller) as tools:
    def observe(self) -> dict: ...         # observation view (delayed/noisy) + weather + runway status
    def issue_clearance(self, callsign, action, params=None) -> dict: ...
    def speak(self, callsign, text, speaker="agent") -> dict: ...
    def set_runway_config(self, arrival_runways, departure_runways) -> dict: ...
    def record_decision(self, **fields) -> dict: ...
    def resolve_readback(self, readback_id, accept) -> dict: ...   # accept a pilot readback or say again (see observe()['pending_readbacks'])

# built-in controller: sim/tower/controller.py (an optional Claude controller lives outside the simulator in agent/)
class Controller:
    name: str
    def on_tick(self, sim, events) -> None: ...   # may call sim tools; must return quickly
```
Clearance `action` values: `taxi_via`, `hold_short`, `cross_runway`, `line_up_and_wait`, `cleared_for_takeoff`, `cleared_to_land`, `go_around`, `hold_position`, `resume_taxi`, `pushback_approved`, `contact_tower_or_ground`. The engine validates only the shape of a call (known callsign/runway/graph nodes); it never refuses a clearance because it is unsafe. Conflicts are logged for scoring.

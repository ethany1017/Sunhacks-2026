# Simulator build prompt (paste into Claude Code from the repo root)

**Before you run it, do these once:**

1. Google Cloud: create a project, turn on billing, enable **Maps JavaScript API**, create an API key and restrict it to the HTTP referrer `http://localhost:*/*` (add the deploy URL if you host it).
2. Save the key in `web/.env.local` as `VITE_GOOGLE_MAPS_API_KEY=...` and your Anthropic key in `.env` as `ANTHROPIC_API_KEY=...`. Both files are gitignored. Never paste keys into the prompt or commit them.
3. Use Google's overlay pattern only (deck.gl on top of the Google map). Do not download or cache Google imagery.

Everything below the line is the prompt.

---


You are building the **KPHX autonomous airport control simulator** in this repo (`Hackathon/`, remote `ethany1017/Sunhacks-2026`). Read `README.md`, `docs/ARCHITECTURE.md` and `data-catalog.md` first; they are the spec. This is a competition demo: one fully autonomous AI agent runs Phoenix Sky Harbor in simulation. **There is no second checker, no human approval, no advisory framing, and no real-world use.** Do not add any. Do not ask me questions; where something is unspecified use the defaults below and list every assumption you made in `docs/ASSUMPTIONS.md`.

## 1. Deliverable and priority order

Build in this order. Each phase must run end to end before starting the next.

- **Phase A (must have, first):** KPHX graph, headless simulator, baseline rule-based controller, WebSocket API, and the full-screen map dashboard showing aircraft moving on a real Google satellite map with the glowing overlay.
- **Phase B:** the Claude agent (tool calling) replaces the baseline controller, with decision records shown in the UI.
- **Phase C:** weather replay (the Aug 25, 2025 haboob) and replanning.
- **Phase D:** voice (spoken clearances and simulated-pilot readbacks).

Presenting happens on a 1920x1080 projector. It must look excellent there and still work at 1280x720.

## 2. Stack (pin exact versions in lockfiles)

- **Backend:** Python 3.11+, FastAPI, uvicorn, pydantic v2, networkx, shapely, pyproj, numpy. One process owns the clock and the true state. `pytest` for tests.
- **Frontend:** Vite + TypeScript, `@googlemaps/js-api-loader`, deck.gl 9.x with `@deck.gl/google-maps` (`GoogleMapsOverlay`), no UI framework needed (plain TypeScript + CSS is fine).
- **Map:** Google Maps JavaScript API, `mapTypeId: "satellite"`, raster map, overlay with `GoogleMapsOverlay({ interleaved: false })`. Key from `import.meta.env.VITE_GOOGLE_MAPS_API_KEY`. Never hard-code or commit a key. Provide `web/.env.example`.
- **Fallback (automatic):** if the key is missing, invalid or the Google script fails to load, switch to MapLibre GL with the USGS NAIP imagery (see `data/build_logistics/usgs_naip_tile_kphx/` for the tile URL pattern) and the same deck.gl layers. Show a small "offline map" badge in that case. The demo must never show a blank screen.
- **Agent:** Anthropic Python SDK, model `claude-sonnet-5-5`, key from `.env` as `ANTHROPIC_API_KEY`.
- **Config:** `.env`, `.env.local` and keys are gitignored. Provide `.env.example` files with empty values.

## 3. Repo layout (create exactly this)

```
sim/graph/      build_graph.py, subset.py
sim/core/       clock.py, entities.py, physics.py, reservations.py, eventlog.py, observation.py, scenario.py
sim/agent/      baseline.py, claude_agent.py, tools.py, prompt.md, decisions.py
sim/voice/      phraseology.py, pilots.py
sim/weather/    replay.py
sim/scenarios/  kphx_haboob.json, kphx_smoke.json
sim/metrics/    score.py
api/            main.py, ws.py, schemas.py
web/            index.html, src/main.ts, src/map.ts, src/layers.ts, src/panels.ts, src/store.ts, src/style.css
tests/          test_graph.py, test_determinism.py, test_physics.py, test_agent_tools.py
docs/           ASSUMPTIONS.md
data/processed/ kphx_graph.json, kphx_graph.geojson (generated, committed if < 5 MB)
Makefile        make graph | make sim | make api | make web | make test | make demo
```

## 4. Inputs already on disk (use these, do not re-download)

- `data/kphx_geometry/osm_overpass_aeroway/kphx_aeroway.json`: Overpass JSON with `elements`. 1,244 elements: 486 `taxiway` ways, 70 `taxilane`, 241 `parking_position` ways, 231 `holding_position` nodes, 111 `gate` nodes, 22 `jet_bridge`, 18 `apron`, runway ways (08/26 is split into three ways: merge by `ref`), `threshold` nodes. Each way has a `geometry` list of `{lat, lon}` points and `tags` (`ref` is the taxiway letter).
- `data/kphx_geometry/faa_arcgis_Runways/runways_phx_bbox.geojson`, `faa_arcgis_AM_Taxiway/`, `faa_arcgis_AM_Apron/`: FAA polygons for rendering pavement and cross-checking.
- `data/reference_and_rules/derived_kphx_aircraft_normalized/kphx_aircraft_normalized.csv`: 39 aircraft types. Columns include `icao_type`, `faa_cwt_A_to_I`, `recat15_A_to_F`, `approach_speed_kt_acd_vref_proxy`, `wingspan_ft_no_winglets`, `length_ft`. Use wingspan and length for icon size and footprint, approach speed for landing kinematics.
- `data/weather_and_status/haboob_clean_2025-08-25/kphx_haboob_20250825_clean.csv`: columns `utc, kind, temp_f, wind_dir, wind_kt, gust_kt, peak_gust_kt, vis_sm, wx, ceiling_ft, rvr, altim_inhg, dust_flag, ts_flag, metar`.
- `data/kphx_traffic/bts_otp_2026_8/derived/phx_hourly_summary.csv`: hourly scheduled departures and arrivals per day, and taxi-out/taxi-in stats. Carrier mix: Southwest, American, SkyWest, Envoy, Delta, United, Frontier, Alaska (weights from the BTS counts in the catalog).
- KPHX facts (from the catalog): runways 08/26 (11,489 ft, northernmost), 07L/25R (10,300 ft, middle), 07R/25L (7,800 ft, southernmost); field elevation 1,134.8 ft; ground 119.75 north and 132.55 south, tower 118.7 (08/26) and 120.9 (07L/25R, 07R/25L), ATIS 127.575. Hot spots HS1 and HS2 are on taxiway F (crossings of 07L-25R at F8, F9, F10). Verify the runway order against the FAA polygons in the first step and print the result.

## 5. Phase A, step by step

### 5.1 Graph (`sim/graph/build_graph.py`)

1. Parse the OSM JSON. Build a directed routable graph: snap way nodes that share an OSM node id; split ways at shared nodes; edge attributes `taxiway_ref`, `length_m` (geodesic), `surface`, coordinates, `kind` (taxiway, taxilane, runway, apron/ramp).
2. Attach each `holding_position` node to the nearest edge within 15 m, splitting the edge there; mark it `hold_short` for the runway it protects. Attach each `gate` and `parking_position` to the nearest taxilane/apron node as a stand.
3. Merge the three 08/26 ways into one runway; create one occupied-pavement resource per runway (both ends share it). Add crossing nodes where a taxiway edge meets a runway pavement.
4. Report and fix defects, printing counts: dead ends (82 expected), unnamed taxiway ways (57), detached hold positions (28). Keep only the largest connected component; fail loudly if it is under 95% of taxiway length.
5. Output `data/processed/kphx_graph.json` (nodes, edges, stands, holds, runways, crossings) and `kphx_graph.geojson` for the map.
6. `subset.py`: a config option to restrict to a region (default: whole airport; fallback: the two south runways plus the terminal 4 area) so we can shrink scope if the full graph misbehaves.
7. `tests/test_graph.py`: a path exists from a sampled gate to each runway end and back; no isolated hold positions; runway pavements have both ends.

### 5.2 Simulator core

- **Clock:** fixed 100 ms sim tick, speed multiplier 1x, 2x, 5x, 10x, 20x, pause. Single `numpy.random.Generator` seeded from the scenario. Same seed and scenario must give identical event logs (hash check in `test_determinism.py`).
- **Entities:** `Aircraft` (id, callsign, type, wingspan_m, length_m, phase, edge_id, offset_m, speed_mps, heading_deg, gate, runway, route, clearance_state) and `Vehicle` (for one or two ground vehicles for visual life).
- **Phases:** arrival: `final` → `landing_roll` → `runway_exit` → `taxi_in` → `at_gate`; departure: `at_gate` → `pushback` → `taxi_out` → `hold_short` → `line_up` → `takeoff_roll` → `airborne`. Go-around: `final` → `go_around` → back to `final`.
- **Kinematics (defaults, all flagged in ASSUMPTIONS.md as placeholders):** taxi 15 kt on straights slowing to 8 kt in turns and 5 kt at crossings; pushback about 4.7 min mean; runway occupancy about 55 s for landings; takeoff roll and landing roll scaled by type from the CSV; landing speed from `approach_speed_kt_acd_vref_proxy`. Heading follows the path tangent; smooth speed changes (no teleporting, no instant stops).
- **Physics (the simulator, not a checker):** aircraft follow their cleared route edge by edge. If the next edge or the runway pavement is occupied or reserved by someone else, the aircraft slows and holds (car-following with a 60 m gap). The simulator never refuses to run the agent's clearance for being "unsafe"; if the agent issues something that causes a conflict, the sim lets it happen and logs `conflict` / `separation_loss` events for the metrics. This keeps the agent genuinely autonomous and the scoreboard honest.
- **Reservations (`reservations.py`):** runway pavements, crossings and taxi edges are resources with an owner and time window; they are state the agent reads, not rules enforced on it.
- **Observation (`observation.py`):** the agent sees a view of the truth with optional delay (default 1 s), position noise (default 3 m) and per-scenario dropouts. Truth stays in the sim.
- **Event log:** append-only JSONL at `runs/<run_id>/events.jsonl` using the envelope in `data-catalog.md` P2.4 (run_id, event_id, kind, entity_id, sim_time_ms, observed/issued/received times, quality, data). Every aircraft state change, clearance, speech line and weather update is an event. Provide `python -m sim.replay <events.jsonl>` that replays a log into the same WebSocket stream.

### 5.3 Traffic scenario (`sim/core/scenario.py`, `sim/scenarios/*.json`)

Scenario JSON: seed, start sim time, duration, weather replay offset, and a list of flights `{callsign, airline, type, kind: arrival|departure, scheduled_sim_s, gate, runway_preference}`. `kphx_smoke.json`: 6 aircraft, 5 minutes, no weather. `kphx_haboob.json`: 12 aircraft, about 8 minutes of sim time, 6 arrivals and 6 departures on a banked schedule (peak-like), haboob trigger at sim 3:00. Callsigns from airline weights (SWA, AAL, SKW, ENY/MQ, DAL, UAL, FFT, ASA). Types and gates drawn from the CSV and the graph's stand nodes. Include 1 GA aircraft and 1 ground vehicle.

### 5.4 Baseline controller (`sim/agent/baseline.py`)

A first-come-first-served controller that: assigns runways, picks the shortest route (networkx), issues taxi, hold-short, cross, line-up, takeoff and landing clearances in order, and works in the sim with no LLM so the demo always runs. It uses the same tool functions the Claude agent will use (see 6.2), so swapping controllers changes nothing else.

### 5.5 API (`api/`)

- `GET /graph` → `kphx_graph.geojson` plus stands and holds.
- `GET /state` → full snapshot.
- `WS /ws` → on connect send `{type: "snapshot", ...}`, then `{type: "delta", t, aircraft: [...], events: [...]}` at 10 Hz. Include clock, speed, paused, agent status (`idle` | `thinking`), weather, runway occupancy, clearances.
- `POST /control` → `{action: "pause"|"resume"|"speed"|"trigger_haboob"|"reset", value}`.
- `GET /decisions/{id}` → a decision record.
- CORS for `http://localhost:5173`. The sim keeps running with no client connected.

### 5.6 Dashboard (this is the part people see; make it beautiful)

**Layout (1920x1080):**
- Full-bleed Google satellite map of KPHX behind everything, centered near the airport reference point (about 33.4343 N, 112.0116 W; fit to the graph bounds), zoom about 14.6, tilt 0, no default Google controls, no street labels (use `styles` / disable POIs), north up.
- Floating **glass panels** (`background: rgba(10,14,22,0.72)`, `backdrop-filter: blur(14px)`, 1px border `rgba(255,255,255,0.08)`, 14px radius, soft shadow): top bar, left list, right decision card, bottom strip. Inter font. Accent colors: cyan `#22d3ee` (arrivals), amber `#f59e0b` (departures), green `#22c55e` (free/ok), red `#ef4444` (occupied/hold), violet `#a78bfa` (agent), text `#e5e7eb`.
- **Top bar:** KPHX · SIMULATION badge · sim clock (UTC and local Phoenix time) · speed buttons (pause, 1x, 5x, 10x) · agent status pill with a pulsing violet dot when thinking · live METAR strip (raw text) with wind arrow, visibility, gust.
- **Left panel:** Arrivals and Departures lists: callsign, type, gate, runway, status chip (e.g. "On final", "Holding short 7L", "Cleared to cross"), ETA; click a row to select the aircraft and fly the camera to it.
- **Right panel:** Selected-decision card: what the agent did, the facts it used, the rules it weighed, options it passed over and why, and when it will look again (from the decision record). Below it, the clearance timeline for the selected aircraft (issued → acknowledged → complete).
- **Bottom strip:** runway occupancy timeline (a Gantt-style bar per runway, past 3 min and next 3 min, red = occupied, amber = reserved) and the voice transcript with each line showing speaker, text and readback status chip (correct / corrected).

**Map layers (deck.gl via GoogleMapsOverlay):**
- `PolygonLayer` runway pavement from the FAA GeoJSON: transparent fill with an animated glow outline; fill turns green (free), amber (reserved) or red (occupied) with a soft pulse.
- `PathLayer` taxi routes: each active route drawn twice for glow (wide 14 px at 18% alpha, additive blend, plus a bright 3 px core); the cleared route solid, the planned route dashed (`PathStyleExtension`); color matches the aircraft's phase.
- `ScatterplotLayer` hold-short markers: small red bars/rings that appear while a hold instruction is active and fade when released.
- `IconLayer` aircraft: a clean top-down airliner silhouette (single-color SVG, `mask: true`), rotated by heading, size in meters (`sizeUnits: "meters"`, scaled up 4x from real wingspan so it is visible at zoom 14.6), colored by arrival (cyan), departure (amber), vehicle (white), plus a soft drop shadow layer. Selected aircraft get a pulsing halo ring and a callsign label.
- `TripsLayer` trails: 25 s fading trail behind each aircraft.
- `TextLayer`: callsign + type labels next to aircraft (hide when more than 14 aircraft overlap), gate labels and taxiway letters only at zoom >= 16.
- Weather: during the haboob, a stylized translucent brown-orange dust wall (gradient polygon) advances across the map from the southeast over the sim time (visual only, not real radar), with reduced map brightness and a vignette as visibility drops. Wind arrow in the METAR strip.

**Motion and polish:**
- Client interpolates aircraft positions and headings between 10 Hz deltas with `requestAnimationFrame` so motion is smooth at 60 fps. Never snap.
- Intro: on load, a 2-second camera fly-in from a wide Phoenix view to the airport.
- Presentation mode: `F` toggles fullscreen and hides the cursor; keys: Space pause/resume, `1` `2` `3` `4` speeds, `H` trigger haboob, `R` reset scenario, `Esc` leave presentation mode. Show key hints for 4 seconds on first load.
- Smooth panel transitions (200 ms), status chips animate on change, new voice lines slide in.
- Target 60 fps with 15 aircraft; if frames drop below 45, reduce trail length and glow layers automatically.
- Handle WebSocket reconnect: show a small "reconnecting" badge and resume from the next snapshot.

## 6. Phase B: the Claude agent

### 6.1 Loop (`sim/agent/claude_agent.py`)

Wake the agent on events (new arrival in range, aircraft at hold line, readback received, weather change, aircraft complete, clearance timeout) and on a 5 s sim timer. Build the prompt context from the observation view plus the last N events. Run a tool-use loop until the agent has no more tool calls. While the agent is thinking set agent status to `thinking`; the sim keeps running (optionally pauses if `pause_while_thinking` is true in config; default false). Cap each wake at 8 tool calls and 15 s.

### 6.2 Tools (`sim/agent/tools.py`, pydantic schemas, syntax checks only)

Information: `get_airport_state()`, `get_aircraft(callsign)`, `get_route_options(callsign, destination)` (shortest paths and lengths), `get_wake_spacing(leader_type, follower_type)` (from the aircraft CSV categories), `get_weather()`, `get_runway_status()`.

Actions (applied directly to the sim): `issue_clearance(callsign, action, params)` with `action` in `taxi_via` (route as taxiway letters and holds), `hold_short`, `cross_runway`, `line_up_and_wait`, `cleared_for_takeoff`, `cleared_to_land`, `go_around`, `hold_position`, `resume_taxi`, `pushback_approved`, `contact_tower_or_ground`; `speak(callsign, text)`; `set_runway_config(arrival_runways, departure_runways)`; `record_decision(...)`.

The tools only validate the shape of the call (known callsign, known runway, route made of existing graph nodes). They never refuse a clearance because it is unsafe.

### 6.3 Agent prompt (`sim/agent/prompt.md`)

Write a system prompt that: states the agent is the sole controller of KPHX in a simulation and acts without anyone's approval; gives the runway layout, frequencies, hot spots; includes the phraseology templates (from `data/voice_and_comms/`, FAA 7110.65 chapters 2-3, AIM 4-2, digits: "niner", "fife", "tree", altitude group form); gives its rulebook (separation, runway occupancy, line-up-and-wait, crossing readback requirement) as knowledge, citing the catalog section; explains the clearance lifecycle, how to read a readback and ask again if wrong, how to react to a weather change, and that every action must be followed by `record_decision` with facts used, rules weighed, options passed over and the next review time.

### 6.4 Decision records (`sim/agent/decisions.py`)

Fields: id, sim_time, snapshot summary with data ages, action, rules/constraints weighed, alternatives passed over with reasons, next_review_sim_s, linked event ids, voice line id. The dashboard shows these verbatim. Explanations are written when the decision is made, never invented afterward.

## 7. Phase C: weather

`sim/weather/replay.py` replays the haboob CSV on the sim clock (map CSV minute to sim second, configurable). Emit `weather` events; the agent receives them as wake-ups. Physics effect when visibility is below 1 SM: taxi speed multiplier 0.6 and runway occupancy +20% (assumptions, flagged). Show raw METAR text, wind and visibility in the UI. Also allow a live mode that polls the AWC API through the backend (no key, max 1 request per minute per station, descriptive User-Agent), off by default.

## 8. Phase D: voice

- **Agent speech:** phraseology templates in `sim/voice/phraseology.py`, spoken in the browser with `speechSynthesis` using one steady voice (zero cost, works offline); optional provider adapter later.
- **Simulated pilots:** `sim/voice/pilots.py` generates readbacks with a second browser voice (different pitch), with a small configurable error rate (wrong runway side, dropped word) and radio-style filtering via Web Audio (bandpass 300-3400 Hz, short noise burst). The agent reads the readback as text and decides itself whether to accept or ask again.
- Voice lines appear in the transcript with waveform animation and a readback status chip.

## 9. Scoring (`sim/metrics/score.py`)

Pure functions over the event log: separation losses (distance and time), runway conflicts (overlapping occupancy), taxi deadlocks (wait-for graph cycle with no progress for 60 s), readback mistakes accepted, clearance reversals, average delay per aircraft against unimpeded time, throughput per 10 min, recovery time after the weather hits. Print a table at the end of each run and show a small score card in the UI. These are for showing the agent works; they do not gate anything.

## 10. Quality bar and hygiene

- `make demo` starts API, simulator and web together; `make test` runs all tests.
- Determinism: same seed gives byte-identical `events.jsonl` (test).
- No secrets in git; `.gitignore` already covers `.env`, `*.key`; add `runs/` to `.gitignore`.
- Keep code small and readable; type hints; no dead code. Do not commit anything above 5 MB.
- Do not modify `data/`, `data-catalog.md` or `README.md` except to add run instructions to the README "Getting started".
- Commit in logical steps with clear messages. Do not push; I will push.

## 11. Verification (run all of these and report results)

1. `make test` passes, including graph connectivity and determinism.
2. Headless: `python -m sim.core.run sim/scenarios/kphx_smoke.json` finishes 6 aircraft with the baseline controller and prints the score table; no `conflict` events.
3. `make demo`, open the dashboard: Google satellite map of KPHX loads with the overlay; aircraft move smoothly; runway glow changes color with occupancy; panels update; speed buttons and `H` work.
4. Unset the Google key: dashboard falls back to MapLibre + USGS imagery with the "offline map" badge and still works.
5. Take screenshots at 1920x1080 and 1280x720 with Playwright and save them to `docs/screenshots/`; check no console errors and a steady 50+ fps with 15 aircraft.
6. Phase B: with `ANTHROPIC_API_KEY` set, run the haboob scenario with the Claude agent and show decision records appearing in the UI.
7. List every assumption in `docs/ASSUMPTIONS.md` with the value used and why.


# [Project Name] — KPHX Airport Simulator

SunHacks 2026 hackathon project by Gus and Ethan.

A simulator of Phoenix Sky Harbor (KPHX) operating on its own: aircraft arrive, land, taxi to gates, push back, taxi out and take off on the real KPHX taxiway layout, with a rule-based tower issuing clearances, simulated pilots reading them back, under calm static weather. A live map shows the whole airport as it runs.

> **Simulation only.** Aircraft, pilots and the tower are all simulated.

## What it simulates

- **Traffic:** seeded arrivals and departures with realistic airlines, aircraft types and gates.
- **Aircraft movement:** approach, landing roll, runway exit, taxi, pushback, line-up, takeoff roll and climb-out, with performance by aircraft type.
- **The ground:** the KPHX taxiway graph built from OpenStreetMap, runway occupancy, hold-short points, crossings and ground vehicles.
- **The tower:** a built-in rule-based controller (`sim/tower/`) that sequences arrivals and departures first come, first served, and records the reason for each clearance.
- **Radio:** clearances in standard phraseology, pilot readbacks (including scripted mistakes), spoken aloud in the browser.
- **Weather:** one calm static KPHX report for now; weather levels come later.
- **Scoring:** separation losses, runway conflicts, deadlocks, delay and throughput, computed from the event log.

## How it works

```
Scenario (flights, weather, vehicles)
              |
     Simulation engine (100 ms ticks)
   aircraft physics · runway reservations · weather · pilots
              |                      ^
              v                      |
      Built-in tower  -- clearances --+
              |
   Event log (JSONL) --> API (REST + WebSocket) --> Dashboard
```

| Component | Where | Responsibility |
| --- | --- | --- |
| Engine | `sim/core/` | Clock, true state, physics, runway reservations, conflict detection, event log. |
| Airport graph | `sim/graph/` | KPHX taxiways, runways and gates from OpenStreetMap. |
| Tower | `sim/tower/` | Rule-based controller: sequencing, taxi routes, runway use, crossings. |
| Pilots and radio | `sim/voice/` | Phraseology and simulated readbacks. |
| Weather | `sim/weather/` | Calm static weather report. |
| Scoring | `sim/metrics/` | Metrics over the event log. |
| API | `api/` | FastAPI server; steps the sim in real time and streams it over WebSocket. |
| Dashboard | `web/` | Live satellite map, flight lists, runway timeline, radio transcript, weather. |

The engine talks to its controller through a small tool surface (`issue_clearance`, `speak`, `set_runway_config`, `record_decision`, `observe`). The tower uses it, and any other controller can be plugged in through `Simulation.set_controller(obj)`.

## Dashboard

- **Live traffic map:** aircraft, airport geometry, routes and predicted paths.
- **Arrivals and departures:** sequence, ETA, queue and readiness.
- **Selected decision:** what the tower did, the facts and rules behind it, and the alternatives it passed over.
- **Runway timeline:** current occupancy and planned movements.
- **Radio:** spoken clearances and pilot readbacks.
- **Weather:** current conditions.

## Demo scope

- **Airport:** Phoenix Sky Harbor International (KPHX)
- **Scenario:** `kphx_busy` with 12 aircraft, one ground vehicle and a 10% pilot readback error rate (the API default); `kphx_smoke` with 6 aircraft.

## Research and data

- [`data-catalog.md`](data-catalog.md): every data source, with verified or unverified status, file paths, limits, conflicts and gaps.
- `data/`: downloaded samples (KPHX geometry, procedures, weather, traffic, aircraft and rules). Large raw files are not committed; the fetch scripts in `scripts/` rebuild them.
- [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md): every number the simulator and tower use, and where it came from.
- [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md): REST and WebSocket messages.

## Getting started

Needs Python 3.9+ and Node 20+.

```bash
make venv          # Python venv + dependencies (.venv)
make graph         # build the KPHX taxiway graph into data/processed/
make web-install   # dashboard dependencies
make test          # simulator tests
make sim           # headless smoke run with the score table
make busy          # headless busy run (12 aircraft, readback errors)
make demo          # API on :8000 and dashboard on :5173, open http://localhost:5173
```

Optional key: `web/.env.local` with `VITE_GOOGLE_MAPS_API_KEY=...` for the Google satellite map (gitignored; without it the dashboard falls back to free USGS imagery with an "offline map" badge).

Dashboard keys: Space pause, 1-4 speeds, R reset, F fullscreen, M mute. `?offline` in the URL forces the fallback map.

## Claude agent (not part of the simulator)

An earlier version of this project had a Claude agent run the airport. It has been moved out to [`agent/`](agent/README.md). The simulator, API and dashboard never import it.

## Tech stack

A custom Python simulator on a KPHX taxiway graph built from OpenStreetMap (`sim/`), an append-only JSONL event log, a FastAPI server with WebSocket updates (`api/`), and a TypeScript dashboard (`web/`) with the Google Maps JavaScript API and deck.gl overlay, falling back to MapLibre with USGS imagery.

## Limitations

- Aircraft, pilots, the tower and neighboring facilities are simulated.
- The tower follows a simplified rulebook (see `docs/ASSUMPTIONS.md`), not the full set of real-world procedures.
- Public weather reports do not include every runway-local condition a real tower uses.

## Team

- Gus
- Ethan

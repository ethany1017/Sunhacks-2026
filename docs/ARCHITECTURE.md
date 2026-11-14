# Architecture: a fully autonomous airport control agent (proposal, draft for Gus and Ethan)

Status: **proposal**, rewritten Nov 14, 2026 from the research in [`../data-catalog.md`](../data-catalog.md). Nothing here is implemented or agreed yet.

The goal is a competition demo: one AI agent that runs the whole airport on its own, in simulation, and explains what it did. There is no second checker and no human approving instructions. The agent decides, speaks, listens and acts by itself. We are not building this for real-world use.

## 1. Decisions this draft makes (change any of them)

| Decision | Choice | Why |
|---|---|---|
| Airport | KPHX | Team decision, Nov 14. KDVT material in the catalog (Part 2) is the fallback. |
| Controller | One autonomous agent (Claude with tool calls) that issues every clearance itself | This is the product. No approval step, no independent checker. |
| Simulator | Custom deterministic discrete-event simulator in Python | No existing open tool covers ground movement. BluebirdATC is en-route only (AGPL-3.0). BlueSky is airborne; its taxi support is unconfirmed. OpenTaxi (very new, 2 stars) is a reference to vet in the first hour, not a dependency. |
| Truth vs observation | The simulator keeps the true state; the agent sees an observation of it | Lets us make the demo interesting: late samples, a missing vehicle, a pilot who reads back wrong. |
| Voice | Push-to-talk, batch speech-to-text, text-to-speech for the agent and a second voice for simulated pilots | Cheap and easy to rehearse; no realtime API. |
| Weather | Backend proxy to the Aviation Weather Center, plus a scripted replay of the Aug 25, 2025 haboob | The demo must not depend on a live storm. |
| Log | Append-only JSONL event log | Drives replay, the dashboard, explanations and scoring. |

## 2. How it runs

```
 scenario.json ──► sim (Python, owns the clock and the true state)
                     │
                     ├─ observation (delayed / noisy view of the truth) ──► agent
                     │                                                        │
 weather-ingest ───────────────────────────────────────────────────────────► agent
                                                                              │
              pilot audio ──► speech-to-text ──────────────────────────────► agent
                                                                              │
              agent tool calls: issue_clearance, speak, hold, replan, ...     │
                     ▲                                                        │
                     └────────────────────────────────────────────────────────┘
                     │
        sim applies the clearance to the aircraft; text-to-speech voices it
                     │
                     ▼
              events.jsonl (append only) ──► api (FastAPI + WebSocket) ──► dashboard (MapLibre + deck.gl)
```

The agent runs in a loop: wake on an event (a new arrival, a readback, a weather change, an aircraft reaching a hold line) or on a short timer, read the current airport state, decide, and call tools. Tool calls go straight to the simulator. The dashboard only watches; closing it does not stop the agent, and on reconnect it catches up from the log.

## 3. Proposed repo layout

```
data/                    downloaded samples (already in repo)
scripts/                 fetch scripts (already in repo)
docs/ARCHITECTURE.md     this file
sim/
  graph/                 OSM -> taxiway graph, hold-short nodes, runway pavements
  core/                  clock, entities, event log, observation view
  agent/                 agent loop, system prompt, tool definitions, rulebook text
  tools/                 route planner, sequencing helper, ETA and spacing calculators
  voice/                 phraseology templates, speech-to-text and text-to-speech adapters, simulated pilots
  weather/               AWC client, haboob replay
  scenarios/             kphx_haboob.json, plus small test scenarios
  metrics/               scoring functions over the event log
api/                     FastAPI app, WebSocket stream of events
web/                     dashboard (Vite + TypeScript + MapLibre + deck.gl)
tests/                   determinism tests, golden replays
```

## 4. Building the KPHX airport model (first task)

Inputs already on disk (see the catalog for status and caveats):

- `data/kphx_geometry/osm_overpass_aeroway/kphx_aeroway.json`: taxiways, holding positions, gates, parking positions, runways. 485 of 486 taxiway ways are connected, so it is routable.
- `data/kphx_geometry/faa_arcgis_*`: FAA runway, taxiway and apron polygons (GeoJSON), for cross-checking pavement.
- `data/kphx_geometry/faa_hotspot_sw` and `faa_airport_diagram`: hot spot list and diagram text.

Steps:

1. Build a directed graph: nodes at intersections, hold positions and stands; edges per taxiway segment with length and taxiway name.
2. Clean the known defects: 82 dead-end candidates, 57 unnamed taxiway ways, 28 detached hold positions.
3. Treat each runway as one shared pavement. Confirm the runway order against the FAA polygons.
4. Start with a small subset: the two southern runways, one crossing, and a handful of stands. Expand only if time allows. KPHX runways: 08/26 is 11,489 ft, 07L/25R is 10,300 ft, 07R/25L is 7,800 ft.
5. Save the reduced graph as GeoJSON in the repo.

## 5. Core model

- **Clock.** Fixed tick (for example 100 ms of simulated time) and one seeded random generator, so the same seed replays the same run.
- **Entities.** Aircraft and vehicles with stable ids, type (from `data/reference_and_rules/derived_kphx_aircraft_normalized/kphx_aircraft_normalized.csv`), phase, position on a graph edge, speed and footprint.
- **Physics.** The simulator moves aircraft along their cleared routes and does not let two of them occupy the same pavement at once; it records anything that goes wrong (a near miss, a stuck aircraft) so the metrics can score it. This is the simulation, not a second opinion on the agent.
- **The agent.** It holds the rulebook (the separation and runway rules in `data-catalog.md`, from JO 7110.65BB, written into its prompt and tools), the current airport state, and a set of tools. It decides arrival and departure order, taxi routes, runway use, crossings, holds and go-arounds, and replans when weather or a pilot's response changes things. Helper tools (shortest path, wake-spacing time, ETA) give it numbers; they do not approve or reject anything.
- **Clearances.** Every instruction is a typed tool call (callsign, action, runway, route, hold point). The sim applies it. Each clearance has an id and a state (issued, acknowledged, complete, amended, cancelled) so the dashboard can show it and the agent does not repeat itself.
- **Voice.** The agent's words come from phraseology templates (verified phrases in `data/voice_and_comms/`) and are spoken by text-to-speech. Simulated pilots reply in a second voice, with optional radio noise and scripted mistakes (wrong runway side, a dropped word). The agent hears the reply through speech-to-text and decides for itself whether the readback is right, asks again if not, and carries on.
- **Weather.** Backend proxy to the AWC API (no key, 100 requests per minute, descriptive User-Agent). Keep the raw report beside the parsed values and mark old data as stale. The demo uses `data/weather_and_status/haboob_clean_2025-08-25/` as a scripted replay with a trigger at a chosen simulated time. Unit traps: AWC JSON altimeter is hPa, wind is "from", visibility can be `10+`.

## 6. Event log and explanations

Every state change is one JSON line using the envelope in `data-catalog.md` (P2.4): run id, event id, kind, entity, times, simulated milliseconds, quality and data. When the agent acts it writes a decision record: what it saw (state snapshot and timestamps), what it did, the rules and constraints it weighed, the options it passed over and why, and when it will look again. The dashboard shows these records as the explanation. Because the agent writes the reason when it decides, the explanation is the real one.

## 7. Dashboard (read-only)

Map (MapLibre, OpenFreeMap tiles, optional USGS aerial image of KPHX), arrivals and departures lists, selected-decision panel, runway timeline, voice transcript, and a weather panel. Aircraft are animated with a deck.gl layer from the WebSocket stream. Tile attribution is required.

## 8. Scoring and tests

Scoring functions run over the event log: separation losses, runway conflicts, taxi deadlocks, readback mistakes, delay per aircraft, throughput, recovery time after the weather hits. These are how we show the agent works, not a gate in front of it. Baselines for KPHX taxi times and hourly traffic come from `data/kphx_traffic/bts_otp_*/derived/` (BTS covers only large US carriers, so real traffic is higher).

Tests to write early: same-seed replays give the same decisions; a golden replay of the haboob scenario; no repeated clearance after a restart; the agent recovers from a missed readback and from a closed runway. Deadlock handling is our own design and has no external source.

## 9. Build order (about 20 hours to the Sunday 11 AM Devpost deadline)

1. Hour 0-2: reduced KPHX graph, scenario file, event log writer. Vet OpenTaxi for reusable ideas.
2. Hour 2-6: sim core, aircraft movement along routes, tool definitions, a first agent loop that clears a single arrival to its gate.
3. Hour 4-8 (parallel): API with WebSocket and the dashboard map with moving aircraft and a decision panel.
4. Hour 8-12: multiple aircraft, runway sequencing and crossings; weather replay and replanning.
5. Hour 12-16: voice loop with simulated pilots and readbacks.
6. Hour 16-19: scenario polish, scoring, a recorded fallback run in case the live demo fails.
7. Last hour: secret scan, README setup instructions, Devpost write-up that discloses AI-assisted code.

Suggested split (change as you like): one person on the sim, agent and tools; the other on the API, dashboard, weather and voice, meeting at the event log format first.

## 10. Risks

- KPHX graph defects eat the first hours. Mitigation: start with a small subset.
- Agent latency: an LLM call per decision is slow. Mitigation: wake the agent on events, batch routine decisions, let helper tools do the arithmetic, and keep the sim clock pausable.
- Wake-separation table numbering is inconsistent in the FAA HTML. Mitigation: use the PDF and the per-type category table when writing the rulebook.
- Voice quality on demo hardware. Mitigation: scripted audio fixtures as a fallback.
- Live feed or API outage during judging. Mitigation: the demo runs offline from frozen data.
- Rubric and pre-existing-code policy are unpublished. Mitigation: ask the organizers (team@sunhacks.io) and disclose research done before kickoff.

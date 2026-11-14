# Simulator architecture (proposal, draft for Gus and Ethan)

Status: **proposal**, written Nov 14, 2026 from the research in [`../data-catalog.md`](../data-catalog.md). Nothing here is implemented or agreed yet. Simulation only: no real aircraft are controlled.

## 1. Decisions this draft makes (change any of them)

| Decision | Choice | Why |
|---|---|---|
| Airport | KPHX | Team decision, Nov 14. KDVT material in the catalog (Part 2) is the fallback. |
| Simulator | Custom deterministic discrete-event simulator in Python | No existing open tool covers ground movement. BluebirdATC is en-route only (AGPL-3.0). BlueSky is airborne; its taxi support is unconfirmed. OpenTaxi (very new, 2 stars) is a reference to vet in the first hour, not a dependency. |
| Truth vs observation | Simulator truth is separate from what the controller sees | Lets us inject stale samples, missing vehicles and wrong readbacks. |
| Authority | The language model never authorizes a movement | A typed clearance manager plus an independent rule checker decide. |
| Voice | Push-to-talk, batch speech-to-text, rule-based parser, templated replies | Cheapest and most testable; no realtime API. |
| Weather | Backend proxy to the Aviation Weather Center, plus a scripted replay of the Aug 25, 2025 haboob | The demo must not depend on a live storm. |
| Log | Append-only JSONL event log | Drives replay, the dashboard, explanations and metrics. |

## 2. Processes

```
 scenario.json ──► sim-core (Python, one process, owns the clock)
                     │  truth state ──► observation filter (delay, noise, dropouts)
                     │                        │
                     │                  airport state (what the controller believes)
                     │                        │
                     │                    planner ──► proposal
                     │                        │
                     │                   safety checker ──► reject / approve (rule ids)
                     │                        │
                     │                 clearance manager (typed lifecycle)
                     │                        │
                     │                 voice service ──► TTS ──► simulated pilots
                     │                        ▲                       │
                     │                  STT + parser ◄── pilot audio/readback
                     ▼
              events.jsonl (append only) ──► api (FastAPI + WebSocket) ──► dashboard (MapLibre + deck.gl)
 weather-ingest (AWC proxy or haboob replay) ──► airport state
```

The dashboard is read-only. Closing it does not stop the simulation. On reconnect it replays from the log to catch up.

## 3. Proposed repo layout

```
data/                    downloaded samples (already in repo)
scripts/                 fetch scripts (already in repo)
docs/ARCHITECTURE.md     this file
schemas/                 JSON schemas: event envelope, clearance, decision record
sim/
  graph/                 OSM -> routable taxiway graph, hold-short nodes, runway pavements
  core/                  clock, entities, reservations, event log, observation filter
  planner/               first-come-first-served baseline, then smarter sequencing
  checker/               rule pack: one function per rule id
  clearance/             lifecycle state machine
  voice/                 phraseology templates, parser, readback matcher, TTS/STT adapters
  weather/               AWC client, haboob replay, flight-category derivation
  scenarios/             kphx_haboob.json, plus small unit scenarios
  metrics/               pure functions over the event log
api/                     FastAPI app, WebSocket stream of events
web/                     dashboard (Vite + TypeScript + MapLibre + deck.gl)
tests/                   unit tests, golden replays, mutation tests
```

## 4. Building the KPHX airport model (first task)

Inputs already on disk (see the catalog for status and caveats):

- `data/kphx_geometry/osm_overpass_aeroway/kphx_aeroway.json`: taxiways, holding positions, gates, parking positions, runways. Verified routable: 485 of 486 taxiway ways connected.
- `data/kphx_geometry/faa_arcgis_*`: FAA runway, taxiway and apron polygons (GeoJSON), for cross-checking pavement.
- `data/kphx_geometry/faa_hotspot_sw` and `faa_airport_diagram`: hot spot list and diagram text.

Steps:

1. Build a directed graph: nodes at intersections, hold positions and stands; edges per taxiway segment with length and taxiway name.
2. Clean the known defects: 82 dead-end candidates, 57 unnamed taxiway ways, 28 detached hold positions. Check that every runway crossing has a hold-short on both sides (not yet checked).
3. Mark runway pavements as shared resources: a runway and both of its ends are one occupied pavement. Verify runway order against the FAA polygons before relying on any note.
4. Start with a small subset: the two southern runways, one crossing, and a handful of stands. Expand only if time allows. KPHX runways: 08/26 is 11,489 ft, 07L/25R is 10,300 ft, 07R/25L is 7,800 ft; the two south runways are about 800 ft apart (the report writer's arithmetic, not a surveyed figure).
5. Store the reduced graph as GeoJSON in the repo, with source file, date and checksum.

## 5. Core model

- **Clock.** Backend-owned fixed tick (for example 100 ms of simulated time). One seeded random generator. Same seed and same assets must give the same decisions.
- **Entities.** Aircraft and vehicles with stable ids, type (from `data/reference_and_rules/derived_kphx_aircraft_normalized/kphx_aircraft_normalized.csv`), phase, position on a graph edge, speed, footprint (length and wingspan for swept-footprint checks).
- **Resources and reservations.** Runway pavements, crossings and taxi edges are resources. An actor reserves a resource for a time window and releases it only when its whole footprint has cleared and the evidence is current. Absence of a track is never evidence that a resource is clear.
- **Observation filter.** Truth goes through a filter that adds delay, noise, stale samples and missing vehicles before it reaches the controller. Only simulator truth judges whether a movement was actually unsafe.
- **Planner.** First build a first-come-first-served baseline. Then add sequencing by wake category, runway and departure readiness. Planners output proposals, never commands.
- **Safety checker.** An independent rule pack, one function per rule, each with a rule id, source paragraph (JO 7110.65BB, see catalog section 3.12) and applicability. Unknown input means unsafe by default. Rules to implement first: runway occupancy exclusivity, hold-short readback required, crossing needs its own authorization, line-up-and-wait, landing and takeoff clearance conditions, wake spacing from the RECAT tables (hard-code only after checking the table numbering against the PDF, as the catalog warns).
- **Clearance manager.** States: proposed, validated, issued, acknowledged, observed complete, plus rejected, canceled, superseded, expired and unresolved. Every clearance has a version and idempotency key. Re-validate a queued instruction immediately before it is spoken. After a restart, rebuild reservations from the log and do not re-issue unresolved commands.
- **Voice.** Controller side: templates from the verified phraseology in `data/voice_and_comms/`. Pilot side: scripted simulated pilots with a second voice, optional noise and injected errors (wrong runway side, dropped token, wrong channel). Parser: normalize digits ("niner", "fife"), fuzzy-match callsigns against the active roster, rule-based extraction. Readback check: compare callsign, action and runway against the issued clearance; a partial or contradictory readback keeps movement pending. Log acknowledgment separately from observed motion.
- **Weather.** Backend proxy to the AWC API (no key, 100 requests per minute, descriptive User-Agent). Keep the raw report beside parsed values; flag stale data; mark TAF as forecast. Demo uses `data/weather_and_status/haboob_clean_2025-08-25/` as a scripted replay, with a trigger at a chosen simulated time. Watch the unit traps: AWC JSON altimeter is hPa, wind is "from", visibility can be `10+`.

## 6. Event log and decision records

Every state change is one JSON line using the envelope in `data-catalog.md` (P2.4): run id, mode, event id, kind, entity, source, observed/issued/received times, simulated milliseconds, quality, raw reference, data. A decision record links to its inputs: snapshot id, proposal, rule ids fired, binding constraints, alternatives rejected and why, and the next review trigger. The dashboard explanation panel reads these records; it never generates an after-the-fact story.

## 7. Dashboard (read-only)

Map (MapLibre, OpenFreeMap tiles, optional USGS aerial image of KPHX), arrivals and departures lists, selected-decision panel, runway timeline, voice transcript with readback status, weather and data-freshness panel. Aircraft animated with a deck.gl layer from the WebSocket stream. Tile attribution is required.

## 8. Metrics and tests

Metrics are pure functions over the event log: separation violations, runway conflicts, clearance inconsistencies, taxi deadlocks, communication errors, recovery time, delay per actor, throughput. Baselines for KPHX taxi times and hourly traffic come from `data/kphx_traffic/bts_otp_*/derived/` (BTS covers only large US carriers, so real traffic is higher).

Tests to write early: same-seed determinism; zero overlapping runway reservations; zero unauthorized hold-line crossings; wrong readback never accepted; no repeated command after restart; the mutation cases from `data-catalog.md` P2.9 (delayed runway exit, footprint blocking a taxiway, wrong channel, unreported vehicle). Deadlock handling (wait-for graph) is our own design and has no external source.

## 9. Build order (about 20 hours to the Sunday 11 AM Devpost deadline)

1. Hour 0-2: reduced KPHX graph, scenario file, event envelope and log writer. Vet OpenTaxi for reusable ideas.
2. Hour 2-6: sim core with reservations, FCFS planner, checker with the first five rules, clearance lifecycle. Headless run that prints decisions.
3. Hour 4-8 (parallel): API with WebSocket, dashboard map with moving aircraft and the decision panel.
4. Hour 8-12: weather replay and replan; decision records feeding the explanation panel.
5. Hour 12-16: voice loop with scripted pilots; readback tests.
6. Hour 16-19: scenario polish, metrics, golden replay, a recorded fallback run in case live demo fails.
7. Last hour: secret scan, README setup instructions, Devpost write-up that discloses AI-assisted code and states plainly that it is a simulation.

Suggested split (change as you like): one person on sim core, checker and clearance manager; the other on API, dashboard, weather and voice, meeting at the event log schema first.

## 10. Risks

- KPHX graph defects eat the first hours. Mitigation: start with a small subset.
- Wake-separation table numbering is inconsistent in the FAA HTML. Mitigation: use the PDF and the per-type category table.
- Voice latency and recognition on demo hardware. Mitigation: scripted fixtures as fallback.
- Live feed or API outage during judging. Mitigation: the demo runs entirely offline from frozen data.
- Rubric and pre-existing-code policy are unpublished. Mitigation: ask the organizers (team@sunhacks.io) and disclose research done before kickoff.

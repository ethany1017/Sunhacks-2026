# Build: Autonomous Airport Traffic Control Agent (SunHacks 2026)

You're helping Ethan and Gus build a hackathon demo due Nov 15, 2026 at 11am.
The only goal is a working, reliable live demo. Not a product. Keep it simple.
Repo: ethany1017/Sunhacks-2026, cloned at ~/Sunhacks-2026. Branch: tower-zero.
Project name: [DECIDE — placeholder "Tower Zero"]

## What it does
An AI tower controller for a simulated airport. It sequences simulated
arrivals and departures, routes aircraft on the ground, speaks clearances,
listens to simulated pilot readbacks, uses live aviation weather to pick the
runway, and shows every decision and its reasoning on a live traffic map.

## What you are NOT building
The simulator. Gus built it (in the repo, likely sim/). Never modify Gus's
simulator files. If something is missing from it, list it for Gus. Your job
is the control agent and dashboard. The agent talks to the simulator only
through the Simulator Interface below. The old stub in backend/simif/stub/ is
an offline fallback only. Don't spend time on it.

## Tech stack (fixed — don't substitute)
- Frontend: React, TypeScript, Vite
- UI design: Tailwind CSS, shadcn/ui
- Live traffic map: MapLibre GL JS
- Backend: Python, FastAPI
- AI reasoning: Claude API
- Safety engine: Python deterministic rule-based validation
- Voice recognition: faster-whisper
- Voice generation: ElevenLabs
- Weather data: NOAA Aviation Weather API (aviationweather.gov)
- Database: SQLite
- Real-time communication: WebSockets
- Development: VS Code, GitHub
- BluebirdATC will NOT be used.

## Architecture
A headless FastAPI backend connects to the simulator and handles traffic
planning, safety validation, clearances, and communications for the simulated
aircraft. The React dashboard displays aircraft movements, weather, AI
decisions, and alerts in real time over WebSockets. Claude assists with
reasoning and explanations, while independent Python safety rules validate
every instruction before it is issued to simulated aircraft. The agent must
keep running with the dashboard closed. The dashboard is read-only: it never
sends commands.

## Hard rules
- Simulation only. Never connect to real ATC, radios, or aircraft.
- Every clearance passes the safety engine first. Claude and the planner
  propose; the safety engine approves or rejects. No bypass path. Claude never
  overrides a rejection.
- Every decision, including rejections, is stored with a plain-English reason.
- No agent code may depend on simulator internals or a specific airport.
  Everything goes through the Simulator Interface.
- Every external service has a fallback so the demo can't die on stage:
  - Claude API fails or times out (~3s) → deterministic planner choice +
    template reason
  - ElevenLabs fails → cached audio for that line, else macOS `say`
  - NOAA fails → last cached METAR
  - Map basemap fails → plain dark background with airport layers
- Secrets in `.env` (gitignored), with a committed `.env.example`.
- Only build what the current phase asks for. If something else seems needed, ask.
- Build one phase at a time. At the end of each phase: commit, stop, show me
  what works, and wait for my go-ahead before the next.
- Don't push until I say so.

## Simulator Interface (adapter between agent and Gus's simulator)
Extend the existing `Simulator` protocol in `backend/simif/`. Adapt it to
Gus's simulator, not the other way around. `connect()` picks Gus's simulator
by default, stub only via a flag.
- `get_airport()` → {config, geojson, graph}: ICAO code, map center, runways
  (both ends, designators, headings, lat/lon), taxiway graph (nodes/edges with
  lat/lon), hold-short nodes, parking nodes, final approach path per runway
  direction.
- `get_state()` → sim time + list of aircraft: callsign, arrival/departure,
  aircraft type, phase, lat/lon, heading, speed, altitude, current clearance.
- `issue(clearance)` → sends a validated clearance (land, hold short, cross,
  line up and wait, takeoff, taxi via route, go around). Returns ack/nack.
- `get_readbacks()` → pilot readback text since last call (callsign, text,
  linked clearance id).
- Events: aircraft_spawn, aircraft_departed, sim_end.
Document every field in `backend/simif/README.md`.

## Event contract (agent → dashboard)
Stored in the SQLite `events` table and pushed over WebSocket `/ws`:
{"t": 123.4, "seq": 17, "type": "...", "callsign": "N123AB", "data": {...}, "reason": "plain English"}
Types: agent_start, airport (data = {config, geojson, graph}), weather,
runway_change, aircraft_spawn, position (~1/sec), proposal, ai_decision
(options, choice, explanation, source: "claude" | "fallback"), safety_check
(approved/rejected, rule, reason), clearance, audio (url, speaker), readback
(transcript, matched), readback_correction, sequence, alert, sim_end.
On WebSocket connect, send the full history for the current run, then live events.

## Out of scope
Building or changing the simulator, live traffic (ADS-B), historical replay,
gate/ramp integration, multiple airports, user accounts, deployment,
BluebirdATC.

---

# Phases

## Phase 1: Foundation — DONE

## Phase 2: Connect to Gus's simulator
1. Find Gus's simulator in the repo (likely sim/). If you can't, stop and ask.
2. Read it end to end. Don't modify any of Gus's files.
3. Before writing code, report back and STOP:
   - How it runs (entry point, process, ports).
   - Exactly what data it exposes: airport layout, runways, taxiways, aircraft
     fields (position, heading, speed, altitude, phase, type, etc.), sim time,
     weather if any.
   - How it accepts commands/clearances, and which commands it supports.
   - Whether it produces pilot readbacks.
   - How it communicates (in-process, HTTP, WebSocket, files).
   - A table: everything the agent needs (Simulator Interface + Phases 3–8)
     → available / partly available / missing.
   - Anything missing that only Gus can add → a short list I can send him.
4. After my go-ahead: build the simif adapter for Gus's simulator.
5. Agent loop: connects through simif.connect(), emits agent_start, airport,
   aircraft_spawn, position, sim_end using his real data.
Done when: Gus's simulator runs, the agent reads all its aircraft live, the
dashboard shows them correctly on the map, and the DB fills with the
dashboard closed.

## Phase 3: Deterministic planner and safety engine (no Claude yet)
1. agent/safety.py: each rule returns (ok, rule_name, reason). Constants at
   top. Unit tests for each rule.
   - Runway occupancy: one aircraft on the active runway at a time.
   - Arrival/departure spacing: no takeoff if an arrival is inside ~2nm final;
     no landing clearance unless the runway will be clear in time.
   - Departure spacing: ≥60–90 sim-seconds between departures.
   - Taxi conflicts: no opposite-direction use of one edge; no two aircraft
     on one node.
   - Hold short: routes entering/crossing a runway stop at hold-short until
     cleared.
   - Tailwind limit: reject runway directions with tailwind > 10 kt.
2. agent/planner.py: deterministic, each tick. Uses ALL data Gus's simulator
   provides (positions, speeds, phases, aircraft types, layout graph) to
   navigate the planes safely.
   - Arrivals first-come first-served by time to threshold.
   - Departures fit into gaps between arrivals.
   - Taxi routes via Dijkstra on the airport graph, avoiding reserved edges.
   - Every proposal has a one-sentence reason.
   - Flow: proposal → safety_check → issue() → clearance event. Emit sequence
     events when order changes, alert events on rejections.
   - For key decisions (which departure takes a gap, sequence order, runway
     change timing), generate valid options + a default, so Phase 4 can plug
     Claude in.
3. Clearance text with standard-ish phraseology built from airport data.
Done when: traffic in Gus's simulator runs with no conflicts, at least one
rejection shows up, every decision has a reason, tests pass.

## Phase 4: Claude reasoning layer
1. agent/reasoning.py: async Claude API client. Model from env
   (ANTHROPIC_MODEL, default to a fast model).
2. At key decision points, send Claude a compact state snapshot + the valid
   options. Claude returns JSON: chosen option id + 1–2 sentence explanation.
   Invalid JSON → fallback.
3. The chosen option still goes through the safety engine. If rejected, log
   it, use the next valid option, say so in the feed.
4. Never block the agent loop. Timeout ~3s → deterministic default + template
   reason, source "fallback".
5. Emit ai_decision events. Dashboard shows Claude's explanation with a badge
   and the safety result next to it.
Done when: with a key set, decisions show Claude explanations; with no key or
network, the agent runs identically on fallback.

## Phase 5: Live weather and runway selection
1. agent/weather.py: fetch
   https://aviationweather.gov/api/data/metar?ids={ICAO}&format=json using the
   ICAO from get_airport(). Parse wind, visibility, raw text. Refresh every few
   minutes. Cache last good result in weather_cache/. Use cache on failure.
2. Manual wind override to force a wind shift on cue.
3. Runway direction by best headwind within the tailwind rule. On change:
   emit weather + runway_change, Claude explains, re-sequence, re-route.
4. Dashboard: wind arrow, METAR text, active runway flips, alert banner.
Done when: live METAR picks the runway, wifi-off works from cache, an
injected wind shift flips the runway cleanly.

## Phase 6: Voice generation (ElevenLabs)
1. agent/voice_out.py: ElevenLabs TTS, one tower voice, 2–3 pilot voices
   (IDs in .env). Cache audio by hash of (voice, text). Fallback to macOS say.
2. Speak every clearance and every readback. Async; never block the loop.
   If Gus's simulator has no readbacks, generate simple ones in the agent and
   tell me.
3. Backend serves audio files. Emit audio events. Dashboard plays them in a
   queue with mute toggle and "now speaking" indicator.
Done when: clearances and readbacks play in order without lag, second run is
fully cached.

## Phase 7: Voice recognition and readback check (faster-whisper)
1. agent/voice_in.py: faster-whisper (small/base, CPU int8, loaded once).
   Transcribe each pilot readback audio file.
2. Parse callsign, runway, instruction with tolerant matching.
3. Compare to the clearance. Emit readback (transcript, matched). Mismatch →
   readback_correction + spoken correction + alert.
4. Runs in a worker thread; never blocks the loop.
Done when: every readback shows a transcript, an injected wrong readback is
caught and corrected.

## Phase 8: Demo flow, polish, README
1. ~3 minute demo, same decisions every run: live METAR picks runway;
   Claude-assisted sequencing with voice; one unsafe departure rejected live;
   one wrong readback caught and corrected; injected wind shift flips the
   runway; traffic finishes cleanly.
2. Pre-warm script to fill audio_cache.
3. Dashboard polish: readable from the back of a room, no overlapping labels,
   "SIMULATION ONLY" badge.
4. run_demo.sh: starts Gus's simulator + backend + frontend, opens browser.
5. README: pitch, tech stack, architecture, how to run, .env setup,
   simulation-only disclaimer.
Done when: ./run_demo.sh runs the full demo three times in a row with no
manual fixes, including once with wifi off.

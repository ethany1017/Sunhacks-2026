# [Project Name] — Autonomous Airport Control

SunHacks 2026 hackathon project by Gus and Ethan.

An autonomous airport traffic management agent that runs in simulation. It sequences arrivals and departures, routes aircraft on the ground, talks to pilots in a synthetic voice, and shows every decision and the reason for it on a live traffic map.

> **Simulation only.** The agent runs the airport on its own, but everything it controls is simulated.

## What it does

- Tracks simulated aircraft from arrival handoff through landing and parking, and from departure readiness through taxi and takeoff.
- Plans arrival order, departure order, and taxi routes.
- Manages runway occupancy and crossings.
- Speaks clearances to pilots and checks their readbacks.
- Adapts to weather changes using live aviation weather reports.
- Records every decision with the facts and constraints behind it.

The agent issues instructions to pilots. Flying the aircraft is outside its scope.

## How it works

The control service runs headless. The dashboard only observes it, so the agent keeps running whether or not the dashboard is open.

```
Traffic / airport / weather feeds
              |
     Shared airport state
              |
     Autonomous AI agent
   (decides, issues clearances)
              |
      Voice communications
              |
      Simulated pilots
              |
  Feedback into airport state

Airport state + decisions + clearances → Dashboard
```

| Component | Responsibility |
| --- | --- |
| Shared airport state | Positions, intent, clearances, runway occupancy, and restrictions. |
| Autonomous agent | Runs the airport: sequences arrivals and departures, routes aircraft, manages runways and crossings, replans for weather, and issues every clearance itself. |
| Clearance manager | Tracks each instruction the agent issues from issued through acknowledged and completed. |
| Voice service | Speaks the agent's instructions and turns pilot replies into text for the agent. |
| Event recorder | Stores inputs, decisions, and communications for the dashboard. |

There is no second checker and no human approving instructions. The agent decides and acts on its own, and the dashboard shows what it did and why.

## Dashboard

The dashboard answers three questions: what is happening, what happens next, and why the agent chose it.

- **Live traffic map:** aircraft, airport geometry, routes, and predicted paths.
- **Arrivals and departures:** sequence, ETA, queue, and readiness.
- **Selected decision:** the action, supporting facts, binding constraints, and alternatives checked.
- **Runway timeline:** current occupancy and planned movements.
- **Voice and transcript:** spoken clearances, pilot readbacks, and alerts.
- **Weather and data health:** current conditions and how fresh each input is.

Explanations come from the recorded decision, not from a description generated afterward.

## Weather

Weather comes from the NOAA Aviation Weather Center API (METAR observations and TAF forecasts). The agent keeps the original report alongside the parsed values, flags stale data, and replans when conditions change.

## Demo scope

- **Airport:** Phoenix Sky Harbor International (KPHX)
- **Mode:** autonomous simulation with simulated pilots
- **Scenario (draft, to confirm):** a scripted haboob (dust storm) disruption at KPHX with 10-12 simulated aircraft, opening with a hold-short and readback exchange and the agent declining a conflicting crossing request, then a weather change that forces it to replan. The weather replay uses real observations from the August 25, 2025 haboob.

Not in the hackathon demo: live traffic observation, historical replay, and gate/ramp integration.

## Research and data

- [`data-catalog.md`](data-catalog.md): every data source for the build, with verified or unverified status, file paths, limits, conflicts, gaps and what must be synthetic. Part 2 merges Ethan's research (including the Deer Valley material, kept as a fallback).
- `data/`: downloaded samples (KPHX geometry, procedures, weather, traffic, aircraft and rules). Large raw files are not committed; the fetch scripts in `scripts/` rebuild them.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): proposed simulator architecture and build order.

## Getting started

[Setup and run instructions — to be added once the stack is chosen.]

## Tech stack

Proposed, not yet agreed (see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)): a custom Python simulator on a KPHX taxiway graph built from OpenStreetMap, an append-only JSONL event log, a small web API with WebSocket updates, and a MapLibre + deck.gl dashboard.

## Limitations

- Aircraft, pilots, and neighboring facilities are simulated.
- The agent follows the rules written into its rulebook for the demo, not the full set of real-world procedures.
- Public weather reports do not include every runway-local condition a real tower uses.

## Team

- Gus
- Ethan

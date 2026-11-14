# [Project Name] — Autonomous Airport Control

SunHacks 2026 hackathon project by Gus and Ethan.

An autonomous airport traffic management agent that runs in simulation. It sequences arrivals and departures, routes aircraft on the ground, talks to pilots in a synthetic voice, and shows every decision and the reason for it on a live traffic map.

> **Simulation only.** This project controls simulated aircraft. It does not transmit instructions to real aircraft and is not a certified air traffic control system.

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
        Traffic planner
              |
   Independent safety checks
              |
      Clearance manager
              |
   Voice communications
              |
      Simulated pilots
              |
  Feedback into airport state

Airport state + checks + clearances → Dashboard
```

| Component | Responsibility |
| --- | --- |
| Shared airport state | Positions, intent, clearances, runway occupancy, and restrictions. |
| Traffic planner | Proposes arrival order, departure order, and taxi routes. |
| Safety checker | Evaluates every proposal against modeled rules before it is issued. |
| Clearance manager | Tracks each instruction from proposed through acknowledged and completed. |
| Voice service | Speaks approved instructions and interprets pilot replies. |
| Event recorder | Stores inputs, decisions, and communications for the dashboard. |

A language model can help with dialogue and planning, but its free-form output never authorizes an aircraft movement directly. Every instruction goes through structured clearance handling and the independent safety checks first.

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

- **Airport:** [airport]
- **Mode:** autonomous simulation with simulated pilots
- **Scenario:** [demo scenario]

Not in the hackathon demo: live traffic observation, historical replay, and gate/ramp integration.

## Getting started

[Setup and run instructions — to be added once the stack is chosen.]

## Tech stack

[To be decided.]

## Limitations

- Aircraft, pilots, and neighboring facilities are simulated.
- The safety checks cover the rules modeled for the demo, not the full set of real-world procedures.
- A passing simulation is evidence for the scenarios tested, not proof of real-world safety.
- Public weather reports do not include every runway-local condition a real tower uses.

## Team

- Gus
- Ethan

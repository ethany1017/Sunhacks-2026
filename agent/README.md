# Claude agent (parked)

An optional Claude controller that can run the KPHX simulator through tool calls. It is **not** part of the simulator:
nothing under `sim/` or `api/` imports this folder, and the API and dashboard always run the airport with the built-in
rule-based tower (`sim/tower/`).

It is kept here so the work is not lost. It has only been tested against a fake Anthropic client (`tests/fakes.py`);
no real model call has been made.

## Plugging it in

A controller is any object with `name`, `status` and `on_tick(sim, events)`. Hand one to the simulator:

```python
from agent.claude_agent import ClaudeAgent
from sim.core.engine import Simulation

sim = Simulation("sim/scenarios/kphx_haboob.json")
sim.set_controller(ClaudeAgent())     # needs ANTHROPIC_API_KEY and `pip install -r agent/requirements.txt`
sim.set_controller("tower")           # hand the airport back to the built-in tower
```

The agent also exposes `service(sim)`, which runs queued tool calls between ticks; `api/ws.py` calls it if the controller
has one. On any API error the agent falls back to the built-in tower until a backoff expires.

## Files

- `claude_agent.py`: wake loop, worker thread, fallback to the tower
- `tools.py`: Anthropic tool definitions and the dispatcher bound to a `Simulation`
- `prompt.md`: system prompt
- `tests/`: agent tests (`make agent-test`)

## Settings

| Item | Value |
|---|---|
| Model | `claude-opus-5-5` at effort `low` (thinking is always on; low effort for speed), override with env `KPHX_AGENT_MODEL` / `KPHX_AGENT_EFFORT` |
| Wake | on events plus a 5 sim-second timer; one wake in flight; at most 8 tool calls and 15 s wall time each; budget exhaustion triggers an immediate follow-up wake |
| Threading | worker thread talks to the API; tool calls are queued and run by the simulation thread, so no locks |
| Failure | any API error: log (key scrubbed), the tower (primed with current clearances) covers for 5 s wall, doubling to 60 s, then Claude retries |
| Wake distance table | TBL 5-5-4 values from research_notes; the table numbering mismatch noted in data-catalog 3.12 is unresolved |
| `KPHX_AGENT_PAUSE_WHILE_THINKING=1` | pauses the clock during a wake (default off) |

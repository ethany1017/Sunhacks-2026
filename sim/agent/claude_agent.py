"""The autonomous Claude controller for KPHX.

Threading model (see docs/ARCHITECTURE.md): the simulation is stepped by one thread (the API runner or a headless loop).
The LLM call is slow, so each "wake" runs on a background worker thread, one in flight at a time. The worker NEVER touches
the Simulation: every tool call is queued and executed by the simulation thread inside `on_tick` / `service` (between
ticks), so no locks are needed and tool calls land at a deterministic point of a step. Events that arrive while a wake is in
flight are coalesced into the next wake. On any API error the baseline controller (primed with the current state) covers
the airport until an exponential backoff expires, then Claude is retried on its next wake.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from sim.agent.baseline import BaselineController
from sim.agent.handover import prime_baseline
from sim.agent.tools import TOOL_DEFS, ToolBox, compact_state, err
from sim.core.engine import Simulation

log = logging.getLogger("kphx.claude_agent")
PROMPT_PATH = Path(__file__).with_name("prompt.md")
DEFAULT_MODEL = "claude-sonnet-5-5"
EVENT_KEEP = {"aircraft.spawned": ("kind", "type", "runway", "gate"), "departure.ready": ("gate",), "aircraft.phase": ("to",),
              "aircraft.touchdown": ("runway",), "aircraft.airborne": ("runway",), "aircraft.completed": (), "aircraft.parked": ("gate",),
              "clearance.issued": ("action", "text"), "clearance.acknowledged": ("action",), "clearance.complete": ("action",),
              "clearance.amended": ("by",), "clearance.cancelled": ("reason",), "clearance.no_effect": ("action", "phase"),
              "readback": ("text", "status"), "readback.accepted": (), "readback.rejected": (), "go_around": ("reason", "runway"),
              "conflict": ("kind", "aircraft", "runway"), "separation_loss": ("aircraft",), "deadlock": ("aircraft",),
              "weather.haboob_triggered": (), "weather.update": ("vis_sm", "gust_kt", "wind_kt"), "runway.config": ("arrival_runways", "departure_runways"),
              "pushback.complete": (), "takeoff.roll": ("runway",)}
BEHIND = {"conflict", "separation_loss", "deadlock"}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class ClaudeConfig:
    model: str = ""
    max_tokens: int = 1500
    max_tool_calls: int = 8              # per wake
    wake_wall_s: float = 15.0            # wall-clock cap per wake
    timer_s: float = 5.0                 # sim seconds between timer wakes
    clearance_timeout_s: float = 20.0    # an unacknowledged clearance older than this wakes the agent
    recent_events: int = 20              # events included in the wake message
    recent_decisions: int = 6            # own past decisions echoed back (the model keeps no memory between wakes)
    pause_while_thinking: bool = False
    sync: bool = False                   # run each wake inline (tests / headless determinism)
    backoff_base_s: float = 5.0          # wall seconds the baseline covers after the first API failure; doubles to backoff_max_s
    backoff_max_s: float = 60.0

    def __post_init__(self) -> None:
        self.model = self.model or os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL
        if _env_flag("CLAUDE_PAUSE_WHILE_THINKING"):
            self.pause_while_thinking = True


def make_client() -> Any:
    import anthropic
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0)


def _scrub(text: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    text = text.replace(key, "***") if key else text
    return text[:300]


class _Call:
    """A tool call waiting for the simulation thread."""

    def __init__(self, epoch: Any, name: str, args: Any) -> None:
        self.epoch, self.name, self.args = epoch, name, args
        self.event = threading.Event()
        self.result: Dict[str, Any] = {}
        self.cancelled = False


class ClaudeAgent:
    name = "claude"

    def __init__(self, client: Any = None, config: Optional[ClaudeConfig] = None, now: Any = time.monotonic) -> None:
        self.cfg = config or ClaudeConfig()
        self.client = client if client is not None else make_client()
        self._now = now
        text = PROMPT_PATH.read_text()
        self._system = [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
        self._calls: "queue.Queue[_Call]" = queue.Queue()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._outcome: Optional[Dict[str, Any]] = None
        self._busy = False
        self._worker_done = False
        self._paused_us = False
        self._sim: Optional[Simulation] = None
        self._epoch: Any = None
        self._toolbox: Optional[ToolBox] = None
        self._fallback: Optional[BaselineController] = None
        self._fails = 0
        self._backoff_until = 0.0
        self._reset_state()
        self.stats = {"wakes": 0, "tool_calls": 0, "api_errors": 0, "fallback_ticks": 0, "budget_exhausted": 0}

    # -- state ---------------------------------------------------------------------------------------------
    def _reset_state(self) -> None:
        self._reasons: List[str] = ["start"]
        self._events: Deque[Dict[str, Any]] = deque(maxlen=80)
        self._last_wake_t = -1e9
        self._wx_seen: Optional[Dict[str, Any]] = None
        self._hold_seen: set = set()
        self._timeout_seen: set = set()
        self._fallback = None

    @property
    def status(self) -> str:
        return "thinking" if self._busy and not self._worker_done else "idle"

    @property
    def last_error(self) -> Optional[str]:
        return getattr(self, "_last_error", None)

    # -- simulation-thread entry points --------------------------------------------------------------------
    def on_tick(self, sim: Simulation, events: List[Dict[str, Any]]) -> None:
        try:
            self._tick(sim, events)
        except Exception as e:       # the controller must never take the simulation down
            log.exception("claude controller tick failed: %s", _scrub(str(e)))
            self._fallback_tick(sim, events)

    def service(self, sim: Simulation) -> None:
        """Run queued tool calls and process a finished wake. Safe to call every loop iteration, also while the clock is paused."""
        self._sim = sim
        epoch = sim.log
        if epoch is not self._epoch:
            self._new_epoch(sim, epoch)
        while True:
            try:
                call = self._calls.get_nowait()
            except queue.Empty:
                break
            if call.cancelled:
                continue
            call.result = self._run_tool(sim, call)
            call.event.set()
        with self._lock:
            outcome, self._outcome = self._outcome, None
        if outcome is not None:
            self._finish_wake(sim, outcome)

    def shutdown(self) -> None:
        """Stop acting: fail queued tool calls, let the in-flight worker end, release a pause we caused."""
        self._stop.set()
        while True:
            try:
                call = self._calls.get_nowait()
            except queue.Empty:
                break
            call.result = err("controller stopped")
            call.event.set()
        if self._paused_us and self._sim is not None:
            self._sim.clock.paused = False
            self._paused_us = False
        self._busy = False
        self._fallback = None

    def join(self, timeout: float = 5.0) -> bool:
        t = self._thread
        if t is not None:
            t.join(timeout)
            return not t.is_alive()
        return True

    # -- per tick --------------------------------------------------------------------------------------------
    def _tick(self, sim: Simulation, events: List[Dict[str, Any]]) -> None:
        self.service(sim)
        if self._stop.is_set():
            return
        if self._fallback is not None:
            if self._now() < self._backoff_until:
                self._fallback_tick(sim, events)
                return
            self._fallback = None                       # backoff over: Claude takes the next wake
        self._note_events(sim, events)
        if sim.clock.tick % 5 == 0:
            self._poll_state(sim)
        if self._busy or sim.finished:
            return
        if sim.t - self._last_wake_t >= self.cfg.timer_s:
            self._reasons.append("timer")
        if self._reasons:
            self._start_wake(sim)
            if self.cfg.sync:
                self.service(sim)

    def _fallback_tick(self, sim: Simulation, events: List[Dict[str, Any]]) -> None:
        if self._fallback is None:
            self._fallback = prime_baseline(BaselineController(), sim)
        self.stats["fallback_ticks"] += 1
        self._fallback.on_tick(sim, events)

    def _new_epoch(self, sim: Simulation, epoch: Any) -> None:
        """First sight of this simulation, or the scenario was reset: drop everything tied to the old run."""
        first = self._epoch is None
        self._epoch = epoch
        self._toolbox = ToolBox(sim)
        if not first:
            self._reset_state()
            if self._paused_us:
                sim.clock.paused = False
                self._paused_us = False

    # -- triggers ----------------------------------------------------------------------------------------------
    def _note_events(self, sim: Simulation, events: List[Dict[str, Any]]) -> None:
        for e in events:
            kind, d = e["kind"], e.get("data", {})
            keep = EVENT_KEEP.get(kind)
            if keep is not None:
                item = {"t": round(e["sim_time_ms"] / 1000.0, 1), "kind": kind}
                if e.get("entity_id"):
                    item["who"] = e["entity_id"]
                item.update({k: d[k] for k in keep if k in d and d[k] is not None})
                self._events.append(item)
            who = e.get("entity_id") or ""
            if kind == "aircraft.spawned" and d.get("kind") == "arrival":
                self._reasons.append("new arrival %s" % who)
            elif kind == "departure.ready":
                self._reasons.append("%s ready for departure" % who)
            elif kind == "aircraft.phase" and d.get("to") in ("hold_short", "line_up", "taxi_in", "taxi_out", "parked", "runway_exit", "go_around"):
                self._reasons.append("%s now %s" % (who, d["to"]))
            elif kind == "aircraft.completed":
                self._reasons.append("%s complete" % who)
            elif kind == "readback" and d.get("status") == "pending":
                self._reasons.append("readback from %s needs a verdict" % who)
            elif kind in BEHIND or kind == "go_around":
                self._reasons.append("%s (%s)" % (kind, who))
            elif kind == "weather.haboob_triggered":
                self._reasons.append("weather: haboob started")
            elif kind == "weather.update" and self._weather_changed(d):
                self._reasons.append("weather change")

    def _weather_changed(self, w: Dict[str, Any]) -> bool:
        cur = {"vis": w.get("vis_sm"), "gust": bool(w.get("gust_kt")), "wind": w.get("wind_kt") or 0}
        old, self._wx_seen = self._wx_seen, cur
        if old is None:
            return False
        vis_jump = (old["vis"] is not None and cur["vis"] is not None and abs(old["vis"] - cur["vis"]) >= 1.0)
        return vis_jump or old["gust"] != cur["gust"] or abs(old["wind"] - cur["wind"]) >= 8

    def _poll_state(self, sim: Simulation) -> None:
        """Conditions that are not log events: an aircraft stopped at a runway hold line, a clearance nobody acknowledged."""
        for ac in sim.aircraft.values():
            if ac.removed or ac.kind == "vehicle" or ac.speed >= 0.2 or ac.stop_why not in ("run", "busy"):
                continue
            if ac.phase in ("taxi_in", "taxi_out", "hold_short") and ac.stopped_since is not None:
                key = (ac.callsign, round(ac.stopped_since, 1))
                if key not in self._hold_seen:
                    self._hold_seen.add(key)
                    self._reasons.append("%s stopped at a runway hold line" % ac.callsign)
        for cl in sim.clearances.values():
            if cl.state == "issued" and sim.t - cl.issued_t >= self.cfg.clearance_timeout_s and cl.id not in self._timeout_seen:
                self._timeout_seen.add(cl.id)
                self._reasons.append("clearance %s to %s unacknowledged for %.0f s" % (cl.id, cl.callsign, sim.t - cl.issued_t))

    # -- waking the agent ------------------------------------------------------------------------------------------
    def _context(self, sim: Simulation, reasons: List[str]) -> str:
        state = compact_state(sim)
        decisions = list(sim.decisions.values())[-self.cfg.recent_decisions:]
        ctx = {"wake": reasons, **state,
               "recent_events": list(self._events)[-self.cfg.recent_events:],
               "your_recent_decisions": [{"t": d["t"], "cs": d["callsign"], "action": d["action"], "summary": d["summary"],
                                          "review_in_s": d["next_review_s"]} for d in decisions]}
        return "Wake at sim t=%.1f s. Act on whatever needs a decision now; if nothing does, reply with one short line.\n%s" % (
            sim.t, json.dumps(ctx, separators=(",", ":")))

    def _start_wake(self, sim: Simulation) -> None:
        seen: Dict[str, int] = {}
        for r in self._reasons:
            seen[r] = seen.get(r, 0) + 1
        reasons = [r if n == 1 else "%s x%d" % (r, n) for r, n in seen.items()][:12]
        self._reasons = []
        self._last_wake_t = sim.t
        self.stats["wakes"] += 1
        message = self._context(sim, reasons)
        epoch = self._epoch
        if self.cfg.sync:
            self._busy = True
            self._wake(epoch, message)
            return
        self._busy, self._worker_done = True, False
        if self.cfg.pause_while_thinking and not sim.clock.paused:
            sim.clock.paused = True
            self._paused_us = True
        self._thread = threading.Thread(target=self._wake, args=(epoch, message), name="claude-wake", daemon=True)
        self._thread.start()

    def _finish_wake(self, sim: Simulation, outcome: Dict[str, Any]) -> None:
        self._busy = False
        if self._paused_us:
            sim.clock.paused = False
            self._paused_us = False
        if outcome.get("error"):
            self._fails += 1
            self._last_error = outcome["error"]
            self.stats["api_errors"] += 1
            wait = min(self.cfg.backoff_base_s * (2 ** (self._fails - 1)), self.cfg.backoff_max_s)
            self._backoff_until = self._now() + wait
            log.warning("claude wake failed (%s); baseline covers for %.0f s", outcome["error"], wait)
            self._fallback = prime_baseline(BaselineController(), sim)
        else:
            self._fails = 0
            if outcome.get("exhausted"):
                self.stats["budget_exhausted"] += 1
                self._reasons.append("continue: the previous wake used its %d tool calls" % self.cfg.max_tool_calls)

    # -- worker thread: no simulation access below this line ---------------------------------------------------------------
    def _wake(self, epoch: Any, message: str) -> None:
        outcome: Dict[str, Any] = {}
        try:
            outcome = self._converse(epoch, message)
        except Exception as e:
            outcome = {"error": "%s: %s" % (type(e).__name__, _scrub(str(e)))}
        finally:
            self._worker_done = True
            with self._lock:
                self._outcome = outcome

    @staticmethod
    def _param(block: Any) -> Dict[str, Any]:
        if block.type == "tool_use":
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
        return {"type": "text", "text": getattr(block, "text", "")}

    def _converse(self, epoch: Any, message: str) -> Dict[str, Any]:
        start = self._now()
        msgs: List[Dict[str, Any]] = [{"role": "user", "content": message}]
        calls, exhausted = 0, False
        while not self._stop.is_set():
            remaining = self.cfg.wake_wall_s - (self._now() - start)
            if remaining <= 0.05 * self.cfg.wake_wall_s:
                break
            try:
                resp = self.client.messages.create(model=self.cfg.model, max_tokens=self.cfg.max_tokens, system=self._system,
                                                   tools=TOOL_DEFS, messages=msgs, timeout=remaining)
            except Exception as e:
                return {"error": "%s: %s" % (type(e).__name__, _scrub(str(e))), "calls": calls}
            blocks = list(resp.content)
            msgs.append({"role": "assistant", "content": [self._param(b) for b in blocks]})
            uses = [b for b in blocks if b.type == "tool_use"]
            if not uses:
                break
            results = []
            for b in uses:
                if calls >= self.cfg.max_tool_calls:
                    exhausted = True
                    res = err("tool-call budget for this wake is used up; you will be woken again right away")
                else:
                    calls += 1
                    res = self._exec(epoch, b.name, b.input, self.cfg.wake_wall_s - (self._now() - start))
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(res, separators=(",", ":"))})
            msgs.append({"role": "user", "content": results})
            if exhausted:
                break
        self.stats["tool_calls"] += calls
        return {"calls": calls, "exhausted": exhausted}

    def _exec(self, epoch: Any, name: str, args: Any, remaining: float) -> Dict[str, Any]:
        call = _Call(epoch, name, args)
        if self.cfg.sync:
            return self._run_tool(self._sim, call)  # type: ignore[arg-type]
        self._calls.put(call)
        if not call.event.wait(timeout=max(remaining, 0.1)):
            call.cancelled = True
            return err("the simulation did not run this call in time (is it paused?)")
        return call.result

    def _run_tool(self, sim: Simulation, call: _Call) -> Dict[str, Any]:
        if call.epoch is not sim.log or self._toolbox is None:
            return err("the scenario was reset; this call was discarded")
        try:
            return self._toolbox.call(call.name, call.args)
        except Exception as e:            # a tool bug must never stop the simulation
            log.exception("tool %s crashed", call.name)
            return err("internal error in %s: %s" % (call.name, _scrub(str(e))))


ClaudeController = ClaudeAgent   # name the engine's lazy import looks for

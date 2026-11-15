"""Headless sim run: load a scenario (format in world.py), tick the world, hand each tick's events to the
caller's `emit(events)` as one list. Runs as a background task."""
import asyncio
import contextlib
import json
import logging
import re
from pathlib import Path

from . import world
from .clock import TICK_S, Clock

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
REASON = {
    "approach": "On final approach.", "landing": "Short final, landing.", "rollout": "Touched down, rolling out.",
    "taxi": "Taxiing on the scripted route.", "holding": "Holding short of the runway.",
    "lineup": "Lining up on the runway.", "takeoff": "Starting the takeoff roll.",
    "departed": "Airborne, climbing out.", "parked": "Parked.",
}
status = {"sim": "idle", "t": 0.0}  # read by /health
log = logging.getLogger(__name__)
_task = None
_lock = asyncio.Lock()


def load(name, graph):
    """Scenario dict and its validated aircraft. FileNotFoundError for an unknown or unsafe name."""
    path = SCENARIO_DIR / f"{name}.json"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or not path.is_file():
        raise FileNotFoundError(f"no scenario named {name!r}")
    scenario = json.loads(path.read_text())
    return scenario, world.build(scenario, graph)


def _ev(t, type, callsign=None, data=None, reason=""):
    return {"t": t, "type": type, "callsign": callsign, "data": data or {}, "reason": reason}


async def _write(emit, events):
    fut = asyncio.ensure_future(asyncio.to_thread(emit, events))
    try:
        await asyncio.shield(fut)
    except asyncio.CancelledError:
        await fut  # let the in-flight transaction land before a new run starts
        raise


async def _end(emit, t, reason):
    """Final sim_end for a run that did not finish on its own."""
    try:
        await asyncio.to_thread(emit, [_ev(t, "sim_end", reason=reason)])
    except Exception:
        log.exception("could not write sim_end")


async def run(scenario, aircraft, speed, apt, emit):
    """emit: synchronous callable taking a list of event dicts (t, type, callsign, data, reason); run in a thread."""
    cfg, clock = apt["config"], Clock(speed)
    pending, active = sorted(aircraft, key=lambda a: a.spawn_t), []
    batch = [
        _ev(0.0, "sim_start", data={"airport": cfg["name"], "icao": cfg["icao"], "speed": speed,
                                    "scenario": scenario["name"]}, reason=f"Scenario {scenario['name']} started."),
        _ev(0.0, "runway_change", data={"runway": scenario["runway"], "previous": None}, reason="Set by the scenario."),
    ]
    status.update(sim="running", t=0.0)
    try:
        while True:
            t = clock.t
            for a in active:
                for dt, pos in a.step(TICK_S):
                    batch.append(_ev(round(t - TICK_S + dt, 2), "position", a.callsign, pos, REASON[pos["phase"]]))
            while pending and pending[0].spawn_t <= t:
                a = pending.pop(0)
                active.append(a)
                pos = a.report()
                batch.append(_ev(t, "aircraft_spawn", a.callsign, {
                    "kind": a.kind, "aircraft_type": a.aircraft_type, "lat": pos["lat"], "lon": pos["lon"],
                    "heading": pos["heading"], "phase": a.phase}, REASON[a.phase]))
            active = [a for a in active if not a.done]
            batch += [_ev(t, "position", a.callsign, a.report()) for a in active]
            finished = not pending and not active
            if finished:
                batch.append(_ev(t, "sim_end", reason="Every aircraft is parked or departed."))
            batch.sort(key=lambda e: e["t"])  # stable: phase changes inside the tick come first
            await _write(emit, batch)
            status["t"] = t
            if finished:
                status["sim"] = "finished"
                return
            batch = []
            await clock.tick()
    except asyncio.CancelledError:
        await _end(emit, clock.t, "Run cancelled before every aircraft finished.")
        raise
    except Exception as e:
        log.exception("sim run %s failed", scenario["name"])
        status["sim"] = "idle"
        await _end(emit, clock.t, f"Run failed: {e!r}")


async def stop():
    if _task and not _task.done():
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task


async def start(name, speed, apt, emit):
    """Cancel any running sim and start a fresh run of scenario `name`."""
    global _task
    async with _lock:
        scenario, aircraft = load(name, apt["graph"])  # fails before touching the current run
        await stop()
        _task = asyncio.create_task(run(scenario, aircraft, speed, apt, emit))
        return {"scenario": name, "speed": speed}

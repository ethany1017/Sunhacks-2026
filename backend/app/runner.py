"""The run loop: owns one simulator run at a time, steps it in real time and writes events to the SQLite store.

Runs as an asyncio task started with the app, so it keeps going with no dashboard connected. The sim is not
thread-safe: it is only touched from the event-loop thread (the loop task and the async REST handlers, which run
between ticks because the loop only yields between ticks).
"""
import asyncio
import logging
import os
import time

import simif
from atc.controller import Controller

from . import db

POSITION_EVERY_S = 1.0
METRICS_EVERY_S = 5.0
TICK_S = 0.1
MAX_TICKS_PER_PASS = 200  # keeps the loop responsive if it falls behind (fast SIM_SPEED)
log = logging.getLogger(__name__)
SCORE_KEYS = ("fails", "completed", "avg_delay_s", "total_delay_s", "go_arounds", "diversions",
              "readback_errors_caught", "readback_errors_accepted", "runway_operations")


class Runner:
    def __init__(self, sim=None):
        self.sim = sim or simif.connect()
        self.run_id = None
        self.ended = True
        self._buf = []
        self._next_pos = self._next_metrics = 0.0

    # -- the dashboard's only permitted actions ------------------------------------------------------
    def options(self):
        return {"levels": [{"id": lv["id"], "name": lv["name"]} for lv in self.sim.get_levels()],
                "controllers": [{"id": k, "label": v} for k, v in self.sim.get_controllers().items()],
                "current": self.current()}

    def current(self):
        if self.run_id is None:
            return None
        lv = self.sim.get_level()
        return {"run_id": self.run_id, "level": lv["id"], "seed": lv["seed"], "controller": self.sim.controller,
                "t": round(self.sim.t, 1), "ended": self.ended}

    def start(self, level, controller, seed=None):
        """Start (or restart) a run. ValueError for an unknown level or controller."""
        if controller not in self.sim.get_controllers():
            raise ValueError(f"unknown controller {controller!r}")
        active = self.run_id is not None and not self.ended
        t_old = self.sim.t if active else 0
        self.sim.reset(level, seed)  # raises on a bad level/seed with the old run untouched, so end it only after
        if active:
            self._add(t_old, "sim_end", data={"why": "reset"}, reason="Run reset from the dashboard.")
            self.flush()
        self._plug(controller)
        lv, labels = self.sim.get_level(), self.sim.get_controllers()
        self.run_id, self.ended = db.start_run(), False
        self._next_pos = self._next_metrics = 0.0
        self._add(0, "run_start", data={"level": lv["id"], "level_name": lv["name"], "controller": controller,
                                        "controller_label": labels[controller], "seed": lv["seed"]},
                  reason=f"{lv['name']} started with the {labels[controller]} controller, seed {lv['seed']}.")
        self._add(0, "airport", data=self.sim.get_airport(), reason="Airport layout from the simulator.")
        self.flush()

    def switch(self, controller):
        if self.run_id is None or self.ended:
            raise ValueError("no run in progress")
        old = self.sim.controller
        if controller == old:
            return
        self._plug(controller)
        labels = self.sim.get_controllers()
        self._add(self.sim.t, "controller_switch", data={"from": old, "to": controller, "label": labels[controller]},
                  reason=f"Controller switched from {labels[old]} to {labels[controller]} at t={self.sim.t:.0f} s.")
        self.flush()

    def _plug(self, controller):
        self.sim.set_controller(controller, Controller() if controller == "agent" else None)

    # -- stepping ------------------------------------------------------------------------------------------
    def tick(self):
        """One sim tick plus the backend's own position/metrics events."""
        events = self.sim.step()
        for e in events:
            if e["type"] == "sim_end":
                continue  # written last, after this tick's positions and metrics
            self._buf.append(e)
            if e["type"] == "referee_result":
                self._add(e["t"], "metrics", data=self._metrics_data(e["data"]["verdict"], e["data"]["score"]))
        t = self.sim.t
        if t + 1e-6 >= self._next_pos:
            self._next_pos += POSITION_EVERY_S
            m = self.sim.metrics()
            for a in m["aircraft"]:
                self._add(t, "position", a["callsign"], {
                    "lat": a["lat"], "lon": a["lon"], "heading": a["heading_deg"], "phase": a["phase"], "kind": a["kind"],
                    "aircraft_type": a["type"], "speed_kt": round(a["speed_mps"] / 0.514444), "altitude_ft": a["altitude_ft"]})
            if t + 1e-6 >= self._next_metrics:
                self._next_metrics += METRICS_EVERY_S
                self._add(t, "metrics", data=self._metrics_data(m["referee"]["verdict"], m["referee"]["score"]))
        self._buf += [e for e in events if e["type"] == "sim_end"]
        if self.sim.finished:
            self.ended = True

    def _metrics_data(self, verdict, score):
        return {"verdict": verdict, "controller": self.sim.controller, **{k: score.get(k) for k in SCORE_KEYS}}

    def run_headless(self):
        """Run to the end of the day as fast as possible (tests, harness)."""
        while not self.ended:
            self.tick()
        self.flush()

    def _add(self, t, type, callsign=None, data=None, reason=""):
        self._buf.append({"t": round(t, 1), "type": type, "callsign": callsign, "data": data or {}, "reason": reason})

    def flush(self):
        if self._buf and self.run_id is not None:
            db.emit_many(self.run_id, self._buf)  # ponytail: synchronous write on the loop thread, a few ms per pass
        self._buf = []

    async def loop(self):
        speed = float(os.environ.get("SIM_SPEED", "1"))
        run, wall0, sim0 = None, 0.0, 0.0
        while True:
            try:
                if self.run_id != run:  # a new run: restart the wall-clock pacing
                    run, wall0, sim0 = self.run_id, time.monotonic(), self.sim.t
                if run is not None and not self.ended:
                    target = sim0 + (time.monotonic() - wall0) * speed
                    n = 0
                    while not self.ended and self.sim.t + TICK_S / 2 <= target and n < MAX_TICKS_PER_PASS:
                        self.tick()
                        n += 1
                    if n == MAX_TICKS_PER_PASS:  # fell behind: drop the backlog instead of racing to catch up
                        wall0, sim0 = time.monotonic(), self.sim.t
                    self.flush()
            except Exception:
                log.exception("run loop error; the run is stopped")
                self._add(self.sim.t, "sim_end", data={"why": "error"}, reason="The run stopped on a backend error.")
                self.flush()
                self.ended = True
            await asyncio.sleep(0.05)

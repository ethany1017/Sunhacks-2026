"""The run loop: owns one simulator run at a time, steps it in real time and writes events to the SQLite store.

Runs as an asyncio task started with the app, so it keeps going with no dashboard connected. The sim is not
thread-safe: it is only touched from the event-loop thread (the loop task and the async REST handlers, which run
between ticks because the loop only yields between ticks).
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path

import simif
from atc.controller import Controller

from . import db

POSITION_EVERY_S = 1.0
METRICS_EVERY_S = 5.0
TICK_S = 0.1
MAX_TICKS_PER_PASS = 200  # keeps the loop responsive if it falls behind (fast SIM_SPEED)
log = logging.getLogger(__name__)
SUMMARY = Path(__file__).resolve().parents[1] / "eval_results/summary.json"  # written by `python -m app.evaluate --publish`
DEMO_SEED_ENV = {"normal_day": "DEMO_SEED_NORMAL_DAY", "light_rain": "DEMO_SEED_LIGHT_RAIN"}  # dashboard default seed
MAX_SEED = 2**31 - 1
SCORE_KEYS = ("fails", "completed", "avg_delay_s", "total_delay_s", "go_arounds", "diversions",
              "readback_errors_caught", "readback_errors_accepted", "runway_operations")


class Runner:
    def __init__(self, sim=None, voice=None):
        self.sim = sim or simif.connect()
        self.voice = voice  # atc.voice_out.Voice, live server only (None in run_headless, tests and the harness)
        self.run_id = None
        self.agent = None  # our Controller while the Agent is plugged in
        self.ended = True
        self._buf = []
        self._next_pos = self._next_metrics = 0.0
        self.demo_seeds = {lv: self._env_seed(lv, var) for lv, var in DEMO_SEED_ENV.items()}

    def _env_seed(self, level, var):
        """The seed a run of `level` gets when none is given, from env `var`; None (the level's first training seed) if
        unset or unusable. A held-out seed is never used here: those are for final grading only."""
        raw = os.environ.get(var, "").strip()
        if not raw:
            return None
        try:
            seed = int(raw)
        except ValueError:
            seed = None
        heldout = self.sim.get_seeds(level)["heldout"] if hasattr(self.sim, "get_seeds") else []
        if seed is None or not 0 <= seed <= MAX_SEED or seed in heldout:
            log.warning("%s=%r is not a usable demo seed (an integer 0..%d, not a held-out seed); using the first "
                        "training seed", var, raw, MAX_SEED)
            return None
        return seed

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
        if seed is None:
            seed = self.demo_seeds.get(level)
        active = self.run_id is not None and not self.ended
        t_old = self.sim.t if active else 0
        self.sim.reset(level, seed)  # raises on a bad level/seed with the old run untouched, so end it only after
        if active:
            self._add(t_old, "sim_end", data={"why": "reset"}, reason="Run reset from the dashboard.")
            self.flush()
        self._plug(controller)
        if self.voice:
            self.voice.clear()  # don't speak the old run's queued lines
        lv, labels = self.sim.get_level(), self.sim.get_controllers()
        self.run_id, self.ended = db.start_run(), False
        self._next_pos = self._next_metrics = 0.0
        self._add(0, "run_start", data={"level": lv["id"], "level_name": lv["name"], "controller": controller,
                                        "controller_label": labels[controller], "seed": lv["seed"]},
                  reason=f"{lv['name']} started with the {labels[controller]} controller, seed {lv['seed']}.")
        self._add(0, "airport", data=self.sim.get_airport(), reason="Airport layout from the simulator.")
        wx, rwy = self.sim.get_weather(), self.sim.get_runway_config()
        self._add(0, "weather", data=wx, reason=f"Weather: {wx['metar']}.")
        self._add(0, "runway_change", data=rwy, reason=f"Runway configuration: {rwy['runway']}.")
        if SUMMARY.exists():
            self._add(0, "metrics", data={"comparison": json.loads(SUMMARY.read_text())},
                      reason="Latest Agent vs Human evaluation.")
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
        self.agent = Controller(self.sim, self._add) if controller == "agent" else None
        self.sim.set_controller(controller, self.agent)

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
        """Run to the end of the day as fast as possible (tests, harness). No event loop runs here, so the agent takes
        the planner's deterministic default at every key decision (repeatable scores, no network) unless
        CLAUDE_IN_HARNESS=1, which runs the day on an event loop and holds sim time while Claude answers (<= 3 s)."""
        if os.environ.get("CLAUDE_IN_HARNESS", "0") == "1":
            return asyncio.run(self._run_headless_with_claude())
        while not self.ended:
            self.tick()
        self.flush()

    async def _run_headless_with_claude(self):
        while not self.ended:
            self.tick()
            while self.agent is not None and self.agent.pending():
                await asyncio.sleep(0.01)
        self.flush()

    def _add(self, t, type, callsign=None, data=None, reason=""):
        self._buf.append({"t": round(t, 1), "type": type, "callsign": callsign, "data": data or {}, "reason": reason})

    def flush(self):
        if self._buf and self.run_id is not None:
            db.emit_many(self.run_id, self._buf)  # ponytail: synchronous write on the loop thread, a few ms per pass
        self._buf = []

    def _speak(self, events):
        """Queue every clearance (tower) and readback (pilot) for speech, in order. Called after they are written, so
        each audio event lands after its line in the store."""
        for e in events:
            d = e["data"]
            if e["type"] == "clearance" and d.get("text"):
                line = {"speaker": "tower", "text": d["text"], "clearance_id": d.get("clearance_id")}
            elif e["type"] == "readback" and d.get("transcript"):
                line = {"speaker": "pilot", "text": d["transcript"], "readback_id": d.get("readback_id"),
                        "clearance_id": d.get("clearance_id")}
            else:
                continue
            self.voice.speak(dict(line, run_id=self.run_id, t=e["t"], callsign=e["callsign"]), self._audio_ready)

    def _audio_ready(self, line, name, source, voice):
        # Worker thread: its own short-lived DB connection; a line of a run that has since been reset is dropped.
        if line["run_id"] != self.run_id:
            return
        data = {k: v for k, v in line.items() if k not in ("run_id", "t", "callsign")}
        db.emit(line["run_id"], "audio", line["t"], line["callsign"],
                dict(data, url=f"/api/audio/{name}", source=source, voice=voice, wall=round(time.time(), 3)),
                f"{line['speaker'].capitalize()} audio ({source}).")

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
                    lines = self._buf
                    self.flush()
                    if self.voice:
                        self._speak(lines)
            except Exception:
                log.exception("run loop error; the run is stopped")
                self._add(self.sim.t, "sim_end", data={"why": "error"}, reason="The run stopped on a backend error.")
                self.flush()
                self.ended = True
            await asyncio.sleep(0.05)

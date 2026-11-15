"""Headless run: python -m sim.core.run sim/scenarios/kphx_smoke.json [--seed N] [--run-dir DIR]
              python -m sim.core.run --level normal_day|light_rain --seed N [--duration S] [--run-dir DIR]"""
from __future__ import annotations

import argparse
import hashlib
import time
from pathlib import Path

from sim.core.engine import Simulation
from sim.metrics.score import format_table


def main() -> None:
    ap = argparse.ArgumentParser(description="Run a KPHX scenario or level to completion with the built-in tower or the Human")
    ap.add_argument("scenario", nargs="?", default=None)
    ap.add_argument("--level", default=None, help="normal_day | light_rain (instead of a scenario file)")
    ap.add_argument("--duration", type=float, default=None, help="level only: scheduled traffic window in sim seconds")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--controller", default="tower", choices=("tower", "human"))
    args = ap.parse_args()
    if (args.scenario is None) == (args.level is None):
        ap.error("give a scenario file or --level")
    name = Path(args.scenario).stem if args.scenario else "%s-seed%s" % (args.level, args.seed)
    wall = time.time()
    sim = Simulation(args.scenario, seed=args.seed, controller=args.controller, level=args.level, duration_s=args.duration,
                     run_dir=Path(args.run_dir) if args.run_dir else Path("runs") / name)
    sim.run_to_completion()
    sim.close()
    status = "completed" if any(e["kind"] == "scenario.complete" for e in sim.log.events) else "day ended"
    print("%s: %s at sim t=%.1f s, %d events, seed %d, %d flights, wall %.1f s" % (
        sim.scenario.name, status, sim.t, len(sim.log.events), sim.seed, len(sim.scenario.flights), time.time() - wall))
    ref = sim.referee_report()
    print("REFEREE: %s (%d fails)" % (ref["verdict"], len(ref["fails"])))
    for f in ref["fails"]:
        print("  FAIL %-18s t=%7.1f %s" % (f["rule"], f["t"], f["reason"]))
    print(format_table(ref["score"]))
    print("fails by rule: %s" % ", ".join("%s=%d" % kv for kv in ref["score"]["fails_by_rule"].items()))
    if sim.log.path:
        print("events: %s (sha256 %s)" % (sim.log.path, hashlib.sha256(sim.log.path.read_bytes()).hexdigest()[:16]))
    for e in sim.log.events:
        if e["kind"] in ("weather.update", "runway.config", "pilot.unable", "arrival.reversed"):
            print("  %s t=%.1f %s %s" % (e["kind"], e["sim_time_ms"] / 1000, e["entity_id"] or "", e["data"]))
    bad = [e for e in sim.log.events if e["kind"] in ("conflict", "separation_loss", "deadlock")]
    for e in bad[:20]:
        print("  %s t=%.1f %s" % (e["kind"], e["sim_time_ms"] / 1000, e["data"]))


if __name__ == "__main__":
    main()

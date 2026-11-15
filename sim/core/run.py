"""Headless run: python -m sim.core.run sim/scenarios/kphx_smoke.json [--seed N] [--run-dir DIR]"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from sim.core.engine import Simulation
from sim.metrics.score import format_table


def main() -> None:
    ap = argparse.ArgumentParser(description="Run a KPHX scenario to completion with the baseline controller")
    ap.add_argument("scenario")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--controller", default="baseline")
    args = ap.parse_args()
    sim = Simulation(args.scenario, seed=args.seed, controller=args.controller,
                     run_dir=Path(args.run_dir) if args.run_dir else Path("runs") / Path(args.scenario).stem)
    sim.run_to_completion()
    sim.close()
    status = "completed" if sim.finished else "DID NOT FINISH"
    print("%s: %s at sim t=%.1f s, %d events, seed %d" % (sim.scenario.name, status, sim.t, len(sim.log.events), sim.seed))
    print(format_table(sim.score()))
    if sim.log.path:
        print("events: %s (sha256 %s)" % (sim.log.path, hashlib.sha256(sim.log.path.read_bytes()).hexdigest()[:16]))
    bad = [e for e in sim.log.events if e["kind"] in ("conflict", "separation_loss", "deadlock")]
    for e in bad[:20]:
        print("  %s t=%.1f %s" % (e["kind"], e["sim_time_ms"] / 1000, e["data"]))


if __name__ == "__main__":
    main()

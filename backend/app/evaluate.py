"""Evaluation harness: run controllers through levels and seeds headless, judged only by the simulator's Referee.

    cd backend && .venv/bin/python -m app.evaluate                       # human + agent, every level, training seeds
    .venv/bin/python -m app.evaluate --controllers human --levels normal_day --seeds 1,2
    .venv/bin/python -m app.evaluate --levels practice                  # the practice scenarios only
    .venv/bin/python -m app.evaluate --seeds heldout                    # final grading only (prints a note)
    add --publish to save eval_results/summary.json and push a `metrics` event into the dashboard's current run.

Each run is the backend's own headless loop (app.runner.Runner.run_headless) in a worker process with a throwaway
event store; the verdict, fails and score come from the adapter's metrics() (the Referee's report), nothing else.
"""
import argparse
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone

import simif

from . import db
from .runner import SUMMARY, Runner

RESULTS = SUMMARY.parent
REAL_LEVELS = ("normal_day", "light_rain")
SECTIONS = {"training": "TRAINING SEEDS", "heldout": "HELD-OUT SEEDS (final grading only)", "practice": "PRACTICE SCENARIOS"}
FAILS_SHOWN = 5  # ponytail: per run in the text report; the JSON keeps every fail
_SIM = None


def plan(sim, controllers, levels, seeds="training"):
    """[(controller, level, seed, section)]. Held-out seeds only with seeds="heldout"; explicit held-out seeds are refused."""
    table = {lv["id"]: lv for lv in sim.get_levels()}
    ids = []
    for name in levels:
        if name == "all":
            ids += list(table)
        elif name == "levels":
            ids += list(REAL_LEVELS)
        elif name == "practice":
            ids += [k for k, lv in table.items() if lv.get("practice")]
        elif name in table:
            ids.append(name)
        else:
            raise ValueError(f"unknown level {name!r}; known: {', '.join(table)}, or all / levels / practice")
    for c in controllers:
        if c not in sim.get_controllers():
            raise ValueError(f"unknown controller {c!r}")
    jobs = []
    heldout = {x for lid in table for x in sim.get_seeds(lid)["heldout"]}  # any level's: practice runs reuse level seeds
    for lid in dict.fromkeys(ids):
        s = sim.get_seeds(lid)
        section = "practice" if table[lid].get("practice") else "training"
        if seeds == "heldout":
            chosen, section = s["heldout"], "heldout"
        elif seeds == "training":
            chosen = s["training"]
        else:
            chosen = [int(x) for x in str(seeds).split(",")]
            bad = [x for x in chosen if x in heldout]
            if bad:
                raise ValueError(f"seed(s) {bad} are held-out grading seeds: run them only with --seeds heldout")
        jobs += [(c, lid, seed, section) for seed in chosen for c in controllers]
    return jobs


def _init(db_dir):
    os.environ["EVENTS_DB"] = os.path.join(db_dir, f"eval-{os.getpid()}.db")  # never the live dashboard's store


def run_one(job):
    """Run one (controller, level, seed, section) to the end of the day; returns the Referee's verdict, fails, score."""
    global _SIM
    controller, level, seed, section = job
    if _SIM is None:
        _SIM = simif.connect()  # one adapter (graph parsed once) per process
    t0 = time.monotonic()
    runner = Runner(_SIM)
    runner.start(level, controller, seed)
    runner.run_headless()
    ref, lv = _SIM.metrics()["referee"], _SIM.get_level()
    return {"controller": controller, "human_impl": _SIM.human_impl, "level": level, "level_name": lv["name"],
            "seed": lv["seed"], "section": section, "verdict": ref["verdict"], "fails": ref["fails"], "score": ref["score"],
            "sim_s": round(_SIM.t, 1), "wall_s": round(time.monotonic() - t0, 1)}


def run_jobs(jobs, workers):
    with tempfile.TemporaryDirectory() as d, mp.get_context("spawn").Pool(workers, _init, (d,)) as pool:
        return pool.map(run_one, jobs, chunksize=1)


def totals(runs):
    by_rule = Counter()
    for r in runs:
        by_rule.update({k: v for k, v in r["score"]["fails_by_rule"].items() if v})
    n = len(runs)
    s = lambda k: sum(r["score"].get(k) or 0 for r in runs)  # noqa: E731
    return {"runs": n, "passed": sum(r["verdict"] == "PASS" for r in runs),
            "pass_rate": round(sum(r["verdict"] == "PASS" for r in runs) / n, 3) if n else None,
            "fails": s("fails"), "fails_by_rule": dict(by_rule),
            "avg_delay_s": round(s("avg_delay_s") / n, 1) if n else None, "go_arounds": s("go_arounds"),
            "diversions": s("diversions"), "readback_errors_caught": s("readback_errors_caught"),
            "readback_errors_missed": s("readback_errors_accepted"), "completed": s("completed")}


def summary(results):
    """The compact Agent vs Human comparison the dashboard shows: totals per section and controller."""
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "human_impl": next((r["human_impl"] for r in results if r["human_impl"]), None), "sections": {}}
    for sec in SECTIONS:
        runs = [r for r in results if r["section"] == sec]
        if runs:
            out["sections"][sec] = {c: totals([r for r in runs if r["controller"] == c])
                                    for c in ("human", "agent") if any(r["controller"] == c for r in runs)}
    return out


def _rules(d):
    return " ".join(f"{k}:{v}" for k, v in sorted(d.items()) if v) or "-"


def report(results):
    """Readable text: per section a table of runs, the fails of each run, then totals per controller."""
    order = {lid: i for i, lid in enumerate(dict.fromkeys(r["level"] for r in results))}  # plan order
    impl = next((r["human_impl"] for r in results if r["human_impl"]), None)
    lines = [f"Human controller = sim controller {impl!r}" + (" (stand-in until the sim's Human lands)" if impl == "tower" else "")
             if impl else "Human not run",
             "Columns: done = flights completed; avg/tot dly = Referee delay (s) over completed flights only; GA = go-arounds;"
             " div = diversions; rb c/m = wrong readbacks caught/missed; wall = seconds of wall time"]
    head = f"{'level':<22}{'seed':>6}  {'ctrl':<6}{'verdict':<8}{'fails':>6}  {'done':>4}{'avg dly':>8}{'tot dly':>8}" \
           f"{'GA':>4}{'div':>4}{'rb c/m':>7}{'wall':>6}  fails by rule"
    for sec, title in SECTIONS.items():
        runs = sorted((r for r in results if r["section"] == sec),
                      key=lambda r: (order.get(r["level"], 99), r["seed"], r["controller"] != "human"))
        lines += ["", f"== {title} ==" + ("" if runs else "  (not run)")]
        if not runs:
            continue
        lines += [head, "-" * len(head)]
        for r in runs:
            sc = r["score"]
            lines.append(f"{r['level']:<22}{r['seed']:>6}  {r['controller']:<6}{r['verdict']:<8}{sc['fails']:>6}  "
                         f"{sc['completed']:>4}{sc['avg_delay_s']:>8.0f}{sc['total_delay_s']:>8.0f}{sc['go_arounds']:>4}"
                         f"{sc['diversions']:>4}{sc['readback_errors_caught']:>4}/{sc['readback_errors_accepted']:<2}"
                         f"{r['wall_s']:>6.0f}  {_rules(sc['fails_by_rule'])}")
        lines.append("")
        for r in runs:
            if r["fails"]:
                lines.append(f"  fails {r['level']} seed {r['seed']} {r['controller']}:")
                for f in r["fails"][:FAILS_SHOWN]:
                    lines.append(f"    t={f['t']:>7.1f}  {f['rule']:<18} {','.join(f['callsigns']):<18} {f['reason']}")
                if len(r["fails"]) > FAILS_SHOWN:
                    lines.append(f"    ... {len(r['fails']) - FAILS_SHOWN} more in the JSON file")
        lines.append(f"  totals ({title.split(' (')[0].lower()}):")
        for c in ("human", "agent"):
            cr = [r for r in runs if r["controller"] == c]
            if cr:
                t = totals(cr)
                lines.append(f"    {c:<6} pass {t['passed']}/{t['runs']} ({t['pass_rate']:.0%})  fails {t['fails']}  "
                             f"avg delay {t['avg_delay_s']:.0f} s  go-arounds {t['go_arounds']}  diversions {t['diversions']}  "
                             f"readback errors caught/missed {t['readback_errors_caught']}/{t['readback_errors_missed']}  "
                             f"by rule {_rules(t['fails_by_rule'])}")
    return "\n".join(lines)


def publish(summ):
    """Merge the summary into eval_results/summary.json (sections not run this time are kept; new dashboard runs start
    with it) and add it to the dashboard's current run as a `metrics` event."""
    if SUMMARY.exists():
        summ = dict(summ, sections=dict(json.loads(SUMMARY.read_text())["sections"], **summ["sections"]))
    SUMMARY.write_text(json.dumps(summ, indent=1) + "\n")
    run_id = db.current_run_id()
    if run_id is not None:
        db.emit(run_id, "metrics", 0, data={"comparison": summ}, reason="Latest Agent vs Human evaluation.")
    return run_id


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--controllers", nargs="+", default=["human", "agent"])
    ap.add_argument("--levels", nargs="+", default=["all"], help="level ids, or all / levels / practice")
    ap.add_argument("--seeds", default="training", help="training (default), heldout, or a list like 1,2,3")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--out", default=str(RESULTS / "latest.json"))
    ap.add_argument("--publish", action="store_true", help="write eval_results/summary.json and a metrics event")
    a = ap.parse_args(argv)
    try:
        jobs = plan(simif.connect(), a.controllers, a.levels, a.seeds)
    except ValueError as e:
        sys.exit(f"error: {e}")
    if not jobs:
        sys.exit("error: nothing to run (practice scenarios have no held-out seeds)")
    if a.seeds == "heldout":
        print("NOTE: running the HELD-OUT grading seeds. These are for final grading only: do not tune on these results.")
    t0 = time.monotonic()
    results = run_jobs(jobs, max(1, min(a.workers, len(jobs) or 1)))
    wall = time.monotonic() - t0
    print(report(results))
    print(f"\n{len(results)} runs in {wall:.0f} s wall ({a.workers} workers)")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    summ = summary(results)
    with open(a.out, "w") as f:
        json.dump({"summary": summ, "wall_s": round(wall, 1), "runs": results}, f, indent=1)
    print(f"results: {a.out}")
    if a.publish:
        run_id = publish(summ)
        print(f"published: {SUMMARY}" + (f" + metrics event in dashboard run {run_id}" if run_id else " (no dashboard run yet)"))


if __name__ == "__main__":
    main()

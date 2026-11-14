#!/usr/bin/env python3
"""Summarize BTS On-Time Performance monthly CSVs for PHX.

Usage: python3 -I kphx_traffic_bts_phx.py <input.csv> <output_dir> [haboob_date YYYY-MM-DD]

Reads the (untrusted) BTS CSV with the csv module only. Writes slim PHX-only
rows and summary tables to <output_dir>. Local times as reported by BTS
(hhmm, local airport time).
"""
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

AP = "PHX"
KEEP = [
    "FlightDate", "Reporting_Airline", "Tail_Number", "Flight_Number_Reporting_Airline",
    "Origin", "Dest", "CRSDepTime", "DepTime", "WheelsOff", "WheelsOn",
    "CRSArrTime", "ArrTime", "TaxiOut", "TaxiIn", "DepDelay", "ArrDelay",
    "Cancelled", "Diverted", "Distance",
]


def hour_of(hhmm):
    try:
        v = int(float(hhmm))
        return (v // 100) % 24
    except (TypeError, ValueError):
        return None


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def stats(vals):
    if not vals:
        return ("", "", "", "")
    return (len(vals), round(statistics.mean(vals), 2), round(statistics.median(vals), 2), pct(vals, 90))


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    haboob = sys.argv[3] if len(sys.argv) > 3 else None
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    with open(src, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["Origin"] == AP or r["Dest"] == AP:
                rows.append({k: r.get(k, "") for k in KEEP})

    with open(out / "phx_flights_slim.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=KEEP)
        w.writeheader()
        w.writerows(rows)

    days = sorted({r["FlightDate"] for r in rows})
    ndays = len(days)
    dep = [r for r in rows if r["Origin"] == AP]
    arr = [r for r in rows if r["Dest"] == AP]

    # hourly scheduled counts (avg per day) and taxi stats
    sched_dep = defaultdict(int)
    sched_arr = defaultdict(int)
    to_by_h = defaultdict(list)
    ti_by_h = defaultdict(list)
    for r in dep:
        h = hour_of(r["CRSDepTime"])
        if h is not None:
            sched_dep[h] += 1
        t = num(r["TaxiOut"])
        wh = hour_of(r["WheelsOff"]) if r["WheelsOff"] else h
        if t is not None and wh is not None:
            to_by_h[wh].append(t)
    for r in arr:
        h = hour_of(r["CRSArrTime"])
        if h is not None:
            sched_arr[h] += 1
        t = num(r["TaxiIn"])
        wh = hour_of(r["WheelsOn"]) if r["WheelsOn"] else h
        if t is not None and wh is not None:
            ti_by_h[wh].append(t)

    with open(out / "phx_hourly_summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["local_hour", "sched_dep_per_day", "sched_arr_per_day",
                    "taxiout_n", "taxiout_mean", "taxiout_median", "taxiout_p90",
                    "taxiin_n", "taxiin_mean", "taxiin_median", "taxiin_p90"])
        for h in range(24):
            w.writerow([h, round(sched_dep[h] / ndays, 2), round(sched_arr[h] / ndays, 2),
                        *stats(to_by_h[h]), *stats(ti_by_h[h])])

    allto = [x for v in to_by_h.values() for x in v]
    allti = [x for v in ti_by_h.values() for x in v]
    canc = sum(1 for r in rows if r["Cancelled"] in ("1", "1.00"))
    div = sum(1 for r in rows if r["Diverted"] in ("1", "1.00"))
    carriers = defaultdict(int)
    for r in rows:
        carriers[r["Reporting_Airline"]] += 1

    lines = []
    lines.append(f"file: {src.name}")
    lines.append(f"days: {ndays} ({days[0]}..{days[-1]})  PHX flights: {len(rows)} (dep {len(dep)}, arr {len(arr)})")
    lines.append(f"avg per day: dep {len(dep)/ndays:.0f}, arr {len(arr)/ndays:.0f}")
    lines.append(f"taxi-out (min) n,mean,median,p90: {stats(allto)}")
    lines.append(f"taxi-in  (min) n,mean,median,p90: {stats(allti)}")
    lines.append(f"cancelled: {canc}  diverted: {div}")
    lines.append("carriers: " + ", ".join(f"{k}={v}" for k, v in sorted(carriers.items(), key=lambda kv: -kv[1])))
    top_dep = sorted(sched_dep.items(), key=lambda kv: -kv[1])[:3]
    lines.append("busiest sched-dep hours (local): " + ", ".join(f"{h:02d}h={c/ndays:.1f}/day" for h, c in top_dep))

    if haboob:
        hd = [r for r in rows if r["FlightDate"] == haboob]
        hdep = [r for r in hd if r["Origin"] == AP]
        harr = [r for r in hd if r["Dest"] == AP]
        hto = [x for x in (num(r["TaxiOut"]) for r in hdep) if x is not None]
        hti = [x for x in (num(r["TaxiIn"]) for r in harr) if x is not None]
        hc = sum(1 for r in hd if r["Cancelled"] in ("1", "1.00"))
        hdv = sum(1 for r in hd if r["Diverted"] in ("1", "1.00"))
        lines.append(f"\nhaboob date {haboob}: PHX flights {len(hd)} (dep {len(hdep)}, arr {len(harr)}), cancelled {hc}, diverted {hdv}")
        lines.append(f"  taxi-out n,mean,median,p90: {stats(hto)}")
        lines.append(f"  taxi-in  n,mean,median,p90: {stats(hti)}")
        with open(out / f"phx_haboob_{haboob}_by_hour.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["local_hour_wheels", "dep_n", "taxiout_mean", "taxiout_p90", "arr_n", "taxiin_mean", "taxiin_p90",
                        "cancelled_sched_hour", "diverted_sched_hour"])
            to_h, ti_h = defaultdict(list), defaultdict(list)
            cc, dd = defaultdict(int), defaultdict(int)
            for r in hdep:
                t = num(r["TaxiOut"]); h = hour_of(r["WheelsOff"])
                if t is not None and h is not None:
                    to_h[h].append(t)
            for r in harr:
                t = num(r["TaxiIn"]); h = hour_of(r["WheelsOn"])
                if t is not None and h is not None:
                    ti_h[h].append(t)
            for r in hd:
                h = hour_of(r["CRSDepTime"] if r["Origin"] == AP else r["CRSArrTime"])
                if h is None:
                    continue
                if r["Cancelled"] in ("1", "1.00"):
                    cc[h] += 1
                if r["Diverted"] in ("1", "1.00"):
                    dd[h] += 1
            for h in range(24):
                a, b = stats(to_h[h]), stats(ti_h[h])
                w.writerow([h, a[0], a[1], a[3], b[0], b[1], b[3], cc[h], dd[h]])

    text = "\n".join(lines)
    (out / "phx_summary.txt").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()

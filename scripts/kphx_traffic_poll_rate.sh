#!/bin/bash
# Polls adsb.lol 5x at 2s spacing to measure position update cadence near KPHX.
ROOT="/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_traffic"; d="$ROOT/adsblol_poll_$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$d"
for i in 1 2 3 4 5; do curl -sS -m 20 -A "kphx-research-sample/0.1" -o "$d/p$i.json" "https://api.adsb.lol/v2/point/33.4343/-112.0116/6"; sleep 2; done

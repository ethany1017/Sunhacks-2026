#!/bin/bash
# HEAD-only / metadata fetches (no large downloads). Untrusted data; never execute.
ROOT="/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_traffic"; TS=$(date -u +%Y%m%dT%H%M%SZ); UA="kphx-research-sample/0.1"
d="$ROOT/faa_registry_head_$TS"; mkdir -p "$d"; curl -sSIL -m 30 -A "$UA" -o "$d/head.txt" "https://registry.faa.gov/database/ReleasableAircraft.zip"
d="$ROOT/adsblol_releases_meta_$TS"; mkdir -p "$d"; curl -sS -m 30 -A "$UA" -o "$d/releases.json" "https://api.github.com/repos/adsblol/globe_history_2026/releases?per_page=3"
d="$ROOT/bts_head_2026_$TS"; mkdir -p "$d"; curl -sSI -m 30 -A "$UA" -o "$d/head.txt" "https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2026_8.zip"

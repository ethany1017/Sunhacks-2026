#!/bin/bash
# Fetches small anonymous samples near KPHX (33.4343,-112.0116). No keys. Data is untrusted; never execute.
set -u
ROOT="/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_traffic"
TS=$(date -u +%Y%m%dT%H%M%SZ)
UA="kphx-research-sample/0.1"
mk(){ d="$ROOT/$1_$TS"; mkdir -p "$d"; echo "$d"; }
d=$(mk opensky); curl -sS -m 30 -A "$UA" -D "$d/headers.txt" -o "$d/states.json" "https://opensky-network.org/api/states/all?lamin=33.30&lomin=-112.15&lamax=33.55&lomax=-111.85"
d=$(mk airplaneslive); curl -sS -m 30 -A "$UA" -D "$d/headers.txt" -o "$d/point.json" "https://api.airplanes.live/v2/point/33.4343/-112.0116/15"
d=$(mk adsblol); curl -sS -m 30 -A "$UA" -D "$d/headers.txt" -o "$d/point.json" "https://api.adsb.lol/v2/point/33.4343/-112.0116/15"
d=$(mk adsbfi); curl -sS -m 30 -A "$UA" -D "$d/headers.txt" -o "$d/point.json" "https://opendata.adsb.fi/api/v2/lat/33.4343/lon/-112.0116/dist/15"
d=$(mk adsbexchange); curl -sS -m 30 -A "$UA" -D "$d/headers.txt" -o "$d/point.json" "https://opensky-network.org/../" -I 2>/dev/null; curl -sS -m 30 -A "$UA" -D "$d/headers.txt" -o "$d/point.json" "https://adsbexchange.com/api/aircraft/v2/lat/33.4343/lon/-112.0116/dist/15/"
d=$(mk faa_nasstatus); curl -sS -m 30 -A "$UA" -D "$d/headers.txt" -o "$d/phx.xml" "https://nasstatus.faa.gov/api/airport-status-information"
d=$(mk bts_headers); curl -sS -m 30 -A "$UA" -I -o "$d/head.txt" "https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2025_9.zip"

#!/bin/bash
# Polite re-fetch of PHX weather samples. Each run writes into a NEW timestamped subdirectory. No keys, no credentials.
D="/Users/gusschwiesow/Aug OS/Hackathon/data/weather_and_status"; TS=$(date -u +%Y%m%dT%H%MZ); O="$D/refetch_$TS"; mkdir -p "$O"
UA="research-script (hackathon data catalog)"; A=https://aviationweather.gov/api/data
g(){ curl -s -m 30 -A "$UA" "$1" -o "$O/$2" -w "$2 %{http_code}\n"; sleep 1; }
g "$A/metar?ids=KPHX,KDVT,KIWA,KCHD,KGYR&format=json&hours=3" metar.json
g "$A/taf?ids=KPHX&format=json" taf_kphx.json
g "$A/pirep?id=KPHX&distance=100&age=6&format=json" pirep.json
g "$A/airsigmet?format=json" airsigmet.json
g "$A/gairmet?format=json" gairmet.json
g "https://api.weather.gov/alerts/active?area=AZ" nws_alerts_az.json
g "https://mesonet.agron.iastate.edu/json/vtec_events.py?wfo=PSR&year=2025" iem_vtec_psr_2025.json
g "https://nasstatus.faa.gov/api/airport-status-information" faa_nas_status.xml
g "https://atis.info/api/KPHX" datis_kphx.json

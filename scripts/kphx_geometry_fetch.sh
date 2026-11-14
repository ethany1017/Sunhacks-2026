#!/usr/bin/env bash
# Fetch KPHX surface-geometry samples from free public endpoints. No keys/accounts.
# Each dataset goes into its own NEW subdirectory (suffix = UTC timestamp). Files are data only; never executed.
# Usage: kphx_geometry_fetch.sh [cycle, default 2611]   (d-TPP cycle YYCC; 2611 = 2026-10-29..2026-11-26)
set -euo pipefail
CYCLE="${1:-2611}"
ROOT="/Users/gusschwiesow/Aug OS/Hackathon/data/kphx_geometry"
UA="kphx-geometry-research/0.1"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BBOX_S=33.40; BBOX_W=-112.05; BBOX_N=33.46; BBOX_E=-111.97
mk(){ d="$ROOT/$1_$TS"; mkdir -p "$d"; echo "$d"; }

# 1. OSM Overpass: all aeroway features in bbox (ODbL)
d=$(mk osm_overpass_aeroway)
printf '[out:json][timeout:90];\n(\n  nwr["aeroway"](%s,%s,%s,%s);\n);\nout geom;\n' $BBOX_S $BBOX_W $BBOX_N $BBOX_E > "$d/query.overpassql"
curl -sf -m 120 -A "$UA" --data-urlencode "data@$d/query.overpassql" https://overpass-api.de/api/interpreter -o "$d/kphx_aeroway.json"; sleep 5

# 2. FAA ArcGIS Hub feature services (runways, taxiways, aprons), bbox query, GeoJSON
S=https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services
for L in Runways AM_Taxiway AM_Apron; do
  d=$(mk faa_arcgis_$L)
  curl -sf -m 60 -A "$UA" -G "$S/$L/FeatureServer/0/query" --data-urlencode "where=1=1" --data-urlencode "outFields=*" \
    --data-urlencode "geometry=$BBOX_W,$BBOX_S,$BBOX_E,$BBOX_N" --data-urlencode "geometryType=esriGeometryEnvelope" \
    --data-urlencode "inSR=4326" --data-urlencode "outSR=4326" --data-urlencode "f=geojson" -o "$d/${L}_phx_bbox.geojson"; sleep 2
done

# 3. FAA d-TPP: airport diagram + hot spot list (PDF) and cycle metafile (XML, ~16 MB; PHX record extracted)
d=$(mk faa_airport_diagram); curl -sf -m 60 -A "$UA" -L "https://aeronav.faa.gov/d-tpp/$CYCLE/00322AD.PDF" -o "$d/00322AD_$CYCLE.pdf"; sleep 2
d=$(mk faa_hotspot_sw);      curl -sf -m 60 -A "$UA" -L "https://www.faa.gov/aero_docs/dtpp/$CYCLE/SW4HOTSPOT.PDF" -o "$d/SW4HOTSPOT_$CYCLE.pdf"; sleep 2
d=$(mk faa_dtpp_cycle_xml);  curl -sf -m 120 -A "$UA" -L "https://aeronav.faa.gov/d-tpp/$CYCLE/xml_data/d-TPP_Metafile.xml" -o "$d/meta.xml"
python3 -I - "$d" <<'PY'
import sys,os
d=sys.argv[1]; t=open(d+'/meta.xml',encoding='utf-8-sig').read()
i=t.find('apt_ident="PHX"'); s=t.rfind('<airport_name',0,i); e=t.find('</airport_name>',i)+15
open(d+'/phx_excerpt.xml','w').write(t[:t.find('<state_code')]+t[s:e]); os.remove(d+'/meta.xml')
PY
echo "done $TS"

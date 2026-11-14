#!/bin/sh
# Downloads two small sample files as untrusted data into their own dirs. Never executes them.
D="/Users/gusschwiesow/Aug OS/Hackathon/data/build_logistics"
curl -sS -A "sunhacks-research/0.1" -o "$D/openfreemap_liberty_style/liberty.json" https://tiles.openfreemap.org/styles/liberty
# KPHX approx lat 33.4343 lon -112.0116 ; z16 tile x=12326? computed below in python
python3 -I - <<'PY'
import math
lat,lon,z=33.4343,-112.0116,16
n=2**z
x=int((lon+180)/360*n)
y=int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)
print(z,y,x)
open('/private/tmp/claude-501/kphx_tile.txt','w').write(f"{z}/{y}/{x}")
PY
T=$(cat /private/tmp/claude-501/kphx_tile.txt)
curl -sS -A "sunhacks-research/0.1" -o "$D/usgs_naip_tile_kphx/tile.jpg" -w "%{http_code} %{content_type} %{size_download}\n" "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/$T"

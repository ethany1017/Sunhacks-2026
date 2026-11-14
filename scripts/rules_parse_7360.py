#!/usr/bin/env python3
"""Parse text extracted from FAA JO 7360.1K (by rules_pdf_to_text.py) into a CSV of designator rows.
Usage: rules_parse_7360.py order.txt out.csv
Only the first (designator-sorted) table is parsed; first occurrence of each designator wins."""
import re,sys,csv
rx=re.compile(r"^([A-Z0-9]{2,4}\*?) (Fixed-wing|Helicopter|Gyroplane|Gyrocopter|Tiltrotor|Powered-lift|Airship|Balloon|Glider|Ground|[A-Za-z-]+) (\d[A-Z]/[A-Z+]+|\d[A-Z]/[A-Z]) (Super|Heavy|Medium|Light) ([A-I]) (III|II|I)\s*(\d+|)\s*(.*)$")
seen={}; lines=open(sys.argv[1]).read().splitlines()
start=next(i for i,l in enumerate(lines) if l.startswith("A002 "))
for i,l in enumerate(lines[start:],start):
    if "A-320 Prestige" in l and l.strip().startswith("A-320 Prestige") : break
    m=rx.match(l.strip())
    if m:
        d=m.group(1).rstrip("*")
        if d not in seen: seen[d]=[d,m.group(2),m.group(3),m.group(4),m.group(5),m.group(6),m.group(7),m.group(8).strip()]
w=csv.writer(open(sys.argv[2],"w",newline=""))
w.writerow(["icao_type","aircraft_class","faa_engine_count_type_weightclass","icao_wtc_word","cwt_category_A_to_I","srs_category","lahso_group","manufacturer_model_raw"])
w.writerows(seen.values()); print(len(seen),"rows")

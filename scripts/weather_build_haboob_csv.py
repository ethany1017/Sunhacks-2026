#!/usr/bin/env python3
"""Build clean haboob event CSVs from IEM ASOS raw CSVs (treated as untrusted data; parsed only as text).
usage: weather_build_haboob_csv.py in.csv out.csv start_utc end_utc  (e.g. "2025-08-25 23:00" "2025-08-26 03:30")"""
import csv, re, sys
inp, outp, t0, t1 = sys.argv[1:5]
def f(x):
    try: return float(x)
    except: return ""
FRAC=lambda s: sum(float(p) if '/' not in p else int(p.split('/')[0])/int(p.split('/')[1]) for p in s.split())
rows=[]
for r in csv.DictReader(open(inp)):
    if not (t0 <= r['valid'] <= t1): continue
    m=r['metar']
    rvr=re.findall(r'R\d\d[LRC]?/[0-9VP]+(?:V[0-9P]+)?FT', m)
    ceil=""; cig=[]
    for i in (1,2,3):
        c,l=r['skyc%d'%i], r['skyl%d'%i]
        if c in('BKN','OVC','VV') and l!='M': cig.append(float(l))
    if cig: ceil=min(cig)
    wx=r['wxcodes'] if r['wxcodes']!='M' else ''
    kind='SPECI/METAR' if 'AUTO' not in m else 'AUTO-5min'
    rows.append(dict(utc=r['valid'],kind=kind,temp_f=f(r['tmpf']),wind_dir=f(r['drct']),wind_kt=f(r['sknt']),gust_kt=f(r['gust']),
      peak_gust_kt=f(r['peak_wind_gust']),vis_sm=f(r['vsby']),wx=wx,ceiling_ft=ceil,rvr=';'.join(rvr),altim_inhg=f(r['alti']),
      dust_flag=int(any(k in wx for k in ('DS','SS','DU'))),ts_flag=int('TS' in wx),metar=m))
rows.sort(key=lambda d:d['utc'])
w=csv.DictWriter(open(outp,'w',newline=''),fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
print(len(rows),'rows ->',outp)

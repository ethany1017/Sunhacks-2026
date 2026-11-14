#!/usr/bin/env python3
"""Merge downloaded reference data into one normalized CSV of KPHX-relevant aircraft types.
Reads (data only, never executes): FAA ACD xlsx, FAA 7360.1K parsed csv, ICAO Doc 8643 json, OpenAP yml/wrap.
Run with the venv python that has openpyxl+pyyaml (python -I). Usage: rules_build_kphx_aircraft_csv.py"""
import csv,json,os,re,warnings
warnings.filterwarnings("ignore")
import openpyxl
B="/Users/gusschwiesow/Aug OS/Hackathon/data/reference_and_rules/"
OUT=B+"derived_kphx_aircraft_normalized/"; os.makedirs(OUT,exist_ok=True)
TYPES="A319 A320 A321 A20N A21N B737 B738 B739 B38M B39M B752 B763 A332 A333 A359 B772 B77W B788 B789 E75L E75S E170 E145 CRJ2 CRJ7 CRJ9 DH8D MD82 C172 C182 SR22 PC12 C208 BE20 C25A C56X C550 C680 P28A".split()
ws=openpyxl.load_workbook(B+"faa_aircraft_characteristics_database/ACD_data.xlsx",read_only=True)["ACD_Data"]
rows=list(ws.iter_rows(values_only=True)); H=list(rows[0][:41]); acd={r[0]:dict(zip(H,r[:41])) for r in rows[1:] if r[0]}
f73={r["icao_type"]:r for r in csv.DictReader(open(B+"derived_faa_7360_1K_designators/faa_7360_1K_types.csv"))}
icao={}
for r in json.load(open(B+"icao_doc8643_json/types.json")): icao.setdefault(r["Designator"],r)
def yml(p):
    d={}
    if not os.path.exists(p): return d
    sec=None
    for l in open(p):
        if not l.strip() or l.lstrip().startswith("#"): continue
        m=re.match(r"^(\w[\w/]*):\s*(.*)$",l)
        if m:
            if m.group(2)=="" : sec=m.group(1)
            else: d[m.group(1)]=m.group(2); sec=None
        else:
            m=re.match(r"^\s+([\w/ ]+):\s*(.*)$",l)
            if m and sec: d[sec+"."+m.group(1)]=m.group(2)
    return d
def wrap(p):
    d={}
    if not os.path.exists(p): return d
    for l in open(p).read().splitlines()[1:]:
        parts=re.split(r"\s{2,}",l.strip())
        if len(parts)>=5: d[parts[0]]=parts
    return d
cols=["icao_type","icao_model_full_name","icao_desc_code","icao_wtc_letter","faa_cwt_A_to_I","faa_srs","faa_lahso_group","recat15_A_to_F","recat2_apxA","recat2_apxB","faa_weight_class","aac_approach_category","aac_min","aac_max","adg","tdg","approach_speed_kt_acd_vref_proxy","wingspan_ft_no_winglets","wingspan_ft_with_winglets","length_ft","tail_height_ft","main_gear_width_ft","mtow_lb_acd","malw_lb_acd","openap_mtow_kg","openap_wing_span_m","openap_fuselage_len_m","openap_vmo_kt","openap_to_dist_opt_km","openap_to_dist_min_km","openap_to_dist_max_km","openap_to_liftoff_v_opt","openap_ld_touchdown_v_opt","openap_ld_brake_dist_opt_km","openap_ld_brake_dist_max_km","in_acd","in_7360_1K","in_icao","in_openap"]
out=[]
for t in TYPES:
    a=acd.get(t,{}); f=f73.get(t,{}); i=icao.get(t,{}); y=yml(B+f"openap_aircraft_yml/{t.lower()}.yml"); w=wrap(B+f"openap_wrap_txt/{t.lower()}.txt")
    g=lambda k,c:(w[k][c] if k in w and len(w[k])>c else "")
    out.append([t,i.get("ModelFullName",""),i.get("Description",""),i.get("WTC",""),f.get("cwt_category_A_to_I",a.get("CWT","")),f.get("srs_category",a.get("SRS","")),f.get("lahso_group",a.get("LAHSO","")),a.get("One_Half_Wake_Category",""),a.get("Two_Wake_Category_Appx_A",""),a.get("Two_Wake_Category_Appx_B",""),a.get("FAA_Weight",""),a.get("AAC",""),a.get("AAC_minimum",""),a.get("AAC_maximum",""),a.get("ADG",""),a.get("TDG",""),a.get("Approach_Speed_knot",""),a.get("Wingspan_ft_without_winglets_sharklets",""),a.get("Wingspan_ft_with_winglets_sharklets",""),a.get("Length_ft",""),a.get("Tail_Height_at_OEW_ft",""),a.get("Main_Gear_Width_ft",""),a.get("MTOW_lb",""),a.get("MALW_lb",""),y.get("mtow",""),y.get("wing.span",""),y.get("fuselage.length",""),y.get("vmo",""),g("to_d_tof",3),g("to_d_tof",4),g("to_d_tof",5),g("to_v_lof",3),g("ld_v_app",3),g("ld_d_brk",3),g("ld_d_brk",5),bool(a),bool(f),bool(i),bool(y)])
csv.writer(open(OUT+"kphx_aircraft_normalized.csv","w",newline="")).writerows([cols]+out)
print(len(out),"rows; missing acd:",[r[0] for r in out if not r[-4]], "missing 7360:",[r[0] for r in out if not r[-3]], "missing icao:",[r[0] for r in out if not r[-2]])

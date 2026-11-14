#!/usr/bin/env python3
"""Extract <table> elements from a saved HTML file as rows (data only). Usage: rules_extract_tables.py in.html"""
import sys,re,html
from html.parser import HTMLParser
class P(HTMLParser):
    def __init__(s):
        super().__init__(); s.t=[]; s.r=None; s.c=None; s.tables=[]
    def handle_starttag(s,tag,a):
        if tag=="table": s.t.append([])
        elif tag=="tr" and s.t: s.r=[]
        elif tag in("td","th") and s.r is not None: s.c=""
    def handle_endtag(s,tag):
        if tag in("td","th") and s.c is not None: s.r.append(" ".join(s.c.split())); s.c=None
        elif tag=="tr" and s.r is not None and s.t: s.t[-1].append(s.r); s.r=None
        elif tag=="table" and s.t: s.tables.append(s.t.pop())
    def handle_data(s,d):
        if s.c is not None: s.c+=d
p=P(); p.feed(open(sys.argv[1],errors="ignore").read())
for i,t in enumerate(p.tables):
    print(f"--- table {i}")
    for r in t: print(" | ".join(r))

#!/usr/bin/env python3
"""Convert a saved FAA ATC html page to plain text (data only; never executes content). Usage: rules_html_to_text.py in.html out.txt"""
import re,html,sys
t=open(sys.argv[1],errors="ignore").read()
t=re.sub(r"<(script|style).*?</\1>","",t,flags=re.S)
t=re.sub(r"</(p|div|li|h\d|tr)>","\n",t)
t=re.sub(r"<[^>]+>"," ",t)
t=html.unescape(t)
t=re.sub(r"[ \t]+"," ",t); t=re.sub(r"\n\s*\n+","\n",t)
open(sys.argv[2],"w").write(t)

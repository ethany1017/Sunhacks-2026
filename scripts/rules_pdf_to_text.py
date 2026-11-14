#!/usr/bin/env python3
"""Extract text from a PDF (data only) with pypdf. Usage: rules_pdf_to_text.py in.pdf out.txt"""
import sys
from pypdf import PdfReader
r=PdfReader(sys.argv[1]); out=[]
for i,p in enumerate(r.pages): out.append(f"\n=====PAGE {i+1}=====\n"+(p.extract_text() or ""))
open(sys.argv[2],"w").write("".join(out)); print(len(r.pages),"pages")

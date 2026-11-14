import sys,re,zlib
# crude PDF text dump (reads only; never executes content)
d=open(sys.argv[1],'rb').read()
for m in re.finditer(rb'stream\r?\n(.*?)\r?\nendstream',d,re.S):
    try: s=zlib.decompress(m.group(1))
    except Exception: continue
    if b'Tj' not in s and b'TJ' not in s: continue
    out=[]
    for line in s.split(b'\n'):
        parts=re.findall(rb'\((.*?)(?<!\\)\)',line) if (line.endswith(b'TJ') or line.endswith(b'Tj')) else []
        if parts: out.append(b''.join(parts).decode('latin1'))
        elif line.endswith(b'ET') or line.endswith(b' Td') or line.endswith(b'TD') : out.append('|')
    print(' '.join(out))

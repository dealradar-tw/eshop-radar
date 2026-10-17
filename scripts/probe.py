import re,urllib.request,urllib.error
UA={"User-Agent":"Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"}
def get(u):
    try:
        with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=40) as x: return x.read().decode("utf-8","replace")
    except Exception as e: print("ERR",u,e); return ""
pages=["https://ec.nintendo.com/","https://ec.nintendo.com/tw/zh","https://ec.nintendo.com/tw/zh/search","https://ec.nintendo.com/tw/zh/sale","https://ec.nintendo.com/tw/zh/titles/70010000000025","https://ec.nintendo.com/TW/zh/titles/70010000000025","https://ec.nintendo.com/HK/zh/","https://ec.nintendo.com/JP/ja/"]
chunks=set()
for p in pages:
    h=get(p); print("PAGE",p,len(h))
    for m in re.findall(r'(/_next/static/[^"\']+\.js)',h): chunks.add(m)
    for m in re.findall(r'href="(/[^"#]*)"',h)[:40]: print("  link",m)
print("chunks",len(chunks))
kw=re.compile(r'(?i)(popular|ranking|sortBy|sortOrder|orderBy|SALES_RANK|searchResult|search\w*Items?\b)')
seen=set()
for c in sorted(chunks):
    t=get("https://ec.nintendo.com"+c)
    hits=[(m.start(),m.group(0)) for m in kw.finditer(t)]
    if hits:
        print("CHUNK",c,len(t),len(hits))
        for pos,_ in hits[:6]:
            s=t[max(0,pos-150):pos+250].replace("\n"," ")
            if s not in seen: seen.add(s); print("   ...",s)

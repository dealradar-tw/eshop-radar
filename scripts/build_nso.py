#!/usr/bin/env python3
"""scripts/nso_src.txt -> docs/nso.json（手工維護，不自動抓取）"""
import json, re, datetime
SRC="scripts/nso_src.txt"
BASE={"FC","SFC","GB"}
SITE="https://www.nintendo.com/tw/nintendo-switch-online/nintendo-classics/index.html"
def norm(s): return re.sub(r"[^a-z0-9]","",s.lower().replace("é","e"))
en=json.load(open("docs/ennames.json"))["n"]
idx={}
for i,n in en.items(): idx.setdefault(norm(n),[]).append(i)
nm={}
for l in open("scripts/nso_names.txt",encoding="utf-8"):
    l=l.strip()
    if l and not l.startswith("#"):
        e,z,j=l.split("|"); nm[e]=(z,j)
games=[]
for ln in open(SRC,encoding="utf-8"):
    ln=ln.strip()
    if not ln or ln.startswith("#"): continue
    con,date,titles=ln.split("|",2)
    for t in titles.split(";"):
        reg="b"
        if t.endswith("~"): reg="w"; t=t[:-1]
        elif t.endswith("*"): reg="j"; t=t[:-1]
        tw = "?" if con=="VB" else ("n" if reg=="w" else "y")
        g={"c":con,"n":t,"d":date,"p":"b" if con in BASE else "e","tw":tw}
        if t in nm:
            if nm[t][0]: g["z"]=nm[t][0]
            if nm[t][1]: g["j"]=nm[t][1]
        games.append(g)
games.sort(key=lambda g:(g["d"],g["c"],g["n"]),reverse=True)
out={"asof":str(datetime.date.today()),"src":SITE,"games":games}
json.dump(out,open("docs/nso.json","w",encoding="utf-8"),ensure_ascii=False,separators=(",",":"))
from collections import Counter
print("zh",sum(1 for g in games if "z" in g),"ja",sum(1 for g in games if "j" in g),"unmatched",[k for k in nm if k not in {g["n"] for g in games}]);print(len(games),Counter(g["c"] for g in games),sum(1 for g in games if "e" in g))

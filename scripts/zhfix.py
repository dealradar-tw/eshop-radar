"""一次性：把 docs/zhnames.json 裡來自維基數據的簡體中文名稱轉成台灣用字。"""
import json, os, sys
from opencc import OpenCC
c = OpenCC("s2twp")
p = os.path.join(os.path.dirname(__file__), "..", "docs", "zhnames.json")
d = json.load(open(p, encoding="utf-8"))
wd = d.get("wd") or {}
n = d.get("n") or {}
changed = 0
for k, v in list(wd.items()):
    nv = c.convert(v)
    if nv != v:
        changed += 1
        print(v, "->", nv)
    if n.get(k) == v:
        n[k] = nv
    wd[k] = nv
d["wd"], d["n"] = wd, n
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print("changed", changed, "of", len(wd))

#!/usr/bin/env python3
"""偵測任天堂網頁商店的資料介面（只讀取公開頁面，結果寫到 probe/out/）。"""
import json, os, re, urllib.request, urllib.error
from urllib.parse import urljoin

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "zh-TW,zh;q=0.9"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)
log = []

def get(url, data=None, headers=None):
    h = dict(UA); h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:2000]
    except Exception as e:
        return -1, str(e)

PAGE = "https://ec.nintendo.com/HK/zh/titles/70010000035483/aocs"
st, html = get(PAGE)
log.append(f"aocs page {st} len={len(html)}")
open(os.path.join(OUT, "aocs.html"), "w", encoding="utf-8").write(html)
scripts = sorted(set(urljoin(PAGE, s) for s in re.findall(r'<script[^>]+src="([^"]+)"', html)))
log.append(f"scripts {len(scripts)}")
hits = {}
for s in scripts:
    st2, js = get(s)
    for pat in [r'graphql', r'persistedQuery', r'sha256Hash', r'operationName', r'https?://[a-z0-9.\-]+nintendo[a-z0-9.\-]*[^"\'`\s]{0,80}',
                r'dlcItems', r'aocs', r'offsetInfo', r'/api/[^"\'`\s]{1,80}']:
        for m in re.finditer(pat, js):
            snip = js[max(0, m.start() - 160): m.end() + 200].replace("\n", " ")
            hits.setdefault(pat, [])
            if len(hits[pat]) < 25:
                hits[pat].append(f"[{s.rsplit('/',1)[-1][:30]}] {snip}")
json.dump(hits, open(os.path.join(OUT, "js_hits.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log.append("hits: " + ", ".join(f"{k}={len(v)}" for k, v in hits.items()))
# payload around dlc in aocs html
pay = ""
for chunk in re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html, re.S):
    try: pay += json.loads('"' + chunk + '"')
    except Exception: pass
open(os.path.join(OUT, "aocs_payload.txt"), "w", encoding="utf-8").write(pay)
log.append(f"payload len={len(pay)} nsUid7005={len(re.findall(r'7005\d{10}', pay))}")
# TW title page payload (TW-only game) to see if server data exists
for name, url in [("tw_title", "https://ec.nintendo.com/TW/zh/titles/70010000102139"),
                  ("hk_dlc", "https://ec.nintendo.com/HK/zh/titles/70050000030683")]:
    st3, h3 = get(url)
    p3 = ""
    for chunk in re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', h3, re.S):
        try: p3 += json.loads('"' + chunk + '"')
        except Exception: pass
    open(os.path.join(OUT, name + "_payload.txt"), "w", encoding="utf-8").write(p3)
    log.append(f"{name} {st3} html={len(h3)} payload={len(p3)} formalName={'formalName' in p3}")
open(os.path.join(OUT, "log.txt"), "w", encoding="utf-8").write("\n".join(log))
print("\n".join(log))

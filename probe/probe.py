#!/usr/bin/env python3
"""測試台灣區與日本區遊戲的對應方式（只讀取公開資料），結果寫到 probe/out/jp3.txt。"""
import json, os, time, urllib.request, urllib.error
URL = "https://wb.lp1.savanna.srv.nintendo.net/graphql"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"content-type": "application/json", "accept": "application/json",
     "X-Nintendo-Savanna-Client-Id": "042e4bd1f0eec144167dbc0c63f1d17876e7b9ec322713b613614214e3675df9",
     "User-Agent": UA, "Origin": "https://ec.nintendo.com", "Referer": "https://ec.nintendo.com/"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "probe", "out", "jp3.txt")
out = []


def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def gql(label, body):
    q = ("query Q($shopId: Int!, $country: CountryCode!, $language: LanguageCode!) "
         "@inContext(shopId: $shopId, country: $country, language: $language) { item { %s } }" % body)
    try:
        t = http(URL, json.dumps({"query": q, "variables": {"shopId": 3, "country": "TW", "language": "zh"}}).encode(), H)
        out.append(f"## {label} OK {t[:600]}")
    except urllib.error.HTTPError as e:
        out.append(f"## {label} HTTP {e.code}")


# 1. 台灣區是否有商品代碼欄位
for f in ["productCode", "initialCode", "itemCode", "romCode", "code", "productCodes", "applications { id initialCode }",
          "applications { id productCode }", "applications { id name }", "hacProductCode", "beeProductCode"]:
    gql("TW field " + f, f"applicationItem(nsUid: 70010000027622) {{ nsUid {f} }}")

# 2. 日本區完整遊戲清單，比對封面圖檔編號
S = "https://search.nintendo.jp/nintendo_soft/search.json?"
jp = {}
for hard, sforms in (("1_HAC", ["HAC_DL", "HAC_DOWNLOADABLE"]), ("05_BEE", [None])):
    for sf in sforms:
        page = 1
        while True:
            q = f"opt_hard={hard}&limit=300&page={page}" + (f"&opt_sform={sf}" if sf else "")
            try:
                r = json.loads(http(S + q))["result"]
            except Exception as e:
                out.append(f"## {q} ERR {e}"); break
            for it in r["items"]:
                if str(it.get("nsuid", "")).startswith("7001"):
                    jp[it["nsuid"]] = {k: it.get(k) for k in ("title", "icode", "iurl", "hard", "price", "current_price", "sprice", "sedate", "ssitu")}
            out.append(f"## {q} total={r['total']} got={len(r['items'])}")
            if page * 300 >= r["total"] or not r["items"]:
                break
            page += 1
            time.sleep(0.5)
out.append(f"## JP games: {len(jp)}")
d = json.load(open(os.path.join(ROOT, "docs", "data.json"), encoding="utf-8"))
F = d["fields"]
tw = {r[F.index("img")]: (r[F.index("id")], r[F.index("name")]) for r in d["rows"] if r[F.index("img")]}
byimg = {}
for ns, v in jp.items():
    if v.get("iurl"):
        byimg.setdefault(v["iurl"], []).append(ns)
hits = [(tw[h], byimg[h]) for h in tw if h in byimg]
out.append(f"## TW with img {len(tw)}; JP with iurl {len(byimg)}; img matches {len(hits)}")
for (tid, name), ns in hits[:15]:
    out.append(f"  {tid} {name} -> {ns} {jp[ns[0]]['title']}")
# 3. 同編號對應（台灣與日本是否偶爾共用 nsuid）
same = [r[F.index("id")] for r in d["rows"] if ("70010000" + r[F.index("id")]) in jp]
out.append(f"## same nsuid: {len(same)} e.g. {same[:5]}")
json.dump(jp, open(os.path.join(ROOT, "probe", "out", "jp_games.json"), "w", encoding="utf-8"), ensure_ascii=False)
open(OUT, "w", encoding="utf-8").write("\n".join(out))
print("\n".join(o[:300] for o in out))

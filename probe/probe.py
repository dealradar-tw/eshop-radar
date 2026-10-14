#!/usr/bin/env python3
"""測試日本區資料來源（只讀取公開資料），結果寫到 probe/out/jp.txt。"""
import json, os, urllib.request, urllib.error, urllib.parse
URL = "https://wb.lp1.savanna.srv.nintendo.net/graphql"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"content-type": "application/json", "accept": "application/json",
     "X-Nintendo-Savanna-Client-Id": "042e4bd1f0eec144167dbc0c63f1d17876e7b9ec322713b613614214e3675df9",
     "User-Agent": UA, "Origin": "https://ec.nintendo.com", "Referer": "https://ec.nintendo.com/"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "jp2.txt")
out = []


def http(label, url, data=None, headers=None, n=3000):
    try:
        req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode("utf-8", "replace")
            out.append(f"## {label} HTTP {r.status}\n{txt[:n]}\n")
            return txt
    except urllib.error.HTTPError as e:
        out.append(f"## {label} HTTP {e.code}\n{e.read().decode('utf-8', 'replace')[:2000]}\n")
    except Exception as e:
        out.append(f"## {label} ERR {e}\n")
    return None


def gql(label, body, country="JP", lang="ja", n=3000):
    q = ("query Q($shopId: Int!, $country: CountryCode!, $language: LanguageCode!) "
         "@inContext(shopId: $shopId, country: $country, language: $language) { item { %s } }" % body)
    data = json.dumps({"query": q, "variables": {"shopId": 3, "country": country, "language": lang}}).encode()
    return http(label, URL, data, H, n)


S = "https://search.nintendo.jp/nintendo_soft/search.json?"
js = http("JP games 300", S + "opt_hard=1_HAC&opt_sform=HAC_DL&limit=300&page=1", n=300)
if js:
    r = json.loads(js)["result"]
    items = r["items"]
    out.append(f"## total={r['total']} got={len(items)}")
    for it in items[:8]:
        out.append(json.dumps({k: it.get(k) for k in ("nsuid", "title", "icode", "apps", "hard", "sform", "price", "current_price", "sprice", "sedate", "ssitu", "cnsuid")}, ensure_ascii=False))
    out.append("## with apps: %d / %d" % (sum(1 for it in items if it.get("apps")), len(items)))
for q in ["opt_hard=05_BEE&limit=3&page=1", "fq=hard:05_BEE&limit=3&page=1", "opt_hard=1_HAC&opt_sform=HAC_DOWNLOADABLE&limit=3&page=1"]:
    js = http("JP " + q, S + q, n=200)
    if js:
        for it in json.loads(js)["result"]["items"][:3]:
            out.append(json.dumps({k: it.get(k) for k in ("nsuid", "title", "icode", "apps", "hard", "sform")}, ensure_ascii=False))
os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="utf-8").write("\n".join(out))
print("\n".join(o[:400] for o in out))

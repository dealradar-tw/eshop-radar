#!/usr/bin/env python3
"""暫時測試：台灣區商店有沒有「體驗版」欄位（只讀取公開資料）。"""
import json, os, urllib.request, urllib.error
URL = "https://wb.lp1.savanna.srv.nintendo.net/graphql"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"content-type": "application/json", "accept": "application/json",
     "X-Nintendo-Savanna-Client-Id": "042e4bd1f0eec144167dbc0c63f1d17876e7b9ec322713b613614214e3675df9",
     "User-Agent": UA, "Origin": "https://ec.nintendo.com", "Referer": "https://ec.nintendo.com/"}
out = []
def q(label, body):
    query = ("query Q($shopId: Int!, $country: CountryCode!, $language: LanguageCode!) "
             "@inContext(shopId: $shopId, country: $country, language: $language) { item { %s } }" % body)
    try:
        req = urllib.request.Request(URL, json.dumps({"query": query, "variables": {"shopId": 3, "country": "TW", "language": "zh"}}).encode(), H)
        with urllib.request.urlopen(req, timeout=30) as r:
            out.append(f"## {label} OK {r.read().decode()[:700]}")
    except urllib.error.HTTPError as e:
        out.append(f"## {label} HTTP {e.code}")
# 70010000027622 動物森友會（無體驗版）；70010000039245 Octopath II? 用幾款已知有體驗版的大作
ids = [70010000027622, 70010000035483, 70010000041428, 70010000034469]
for f in ["demo { nsUid }", "demos { nsUid }", "demoItem { nsUid }", "demoItems { nsUid }", "hasDemo", "isDemoAvailable",
          "demoNsUid", "trialItem { nsUid }", "relatedItems { nsUid }", "demo { __typename }", "demos { __typename }",
          "demoApplicationItem { nsUid }", "demoApplication { id }", "applications { id demo }", "applications { id isDemo }",
          "isDemo", "demoUrl", "contentType", "labels { name }", "features { name }", "tagItems { name }"]:
    q("TW " + f, " ".join(f"a{i}: applicationItem(nsUid: {n}) {{ nsUid formalName {f} }}" for i, n in enumerate(ids)))
# 日本官方搜尋有沒有體驗版欄位
try:
    req = urllib.request.Request("https://search.nintendo.jp/nintendo_soft/search.json?opt_hard=1_HAC&opt_sform=HAC_DL&limit=300&page=1", headers={"User-Agent": UA})
    items = json.loads(urllib.request.urlopen(req, timeout=40).read())["result"]["items"]
    keys = sorted({k for it in items for k, v in it.items() if v not in (None, "", [], ["0"], "0", 0)})
    out.append("## JP keys " + ", ".join(keys))
    for k in ("cont", "demo", "tr", "trisdate", "tristat", "nso", "pmode"):
        out.append(f"## JP {k}: " + json.dumps(sorted({json.dumps(it.get(k), ensure_ascii=False) for it in items})[:15], ensure_ascii=False))
except Exception as e:
    out.append(f"## JP ERR {e}")
os.makedirs(os.path.join(os.path.dirname(__file__), "out"), exist_ok=True)
open(os.path.join(os.path.dirname(__file__), "out", "demo.txt"), "w", encoding="utf-8").write("\n".join(out))
print("\n".join(o[:300] for o in out))

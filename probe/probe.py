#!/usr/bin/env python3
"""測試日本區 GraphQL 能否查到遊戲的 applicationId（只讀取公開資料），結果寫到 probe/out/jp4.txt。"""
import json, os, urllib.request, urllib.error
URL = "https://wb.lp1.savanna.srv.nintendo.net/graphql"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"content-type": "application/json", "accept": "application/json",
     "X-Nintendo-Savanna-Client-Id": "042e4bd1f0eec144167dbc0c63f1d17876e7b9ec322713b613614214e3675df9",
     "User-Agent": UA, "Origin": "https://ec.nintendo.com", "Referer": "https://ec.nintendo.com/"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = []
# 70010000022675 = 日本區 ブック・オブ・デーモンズ；70010000000965 = 日本區 Minecraft
for shop in (1, 2, 3, 4, 5, 6):
    for country, lang in (("JP", "ja"), ("JP", "en")):
        q = ("query Q($shopId: Int!, $country: CountryCode!, $language: LanguageCode!) "
             "@inContext(shopId: $shopId, country: $country, language: $language) { item { "
             "a: applicationItem(nsUid: 70010000022675) { nsUid formalName applications { id } } "
             "b: applicationItem(nsUid: 70010000000965) { nsUid formalName applications { id } } } }")
        try:
            req = urllib.request.Request(URL, json.dumps({"query": q, "variables": {"shopId": shop, "country": country, "language": lang}}).encode(), H)
            with urllib.request.urlopen(req, timeout=30) as r:
                out.append(f"## shop={shop} {country}/{lang} OK {r.read().decode()[:400]}")
        except urllib.error.HTTPError as e:
            out.append(f"## shop={shop} {country}/{lang} HTTP {e.code} {e.read().decode('utf-8','replace')[:200]}")
        except Exception as e:
            out.append(f"## shop={shop} {country}/{lang} ERR {e}")
# 日本區商品頁（ec.nintendo.com/JP/ja/titles/...）是否有 applicationId
for u in ["https://ec.nintendo.com/JP/ja/titles/70010000022675", "https://store-jp.nintendo.com/item/software/D70010000022675"]:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            t = r.read().decode("utf-8", "replace")
            import re
            out.append(f"## {u} {r.status} len={len(t)} appIds={sorted(set(re.findall(r'01[0-9a-fA-F]{14}', t)))[:5]}")
    except urllib.error.HTTPError as e:
        out.append(f"## {u} HTTP {e.code}")
    except Exception as e:
        out.append(f"## {u} ERR {e}")
open(os.path.join(ROOT, "probe", "out", "jp4.txt"), "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out))

#!/usr/bin/env python3
"""測試日本區資料來源（只讀取公開資料），結果寫到 probe/out/jp.txt。"""
import json, os, urllib.request, urllib.error, urllib.parse
URL = "https://wb.lp1.savanna.srv.nintendo.net/graphql"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"content-type": "application/json", "accept": "application/json",
     "X-Nintendo-Savanna-Client-Id": "042e4bd1f0eec144167dbc0c63f1d17876e7b9ec322713b613614214e3675df9",
     "User-Agent": UA, "Origin": "https://ec.nintendo.com", "Referer": "https://ec.nintendo.com/"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "jp.txt")
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


# 1. GraphQL 結構查詢（可能被關閉）
http("introspection", URL, json.dumps({"query": "{ __schema { queryType { name fields { name args { name } } } } }"}).encode(), H)
http("introspection item", URL, json.dumps({"query": '{ __type(name: "ItemQuery") { name fields { name args { name type { name kind ofType { name } } } } } }'}).encode(), H)
# 2. 猜測用 applicationId 查商品的欄位，錯誤訊息可能提示正確名稱
for f in ['applicationItemByApplicationId(applicationId: "0100F2C0115B6000") { nsUid formalName }',
          'applicationItems(applicationIds: ["0100F2C0115B6000"]) { nsUid formalName }',
          'search(query: "zelda") { nsUid }',
          'applicationItem(nsUid: 70010000027622) { nsUid formalName applications { id } }']:
    gql("JP " + f[:50], f)
# 3. 日本官方搜尋
js = http("JP search", "https://search.nintendo.jp/nintendo_soft/search.json?opt_hard=1_HAC&limit=3&page=1", n=6000)
if js:
    try:
        d = json.loads(js)
        r = d.get("result", d)
        out.append("## JP search keys " + json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in r.items()}, ensure_ascii=False)[:800])
        items = r.get("items", [])
        if items:
            out.append("## JP item0 " + json.dumps(items[0], ensure_ascii=False)[:3000])
            ns = [str(i.get("nsuid")) for i in items if i.get("nsuid")]
            if ns:
                http("JP price", "https://api.ec.nintendo.com/v1/price?country=JP&lang=ja&ids=" + ",".join(ns))
                gql("JP app by jp nsuid", f"applicationItem(nsUid: {ns[0]}) {{ nsUid formalName applications {{ id }} }}")
    except Exception as e:
        out.append(f"## parse ERR {e}")
http("JP search switch2", "https://search.nintendo.jp/nintendo_soft/search.json?opt_hard=1_BEE&limit=1&page=1", n=1500)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="utf-8").write("\n".join(out))
print("\n".join(o[:400] for o in out))

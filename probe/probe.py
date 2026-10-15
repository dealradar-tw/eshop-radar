#!/usr/bin/env python3
"""暫時測試：台灣區商店的影片欄位格式（只讀取公開資料）。"""
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
            t = r.read().decode(); out.append(f"## {label} OK {t[:1500]}"); return t
    except urllib.error.HTTPError as e:
        out.append(f"## {label} HTTP {e.code} {e.read().decode('utf-8','replace')[:300]}")
ids = [70010000038615, 70010000039948, 70010000041270, 70010000042927, 70010000038620]
MV = "movies { __typename ... on YoutubeMovie { youtubeId } ... on EshopMovie { movieUrl thumbnailUrl } }"
for cc in ("TW", "HK"):
    body = " ".join(f"a{i}: applicationItem(nsUid: {n}) {{ nsUid {MV} }}" for i, n in enumerate(ids))
    query = ("query Q($shopId: Int!, $country: CountryCode!, $language: LanguageCode!) "
             "@inContext(shopId: $shopId, country: $country, language: $language) { item { %s } }" % body)
    try:
        req = urllib.request.Request(URL, json.dumps({"query": query, "variables": {"shopId": 3, "country": cc, "language": "zh"}}).encode(), H)
        out.append(f"## {cc} " + urllib.request.urlopen(req, timeout=30).read().decode()[:2500])
    except urllib.error.HTTPError as e:
        out.append(f"## {cc} HTTP {e.code} {e.read().decode('utf-8','replace')[:300]}")
for f in ["promotionMovies { __typename }", "trailers { __typename }", "movieUrls", "youtubeIds", "videos { __typename }", "mainMovie { __typename }"]:
    q("TW " + f, f"a0: applicationItem(nsUid: 70010000038615) {{ {f} }}")
# 港區商品頁原始內容裡的 YouTube 編號
try:
    req = urllib.request.Request("https://ec.nintendo.com/HK/zh/titles/70010000038615", headers={"User-Agent": UA})
    t = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    import re
    out.append("## HK page yt " + str(sorted(set(re.findall(r"youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=)([\w-]{11})|\\?\"youtubeId\\?\":\\?\"([\w-]{11})", t)))[:10]) + f" len={len(t)}")
    k = t.find("ovie"); out.append("## HK page movie ctx " + t[max(0,k-300):k+300])
except Exception as e:
    out.append(f"## HK page ERR {e}")
os.makedirs(os.path.join(os.path.dirname(__file__), "out"), exist_ok=True)
open(os.path.join(os.path.dirname(__file__), "out", "movies2.txt"), "w", encoding="utf-8").write("\n".join(out))

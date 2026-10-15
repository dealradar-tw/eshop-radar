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
ids = [70010000027622, 70010000035483, 70010000041428, 70010000034469, 70010000022954]
q("typenames", " ".join(f"a{i}: applicationItem(nsUid: {n}) {{ nsUid movies {{ __typename }} }}" for i, n in enumerate(ids)))
for tn in ["Movie", "EshopMovie", "NintendoMovie", "HostedMovie", "VideoMovie", "UrlMovie", "BrightcoveMovie", "EcMovie", "MovieItem"]:
    for f in ["url", "movieUrl", "thumbnailUrl", "hlsUrl", "mp4Url", "id"]:
        q(f"{tn}.{f}", f"a0: applicationItem(nsUid: 70010000027622) {{ movies {{ __typename ... on {tn} {{ {f} }} }} }}")
os.makedirs(os.path.join(os.path.dirname(__file__), "out"), exist_ok=True)
open(os.path.join(os.path.dirname(__file__), "out", "movies.txt"), "w", encoding="utf-8").write("\n".join(out))

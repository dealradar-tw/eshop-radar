#!/usr/bin/env python3
"""偵測任天堂網頁商店的資料介面：下載頁面腳本供分析（只讀取公開頁面）。"""
import os, re, urllib.request
from urllib.parse import urljoin
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "js")
os.makedirs(OUT, exist_ok=True)
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
        return r.read().decode("utf-8", "replace")
seen = set()
for page in ["https://ec.nintendo.com/HK/zh/titles/70010000035483/aocs", "https://ec.nintendo.com/HK/zh/titles/70010000035483",
             "https://ec.nintendo.com/TW/zh/titles/70010000102139"]:
    html = get(page)
    for s in re.findall(r'<script[^>]+src="([^"]+)"', html):
        u = urljoin(page, s)
        name = u.split("?")[0].rsplit("/", 1)[-1]
        if name in seen: continue
        seen.add(name)
        try:
            open(os.path.join(OUT, name), "w", encoding="utf-8").write(get(u))
        except Exception as e:
            print("fail", u, e)
print(len(seen), "scripts saved")

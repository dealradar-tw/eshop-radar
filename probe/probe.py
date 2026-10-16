import sys, json, os
sys.path.insert(0, "scripts")
import update as u
os.makedirs("probe/out", exist_ok=True)
out = {}
jp = json.load(open("data/jp.json")); cat = json.load(open("data/catalog.json"))["games"]
tw_apps = {g["app"].lower() for g in cat.values() if g.get("app")}
only = [n for n, a in jp["apps"].items() if a and a not in tw_apps]
only.sort(key=lambda n: -int(n))
sample = only[:3] + only[3000:3003]
res = u.fetch_apps(sample, u.JP_VARS)
out["jp_meta"] = {n: ({k: (f.get(k) if k != "description" else (f.get(k) or "")[:80]) for k in
        ("formalName", "labelPlatform", "heroBannerUrl", "genre", "languages", "releaseDateOnEshop", "screenshotUrls", "ratingInfo", "applications")} if f else None) for n, f in res.items()}
out["jp_titles"] = {n: jp["titles"].get(n) for n in sample}
out["jp_price"] = [{"id": p.get("title_id"), **u.parse_price(p)} for p in u.price_batch(sample, "JP")]
# 港區限定：在一段編號範圍內，台灣沒有但港區有
found = []
for lo in (70010000100000, 70010000115000, 70010000130000):
    ids = list(range(lo, lo + 2000))
    tw = set(); hk = {}
    for b in u.batches(ids):
        for p in u.price_batch(b):
            if u.parse_price(p)["status"] != "not_found": tw.add(str(p["title_id"]))
        for p in u.price_batch(b, "HK"):
            i = u.parse_price(p)
            if i["status"] != "not_found": hk[str(p["title_id"])] = i
    out[f"hk_scan_{lo}"] = {"tw": len(tw), "hk": len(hk), "hk_only": [(n, hk[n]) for n in hk if n not in tw][:8], "hk_only_n": len([n for n in hk if n not in tw])}
json.dump(out, open("probe/out/probe.json", "w"), ensure_ascii=False, indent=1)
print("done")

#!/usr/bin/env python3
"""
台區特價雷達：每日更新腳本（由 GitHub Actions 自動執行）

做的事：
  1. 重新查詢所有已知遊戲的台灣區價格
  2. 往後掃描新編號，找出新上架的遊戲（--full 時從頭到尾全部重掃）
  3. 新遊戲到港區商品頁抓名稱、平台、封面、介紹
  4. 記錄價格歷史（只在價格變動時新增一筆）
  5. 產生網站用的 docs/data.json 與 docs/d/<編號>.json
  6. （選用）收藏遊戲降價時發 Telegram 通知

只用 Python 標準函式庫。
"""
import argparse, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA, DOCS = os.path.join(ROOT, "data"), os.path.join(ROOT, "docs")
TW = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "zh-TW,zh;q=0.9"}
PRICE_API = "https://api.ec.nintendo.com/v1/price?country=TW&lang=zh&ids={ids}"
HK_PAGE = "https://ec.nintendo.com/HK/zh/titles/{id}"
BASE = 70010000000000
SELLING = ("onsale", "preorder", "unreleased")


def log(*a):
    print(*a, flush=True)


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def load(name, default):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return default


def save(path, obj, pretty=False):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if pretty:
            json.dump(obj, f, ensure_ascii=False, indent=0, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ───────────── 1+2. 價格 ─────────────
def price_batch(ids):
    for attempt in range(5):
        try:
            d = json.loads(get(PRICE_API.format(ids=",".join(map(str, ids)))))
            return d.get("prices", [])
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return []
            time.sleep(5 * (attempt + 1))
        except Exception:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"價格查詢連續失敗：{ids[0]}…")


def parse_price(p):
    reg = (p.get("regular_price") or {}).get("raw_value")
    disc = p.get("discount_price") or {}
    num = lambda v: int(float(v)) if v not in (None, "") else None
    return {"status": p.get("sales_status"), "reg": num(reg), "sale": num(disc.get("raw_value")),
            "end": disc.get("end_datetime", "") or ""}


def refresh_prices(games, full):
    known = sorted(int(i) for i in games)
    top = max(known) if known else BASE
    if full:
        scan = range(BASE, top + 6000)
    else:
        scan = range(max(BASE, top - 2000), top + 6000)  # 新遊戲的編號會比現有的大
    targets = sorted(set(known) | set(scan))
    log(f"查價：{len(targets)} 個編號（已知 {len(known)}）")
    seen, new = set(), []
    batches = [targets[i:i + 50] for i in range(0, len(targets), 50)]
    for n, b in enumerate(batches, 1):
        for p in price_batch(b):
            nsuid = str(p.get("title_id"))
            info = parse_price(p)
            if info["status"] == "not_found":
                continue
            seen.add(nsuid)
            g = games.get(nsuid)
            if g is None:
                g = games[nsuid] = {"name": "", "plat": "", "img": "", "meta": False, "hk404": False,
                                    "first_seen": TODAY}
                new.append(nsuid)
            g.update(info)
        if n % 20 == 0:
            log(f"  {n}/{len(batches)}")
        time.sleep(0.4)
    known_set = set(map(str, known))
    for nsuid, g in games.items():
        if nsuid not in seen and nsuid in known_set:
            g["status"] = "not_found"  # 已下架
    log(f"新發現 {len(new)} 個商品")
    return new


# ───────────── 3. 商品資料（港區頁面） ─────────────
PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', re.S)
LDJSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
MODES = {"TV_MODE": "TV 模式", "TABLETOP_MODE": "桌上模式", "HANDHELD_MODE": "手提模式"}


def grab_object(text, key):
    i = text.find(f'"{key}":{{')
    if i < 0:
        return None
    start = text.index("{", i)
    depth, in_str, esc = 0, False, False
    for j in range(start, len(text)):
        c = text[j]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_str = False
        elif c == '"': in_str = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:j + 1])
    return None


def s(x):
    return x.strip() if isinstance(x, str) else ""


def gb(r):
    v = ((r or {}).get("totalRomSize") or (r or {}).get("estimatedRomSize") or {}).get("rawValue")
    return f"{v / 1e9:.1f}GB" if isinstance(v, (int, float)) and v > 0 else ""


def build_detail(f, ld):
    pn = f.get("playerNumber") or {}

    def rng(a, b):
        a, b = pn.get(a), pn.get(b)
        if not b:
            return ""
        return f"{a}–{b} 人" if a and a != b else f"{b} 人"

    players = " · ".join(x for x in [
        rng("offlineMin", "offlineMax") and "單機 " + rng("offlineMin", "offlineMax"),
        rng("localMin", "localMax") and "本機通訊 " + rng("localMin", "localMax"),
        rng("onlineMin", "onlineMax") and "網路 " + rng("onlineMin", "onlineMax")] if x)
    langs = []
    for l in f.get("languages") or []:
        n = s(l.get("name"))
        if n and n not in langs:
            langs.append(n)
    rs = f.get("romSizes") or {}
    size = " / ".join(x for x in [gb(rs.get("bee")) and "Switch 2 " + gb(rs.get("bee")),
                                  gb(rs.get("hac")) and "Switch " + gb(rs.get("hac"))] if x)
    shots = [u for u in (f.get("screenshotUrls") or [])
             if isinstance(u, str) and re.fullmatch(r"https://img-eshop\.cdn\.nintendo\.net/i/[0-9a-f]+\.(jpg|png)", u)][:10]
    vids = [m.get("youtubeId") for m in (f.get("movies") or [])
            if isinstance(m, dict) and re.fullmatch(r"[\w-]{11}", str(m.get("youtubeId")))][:6]
    modes = [MODES[p["name"]] for p in (f.get("playStyles") or []) if isinstance(p, dict) and p.get("name") in MODES]
    rec = {"catch": s(f.get("catchCopy")), "desc": s(f.get("description"))[:3000],
           "publisher": s((f.get("publisher") or {}).get("name")), "release": s(f.get("releaseDateOnEshop")),
           "genres": [s(ld.get("genre"))] if s(ld.get("genre")) else [], "players": players, "langs": langs,
           "size": size, "modes": modes, "shots": shots, "vids": vids}
    return {k: v for k, v in rec.items() if v}


def fetch_meta(nsuid):
    try:
        html = get(HK_PAGE.format(id=nsuid))
    except urllib.error.HTTPError as e:
        return nsuid, ("404" if e.code == 404 else f"HTTP{e.code}"), None
    except Exception as e:
        return nsuid, type(e).__name__, None
    ld = {}
    m = LDJSON.search(html)
    if m:
        try: ld = json.loads(m.group(1))
        except Exception: pass
    payload = ""
    for chunk in PUSH.findall(html):
        try: payload += json.loads('"' + chunk + '"')
        except Exception: pass
    try:
        f = grab_object(payload, "serverFragment") or {}
    except Exception:
        f = {}
    name = s(f.get("formalName")) or s(ld.get("name"))
    if not name:
        return nsuid, "no-data" if len(html) > 20000 else f"short-page({len(html)})", None
    plat = f.get("labelPlatform") if f.get("labelPlatform") in ("HAC", "BEE") else (
        "BEE" if "Switch 2" in s(ld.get("gamePlatform")) else "HAC")
    img_url = s(ld.get("image")) or s(f.get("heroBannerUrl"))
    m2 = re.search(r"/i/([0-9a-f]{20,})\.jpg", img_url)
    return nsuid, "ok", {"name": name, "plat": plat, "img": m2.group(1) if m2 else "",
                         "detail": build_detail(f, ld)}


def refresh_meta(games, limit, retry_404):
    """慢慢抓，避免被港區網站當成機器人擋掉；連續失敗太多次就先停，下次再繼續。"""
    todo = [i for i, g in games.items()
            if not g.get("meta") and g.get("status") in SELLING and (retry_404 or not g.get("hk404"))]
    todo.sort(key=lambda i: (games[i].get("sale") is None, -int(i)))  # 特價中、較新的優先
    total = len(todo)
    todo = todo[:limit]
    log(f"抓商品資料：本次 {len(todo)} 筆（尚缺 {total} 筆）")
    os.makedirs(os.path.join(DOCS, "d"), exist_ok=True)
    stats, fails, ok = {}, 0, 0
    for n, nsuid in enumerate(todo, 1):
        nsuid, st, rec = fetch_meta(nsuid)
        stats[st] = stats.get(st, 0) + 1
        g = games[nsuid]
        if st == "ok":
            g.update(name=rec["name"], plat=rec["plat"], img=rec["img"] or g.get("img", ""),
                     meta=True, hk404=False)
            save(os.path.join(DOCS, "d", nsuid[-6:] + ".json"), rec["detail"])
            ok += 1
            fails = 0
        elif st == "404":
            g["hk404"] = True
            fails = 0
        else:
            fails += 1
            if fails >= 15:
                log(f"  連續 {fails} 次失敗（{st}），可能被暫時擋住，先停止，下次再繼續")
                break
            time.sleep(10)
        if n % 50 == 0:
            log(f"  {n}/{len(todo)} {stats}")
        time.sleep(1.5)
    log(f"  完成 {ok} 筆，結果統計：{stats}")


# ───────────── 4. 價格歷史 ─────────────
def effective(g):
    if g.get("status") not in ("onsale", "preorder") or g.get("reg") is None:
        return None
    return g["sale"] if g.get("sale") is not None else g["reg"]


def update_history(games, hist):
    drops = []
    for nsuid, g in games.items():
        p = effective(g)
        if p is None:
            continue
        h = hist.setdefault(nsuid, [])
        if not h or h[-1][1] != p:
            prev = h[-1][1] if h else None
            low_before = min(x[1] for x in h) if h else None
            h.append([TODAY, p])
            if prev is not None and p < prev:
                drops.append((nsuid, prev, p, low_before is not None and p <= low_before))
    return drops


# ───────────── 5. 網站資料 ─────────────
def build_site(games, hist, asof):
    rows = []
    for nsuid, g in games.items():
        if g.get("status") not in SELLING:
            continue
        h = hist.get(nsuid, [])
        prices = [x[1] for x in h]
        low = min(prices) if prices else None
        low_date = next((x[0] for x in reversed(h) if x[1] == low), "") if prices else ""
        rows.append([nsuid[-6:], g.get("name", ""), 1 if g.get("plat") == "BEE" else 0, g.get("img", ""),
                     g.get("reg"), g.get("sale"), (g.get("end") or "")[:10], low, low_date,
                     len(set(prices)), {"onsale": 0, "preorder": 1, "unreleased": 2}[g["status"]],
                     g.get("first_seen", ""), 1 if g.get("meta") else 0])
    rows.sort(key=lambda r: r[0])
    save(os.path.join(DOCS, "data.json"), {
        "asof": asof,
        "fields": ["id", "name", "s2", "img", "reg", "sale", "end", "low", "lowDate",
                   "nPrices", "status", "firstSeen", "hasDetail"],
        "rows": rows})
    log(f"網站資料：{len(rows)} 筆")


# ───────────── 6. Telegram 通知（選用） ─────────────
def notify(games, drops):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    wl_path = os.path.join(DATA, "wishlist.txt")
    if not (token and chat and os.path.exists(wl_path)):
        return
    with open(wl_path, encoding="utf-8") as f:
        text = "\n".join(l for l in f if not l.lstrip().startswith("#"))
        wl = {("70010000" + x[-6:]) for x in re.findall(r"\d{6,14}", text)}
    hits = [d for d in drops if d[0] in wl]
    if not hits:
        log("收藏清單沒有降價")
        return
    lines = ["🎮 收藏的遊戲降價了"]
    for nsuid, prev, now, is_low in hits:
        g = games[nsuid]
        lines.append(f"\n{g.get('name') or nsuid}\nNT${prev} → NT${now}"
                     + ("（史低）" if is_low else "")
                     + (f"，特價到 {g['end'][:10]}" if g.get("end") else ""))
    site = os.environ.get("SITE_URL", "")
    if site:
        lines.append(f"\n{site}")
    body = urllib.parse.urlencode({"chat_id": chat, "text": "\n".join(lines)}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=20)
        log(f"已發送 Telegram 通知：{len(hits)} 筆")
    except Exception as e:
        log(f"Telegram 發送失敗：{e}")


def main():
    global TODAY
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="從頭掃描全部編號，並重試港區查不到的商品")
    ap.add_argument("--meta-limit", type=int, default=700)
    args = ap.parse_args()
    now = datetime.now(TW)
    TODAY = now.strftime("%Y-%m-%d")

    cat = load("catalog.json", {"games": {}})
    games, hist = cat["games"], load("history.json", {})
    refresh_prices(games, args.full)
    refresh_meta(games, args.meta_limit, args.full)
    drops = update_history(games, hist)
    asof = now.strftime("%Y-%m-%d %H:%M")
    cat["asof"] = asof
    save(os.path.join(DATA, "catalog.json"), cat, pretty=True)
    save(os.path.join(DATA, "history.json"), hist)
    build_site(games, hist, asof)
    log(f"今日降價：{len(drops)} 筆")
    notify(games, drops)


TODAY = ""
if __name__ == "__main__":
    sys.exit(main())

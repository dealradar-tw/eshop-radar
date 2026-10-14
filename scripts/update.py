#!/usr/bin/env python3
"""
台區特價雷達：每日更新腳本（由 GitHub Actions 自動執行）

做的事：
  1. 重新查詢所有已知遊戲與 DLC 的台灣區價格（同時記錄港區價格，供跨區比價）
  2. 往後掃描新編號，找出新上架的遊戲（--full 時從頭到尾全部重掃）
  3. 到港區商品頁抓名稱、平台、封面、介紹、DLC 清單
  4. 記錄價格歷史（只在價格變動時新增一筆）
  5. 產生網站資料：docs/data.json、docs/dlc.json、docs/d/<編號>.json、docs/h/<編號>.json
  6. （選用）收藏遊戲降價時發 Telegram 通知

只用 Python 標準函式庫。
"""
import argparse, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA, DOCS = os.path.join(ROOT, "data"), os.path.join(ROOT, "docs")
TW = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "zh-TW,zh;q=0.9"}
PRICE_API = "https://api.ec.nintendo.com/v1/price?country={cc}&lang=zh&ids={ids}"
HK_PAGE = "https://ec.nintendo.com/HK/zh/titles/{id}"
BASE = 70010000000000
SELLING = ("onsale", "preorder", "unreleased")
DETAIL_VERSION = 2   # 詳細資料格式版本；舊版會在之後的排程中慢慢重抓


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


def dumps(obj, pretty=False):
    if pretty:
        return json.dumps(obj, ensure_ascii=False, indent=0, separators=(",", ":"))
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def save(path, obj, pretty=False):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(dumps(obj, pretty))
    os.replace(tmp, path)


def save_if_changed(path, obj):
    text = dumps(obj)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


# ───────────── 1+2. 價格 ─────────────
def price_batch(ids, cc="TW"):
    for attempt in range(5):
        try:
            d = json.loads(get(PRICE_API.format(cc=cc, ids=",".join(map(str, ids)))))
            return d.get("prices", [])
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return []
            time.sleep(5 * (attempt + 1))
        except Exception:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"價格查詢連續失敗：{cc} {ids[0]}…")


def parse_price(p):
    reg = (p.get("regular_price") or {}).get("raw_value")
    disc = p.get("discount_price") or {}
    num = lambda v: int(round(float(v))) if v not in (None, "") else None
    return {"status": p.get("sales_status"), "reg": num(reg), "sale": num(disc.get("raw_value")),
            "end": disc.get("end_datetime", "") or ""}


def batches(seq, n=50):
    seq = list(seq)
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def refresh_prices(games, full):
    known = sorted(int(i) for i in games)
    top = max(known) if known else BASE
    scan = range(BASE, top + 6000) if full else range(max(BASE, top - 2000), top + 6000)
    targets = sorted(set(known) | set(scan))
    log(f"查價（台）：{len(targets)} 個編號（已知遊戲 {len(known)}）")
    seen, new = set(), []
    bs = batches(targets)
    for n, b in enumerate(bs, 1):
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
            log(f"  {n}/{len(bs)}")
        time.sleep(0.4)
    known_set = set(map(str, known))
    for nsuid, g in games.items():
        if nsuid not in seen and nsuid in known_set:
            g["status"] = "not_found"  # 已下架
    log(f"新發現 {len(new)} 個商品")
    return new


def refresh_dlc_prices(dlcs):
    if not dlcs:
        return
    log(f"查價（DLC）：{len(dlcs)} 個")
    seen = set()
    for b in batches(sorted(dlcs)):
        for p in price_batch(b):
            nsuid = str(p.get("title_id"))
            if nsuid in dlcs:
                info = parse_price(p)
                dlcs[nsuid].update(info)
                seen.add(nsuid)
        time.sleep(0.4)
    for nsuid, d in dlcs.items():
        if nsuid not in seen:
            d["status"] = "not_found"


def refresh_hk_prices(items):
    """跨區比價用：查同一批編號在港區的價格（港幣）。"""
    ids = [i for i, g in items.items() if g.get("status") in SELLING]
    log(f"查價（港）：{len(ids)} 個")
    for b in batches(ids):
        try:
            for p in price_batch(b, "HK"):
                nsuid = str(p.get("title_id"))
                if nsuid in items:
                    info = parse_price(p)
                    items[nsuid]["hk"] = None if info["status"] == "not_found" else \
                        {"reg": info["reg"], "sale": info["sale"], "end": info["end"]}
        except RuntimeError as e:
            log(f"  港區查價失敗，略過：{e}")
            return
        time.sleep(0.4)


# ───────────── 3. 商品資料（港區頁面） ─────────────
PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', re.S)
LDJSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
MODES = {"TV_MODE": "TV 模式", "TABLETOP_MODE": "桌上模式", "HANDHELD_MODE": "手提模式"}
IMG_HASH = re.compile(r"/i/([0-9a-f]{20,})\.(?:jpg|png)")


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


def img_hash(url):
    m = IMG_HASH.search(s(url))
    return m.group(1) if m else ""


def page_payload(html):
    payload = ""
    for chunk in PUSH.findall(html):
        try: payload += json.loads('"' + chunk + '"')
        except Exception: pass
    return payload


def dlc_from_items(items):
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        nid = str(it.get("nsUid", ""))
        if not re.fullmatch(r"\d{14}", nid):
            continue
        out.append({"id": nid, "name": s(it.get("formalName")), "img": img_hash(it.get("heroBannerUrl")),
                    "date": s(it.get("releaseDateOnEshop")), "mem": bool(it.get("isMembershipRequired"))})
    return out


def dlc_from_aocs_page(nsuid):
    """DLC 很多時，商品頁只列前幾個；到「追加內容」頁抓完整清單。"""
    try:
        payload = page_payload(get(HK_PAGE.format(id=nsuid) + "/aocs"))
    except Exception:
        return []
    out, seen = [], set()
    for m in re.finditer(r'"nsUid":\s*"?(7005\d{10})', payload):
        nid = m.group(1)
        if nid in seen:
            continue
        seen.add(nid)
        chunk = payload[m.start(): m.start() + 2500]
        nxt = chunk.find('"nsUid"', 10)
        if nxt > 0:
            chunk = chunk[:nxt]
        name = re.search(r'"formalName":\s*"((?:[^"\\]|\\.)*)"', chunk)
        img = re.search(r'"heroBannerUrl":\s*"([^"]+)"', chunk)
        date = re.search(r'"releaseDateOnEshop":\s*"([^"]+)"', chunk)
        mem = re.search(r'"isMembershipRequired":\s*(true|false)', chunk)
        out.append({"id": nid, "name": json.loads('"' + name.group(1) + '"') if name else "",
                    "img": img_hash(img.group(1)) if img else "", "date": date.group(1) if date else "",
                    "mem": bool(mem and mem.group(1) == "true")})
    return out


def build_detail(f, ld, nsuid):
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
             if isinstance(u, str) and re.fullmatch(r"https://img-eshop\.cdn\.nintendo\.net/i/[0-9a-f]+\.(jpg|png)", u)][:12]
    vids = [m.get("youtubeId") for m in (f.get("movies") or [])
            if isinstance(m, dict) and re.fullmatch(r"[\w-]{11}", str(m.get("youtubeId")))][:8]
    modes = [MODES[p["name"]] for p in (f.get("playStyles") or []) if isinstance(p, dict) and p.get("name") in MODES]
    genres = [x.strip() for x in s(ld.get("genre")).split("/") if x.strip()]
    dlc_seg = f.get("dlcItems") or {}
    dlc = dlc_from_items(dlc_seg.get("items"))
    total = (dlc_seg.get("offsetInfo") or {}).get("total") or 0
    if total > len(dlc):
        more = dlc_from_aocs_page(nsuid)
        known = {d["id"] for d in dlc}
        dlc += [d for d in more if d["id"] not in known]
    apps = [s(a.get("id")) for a in (f.get("applications") or []) if isinstance(a, dict) and s(a.get("id"))]
    rec = {"v": DETAIL_VERSION,
           "catch": s(f.get("catchCopy")), "desc": s(f.get("description"))[:3000],
           "publisher": s((f.get("publisher") or {}).get("name")), "release": s(f.get("releaseDateOnEshop")),
           "genres": genres, "players": players, "langs": langs,
           "pmax": max([pn.get(k) or 0 for k in ("offlineMax", "localMax", "onlineMax")] or [0]),
           "poff": pn.get("offlineMax") or 0,
           "size": size, "modes": modes, "shots": shots, "vids": vids,
           "dlc": dlc, "dlcTotal": total, "titleId": apps[0] if apps else ""}
    return {k: v for k, v in rec.items() if v not in ("", [], None, 0) or k == "v"}


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
    try:
        f = grab_object(page_payload(html), "serverFragment") or {}
    except Exception:
        f = {}
    name = s(f.get("formalName")) or s(ld.get("name"))
    if not name:
        return nsuid, "no-data" if len(html) > 20000 else f"short-page({len(html)})", None
    plat = f.get("labelPlatform") if f.get("labelPlatform") in ("HAC", "BEE") else (
        "BEE" if "Switch 2" in s(ld.get("gamePlatform")) else "HAC")
    return nsuid, "ok", {"name": name, "plat": plat,
                         "img": img_hash(ld.get("image")) or img_hash(f.get("heroBannerUrl")),
                         "detail": build_detail(f, ld, nsuid)}


def refresh_meta(games, dlcs, limit, retry_404):
    """慢慢抓，避免被港區網站當成機器人擋掉；連續失敗太多次就先停，下次再繼續。"""
    selling = lambda g: g.get("status") in SELLING
    missing = [i for i, g in games.items()
               if selling(g) and not g.get("meta") and (retry_404 or not g.get("hk404"))]
    outdated = [i for i, g in games.items()
                if selling(g) and g.get("meta") and g.get("dv", 1) < DETAIL_VERSION]
    key = lambda i: (games[i].get("sale") is None, -int(i))  # 特價中、較新的優先
    todo = sorted(missing, key=key) + sorted(outdated, key=key)
    log(f"抓商品資料：本次最多 {limit} 筆（缺資料 {len(missing)}、需更新格式 {len(outdated)}）")
    todo = todo[:limit]
    os.makedirs(os.path.join(DOCS, "d"), exist_ok=True)
    stats, fails, ok = {}, 0, 0
    for n, nsuid in enumerate(todo, 1):
        nsuid, st, rec = fetch_meta(nsuid)
        stats[st] = stats.get(st, 0) + 1
        g = games[nsuid]
        if st == "ok":
            g.update(name=rec["name"], plat=rec["plat"], img=rec["img"] or g.get("img", ""),
                     meta=True, hk404=False, dv=DETAIL_VERSION)
            det = rec["detail"]
            save(os.path.join(DOCS, "d", nsuid[-6:] + ".json"), det)
            g["genres"] = det.get("genres", [])
            g["zh"] = (1 if any("繁體" in l for l in det.get("langs", [])) else 0) | \
                      (2 if any("簡體" in l for l in det.get("langs", [])) else 0)
            g["pmax"], g["poff"] = det.get("pmax", 0), det.get("poff", 0)
            g["dlc"] = [d["id"] for d in det.get("dlc", [])]
            for d in det.get("dlc", []):
                cur = dlcs.setdefault(d["id"], {"first_seen": TODAY})
                cur.update(parent=nsuid, name=d["name"], img=d["img"], mem=d["mem"], date=d["date"])
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


def update_history(items, hist):
    drops = []
    for nsuid, g in items.items():
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


def hk_effective(g):
    hk = g.get("hk")
    if not hk or hk.get("reg") is None:
        return None
    return hk["sale"] if hk.get("sale") is not None else hk["reg"]


def update_hk_history(items, hist):
    for nsuid, g in items.items():
        p = hk_effective(g)
        if p is None:
            continue
        h = hist.setdefault(nsuid, [])
        if not h or h[-1][1] != p:
            h.append([TODAY, p])


# ───────────── 5. 網站資料 ─────────────
def low_info(h):
    prices = [x[1] for x in h]
    if not prices:
        return None, "", 0
    low = min(prices)
    return low, next(x[0] for x in reversed(h) if x[1] == low), len(set(prices))


STATUS_CODE = {"onsale": 0, "preorder": 1, "unreleased": 2}


def backfill_filters(games):
    """舊版抓到的詳細資料沒存篩選欄位；先從已有的 d/<編號>.json 補上。"""
    for nsuid, g in games.items():
        if not g.get("meta") or "zh" in g:
            continue
        p = os.path.join(DOCS, "d", nsuid[-6:] + ".json")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            det = json.load(f)
        langs = det.get("langs", [])
        g["genres"] = [x.strip() for gg in det.get("genres", []) for x in gg.split("/") if x.strip()]
        g["zh"] = (1 if any("繁體" in l for l in langs) else 0) | (2 if any("簡體" in l for l in langs) else 0)
        nums = [int(n) for n in re.findall(r"(\d+) 人", det.get("players", ""))]
        g["pmax"] = max(nums) if nums else 0


def build_site(games, dlcs, hist, hist_hk, asof):
    backfill_filters(games)
    dlc_count = {}
    for d in dlcs.values():
        if d.get("status") in SELLING and d.get("parent"):
            dlc_count[d["parent"]] = dlc_count.get(d["parent"], 0) + 1
    rows = []
    for nsuid, g in games.items():
        if g.get("status") not in SELLING:
            continue
        low, low_date, n = low_info(hist.get(nsuid, []))
        hk = g.get("hk") or {}
        rows.append([nsuid[-6:], g.get("name", ""), 1 if g.get("plat") == "BEE" else 0, g.get("img", ""),
                     g.get("reg"), g.get("sale"), (g.get("end") or "")[:10], low, low_date,
                     n, STATUS_CODE[g["status"]], g.get("first_seen", ""), 1 if g.get("meta") else 0,
                     g.get("genres", []), g.get("zh", 0), g.get("pmax", 0), dlc_count.get(nsuid, 0),
                     hk.get("reg"), hk.get("sale")])
    rows.sort(key=lambda r: r[0])
    save(os.path.join(DOCS, "data.json"), {
        "asof": asof,
        "fields": ["id", "name", "s2", "img", "reg", "sale", "end", "low", "lowDate", "nPrices", "status",
                   "firstSeen", "hasDetail", "genres", "zh", "pmax", "dlcN", "hkReg", "hkSale"],
        "rows": rows})

    drows = []
    for nsuid, d in dlcs.items():
        if d.get("status") not in SELLING:
            continue
        low, low_date, n = low_info(hist.get(nsuid, []))
        hk = d.get("hk") or {}
        drows.append([nsuid, d.get("name", ""), d.get("parent", "")[-6:], d.get("img", ""),
                      d.get("reg"), d.get("sale"), (d.get("end") or "")[:10], low, low_date, n,
                      STATUS_CODE[d["status"]], d.get("first_seen", ""), 1 if d.get("mem") else 0,
                      hk.get("reg"), hk.get("sale")])
    drows.sort(key=lambda r: r[0])
    save(os.path.join(DOCS, "dlc.json"), {
        "asof": asof,
        "fields": ["id", "name", "parent", "img", "reg", "sale", "end", "low", "lowDate", "nPrices",
                   "status", "firstSeen", "membersOnly", "hkReg", "hkSale"],
        "rows": drows})

    # 每個商品一個價格歷史檔，給價格走勢圖用（只在有變動時寫入）
    hdir = os.path.join(DOCS, "h")
    os.makedirs(hdir, exist_ok=True)
    changed = 0
    for nsuid in set(hist) | set(hist_hk):
        changed += save_if_changed(os.path.join(hdir, nsuid + ".json"),
                                   {"tw": hist.get(nsuid, []), "hk": hist_hk.get(nsuid, [])})
    log(f"網站資料：遊戲 {len(rows)} 筆、DLC {len(drows)} 筆、價格歷史檔更新 {changed} 個")


# ───────────── 6. Telegram 通知（選用） ─────────────
def notify(items, drops):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    wl_path = os.path.join(DATA, "wishlist.txt")
    if not (token and chat and os.path.exists(wl_path)):
        return
    with open(wl_path, encoding="utf-8") as f:
        text = "\n".join(l for l in f if not l.lstrip().startswith("#"))
        wl = set(re.findall(r"\d{14}", text))
    hits = [d for d in drops if d[0] in wl]
    if not hits:
        log("收藏清單沒有降價")
        return
    lines = ["🎮 收藏的遊戲降價了"]
    for nsuid, prev, now, is_low in hits:
        g = items[nsuid]
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
    ap.add_argument("--meta-limit", type=int, default=800)
    args = ap.parse_args()
    now = datetime.now(TW)
    TODAY = now.strftime("%Y-%m-%d")

    cat = load("catalog.json", {"games": {}})
    games = cat["games"]
    dlcs = load("dlc.json", {})
    hist, hist_hk = load("history.json", {}), load("history_hk.json", {})

    refresh_prices(games, args.full)
    refresh_dlc_prices(dlcs)
    refresh_meta(games, dlcs, args.meta_limit, args.full)
    refresh_dlc_prices({k: v for k, v in dlcs.items() if "status" not in v})  # 這次新發現的 DLC
    refresh_hk_prices({**games, **dlcs})

    items = {**games, **dlcs}
    drops = update_history(items, hist)
    update_hk_history(items, hist_hk)

    asof = now.strftime("%Y-%m-%d %H:%M")
    cat["asof"] = asof
    save(os.path.join(DATA, "catalog.json"), cat, pretty=True)
    save(os.path.join(DATA, "dlc.json"), dlcs, pretty=True)
    save(os.path.join(DATA, "history.json"), hist)
    save(os.path.join(DATA, "history_hk.json"), hist_hk)
    build_site(games, dlcs, hist, hist_hk, asof)
    log(f"今日降價：{len(drops)} 筆")
    notify(items, drops)


TODAY = ""
if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
台區特價雷達：每日更新腳本（由 GitHub Actions 自動執行）

做的事：
  1. 重新查詢所有已知遊戲與 DLC 的台灣區價格（同時記錄港區價格，供跨區比價）
  2. 往後掃描新編號，找出新上架的遊戲（--full 時從頭到尾全部重掃）
  3. 從任天堂商店資料介面（台灣區）抓名稱、平台、封面、介紹、DLC 清單
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
BASE = 70010000000000
SELLING = ("onsale", "preorder", "unreleased")
DETAIL_VERSION = 3   # 詳細資料格式版本；舊版會在之後的排程中重抓


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
                g = games[nsuid] = {"name": "", "plat": "", "img": "", "meta": False,
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


# ───────────── 3. 商品資料（任天堂商店 GraphQL，台灣區） ─────────────
GQL_URL = "https://wb.lp1.savanna.srv.nintendo.net/graphql"
GQL_HEADERS = {"content-type": "application/json", "accept": "application/json",
               "X-Nintendo-Savanna-Client-Id": "042e4bd1f0eec144167dbc0c63f1d17876e7b9ec322713b613614214e3675df9",
               "Origin": "https://ec.nintendo.com", "Referer": "https://ec.nintendo.com/", **UA}
GQL_CTX = ("query Q($shopId: Int!, $country: CountryCode!, $language: LanguageCode!) "
           "@inContext(shopId: $shopId, country: $country, language: $language) { item { %s } }")
GQL_VARS = {"shopId": 3, "country": "TW", "language": "zh"}
APP_FIELDS = ("nsUid formalName labelPlatform heroBannerUrl releaseDateOnEshop catchCopy description genre "
              "publisher { name } screenshotUrls movies { __typename ... on YoutubeMovie { youtubeId } } "
              "languages { name } playerNumber { offlineMin offlineMax localMin localMax onlineMin onlineMax } "
              "playStyles { name } ratingInfo { rating { name age } } applications { id } "
              "romSizes { hac { totalRomSize { rawValue } estimatedRomSize { rawValue } } "
              "bee { totalRomSize { rawValue } estimatedRomSize { rawValue } } }")
DLC_FIELDS = ("items { __typename ... on DlcItem { nsUid formalName labelPlatform heroBannerUrl releaseDateOnEshop "
              "isMembershipRequired } } offsetInfo { total }")
MODES = {"TV_MODE": "TV 模式", "TABLETOP_MODE": "桌上模式", "HANDHELD_MODE": "手提模式"}
IMG_HASH = re.compile(r"/i/([0-9a-f]{20,})\.(?:jpg|png)")


class Blocked(Exception):
    pass


def gql(body_fields):
    """送一個 GraphQL 查詢；回傳 data.item（可能部分為 None）。"""
    body = json.dumps({"query": GQL_CTX % body_fields, "variables": GQL_VARS}).encode()
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(GQL_URL, data=body, headers=GQL_HEADERS)
            with urllib.request.urlopen(req, timeout=40) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            return (d.get("data") or {}).get("item") or {}
        except urllib.error.HTTPError as e:
            last = f"HTTP{e.code}"
            if e.code in (403, 429):
                time.sleep(20 * (attempt + 1))
            elif e.code == 400:
                return None
            else:
                time.sleep(5 * (attempt + 1))
        except Exception as e:
            last = type(e).__name__
            time.sleep(5 * (attempt + 1))
    raise Blocked(last)


def s(x):
    return x.strip() if isinstance(x, str) else ""


def gb(r):
    v = ((r or {}).get("totalRomSize") or (r or {}).get("estimatedRomSize") or {}).get("rawValue")
    return f"{v / 1e9:.1f}GB" if isinstance(v, (int, float)) and v > 0 else ""


def img_hash(url):
    m = IMG_HASH.search(s(url))
    return m.group(1) if m else ""


def build_detail(f):
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
        n = s((l or {}).get("name"))
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
    genres = [x.strip() for x in s(f.get("genre")).split("/") if x.strip()]
    rating = (f.get("ratingInfo") or {}).get("rating") or {}
    apps = [s(a.get("id")) for a in (f.get("applications") or []) if isinstance(a, dict) and s(a.get("id"))]
    rec = {"v": DETAIL_VERSION,
           "catch": s(f.get("catchCopy")), "desc": s(f.get("description"))[:3000],
           "publisher": s((f.get("publisher") or {}).get("name")), "release": s(f.get("releaseDateOnEshop")),
           "genres": genres, "players": players, "langs": langs,
           "pmax": max([pn.get(k) or 0 for k in ("offlineMax", "localMax", "onlineMax")] or [0]),
           "poff": pn.get("offlineMax") or 0, "rating": s(rating.get("name")),
           "size": size, "modes": modes, "shots": shots, "vids": vids, "titleId": apps[0] if apps else ""}
    return {k: v for k, v in rec.items() if v not in ("", [], None, 0) or k == "v"}


def fetch_apps(ids):
    """一次查多款遊戲（每款一個別名）。回傳 {nsuid: 資料或 None}。"""
    fields = " ".join(f"a{i}: applicationItem(nsUid: {nid}) {{ {APP_FIELDS} }}" for i, nid in enumerate(ids))
    item = gql(fields)
    if item is None:  # 整批被拒，改成一款一款查，找出問題的那款
        out = {}
        for nid in ids:
            one = gql(f"a0: applicationItem(nsUid: {nid}) {{ {APP_FIELDS} }}")
            out[nid] = (one or {}).get("a0")
            time.sleep(0.5)
        return out
    return {nid: item.get(f"a{i}") for i, nid in enumerate(ids)}


def fetch_dlc_lists(ids):
    """一次查多款遊戲的 DLC 清單（每款最多 200 個，超過再分頁）。"""
    out = {}
    def q(pairs):
        fields = " ".join(
            f"d{i}: dlcItemsByApplicationItem(nsUid: {nid}, offset: {off}, limit: 100, publicStatuses: [PUBLIC], "
            f"salesStatuses: [ONSALE, PRE_ORDER], isIncludeBundleItem: true) {{ {DLC_FIELDS} }}"
            for i, (nid, off) in enumerate(pairs))
        return gql(fields) or {}
    item = q([(nid, 0) for nid in ids])
    for i, nid in enumerate(ids):
        seg = item.get(f"d{i}") or {}
        items = list(seg.get("items") or [])
        total = (seg.get("offsetInfo") or {}).get("total") or 0
        off = 100
        while total > off and off < 1000:
            more = (q([(nid, off)]).get("d0") or {}).get("items") or []
            if not more:
                break
            items += more
            off += 100
            time.sleep(1)
        out[nid] = [{"id": str(it["nsUid"]), "name": s(it.get("formalName")), "img": img_hash(it.get("heroBannerUrl")),
                     "date": s(it.get("releaseDateOnEshop")), "mem": bool(it.get("isMembershipRequired"))}
                    for it in items if isinstance(it, dict) and it.get("__typename") == "DlcItem" and it.get("nsUid")]
    return out


def refresh_meta(games, dlcs, limit, retry_missing):
    """用任天堂商店的資料介面（台灣區）補齊名稱、平台、介紹與 DLC 清單。"""
    selling = lambda g: g.get("status") in SELLING
    need = [i for i, g in games.items() if selling(g) and (
            not g.get("meta") or g.get("dv", 1) < DETAIL_VERSION) and (retry_missing or not g.get("nodata"))]
    need.sort(key=lambda i: (games[i].get("meta", False), games[i].get("sale") is None, -int(i)))
    todo = need[:limit]
    log(f"抓商品資料：本次 {len(todo)} 筆（待處理 {len(need)}）")
    os.makedirs(os.path.join(DOCS, "d"), exist_ok=True)
    ok = nodata = 0
    done = []
    try:
        for n, b in enumerate(batches(todo, 20), 1):
            res = fetch_apps(b)
            for nid in b:
                f, g = res.get(nid), games[nid]
                if not f or not s(f.get("formalName")):
                    g["nodata"] = True
                    nodata += 1
                    continue
                det = build_detail(f)
                g.update(name=s(f["formalName"]), plat=f.get("labelPlatform") if f.get("labelPlatform") in ("HAC", "BEE") else "HAC",
                         img=img_hash(f.get("heroBannerUrl")) or g.get("img", ""), meta=True, nodata=False,
                         dv=DETAIL_VERSION, genres=det.get("genres", []), pmax=det.get("pmax", 0), poff=det.get("poff", 0),
                         zh=(1 if any("繁體" in l for l in det.get("langs", [])) else 0)
                            | (2 if any("簡體" in l for l in det.get("langs", [])) else 0))
                g.pop("hk404", None)
                done.append((nid, det))
                ok += 1
            if n % 20 == 0:
                log(f"  {n * 20}/{len(todo)}（成功 {ok}、查無 {nodata}）")
            time.sleep(1.2)
        log(f"  商品資料完成 {ok} 筆，查無資料 {nodata} 筆")
        # DLC 清單
        dl_total = 0
        for b in batches([nid for nid, _ in done], 10):
            lists = fetch_dlc_lists(b)
            for nid, det in done:
                if nid not in lists:
                    continue
                items = lists[nid]
                det["dlc"] = items
                games[nid]["dlc"] = [d["id"] for d in items]
                for d in items:
                    cur = dlcs.setdefault(d["id"], {"first_seen": TODAY})
                    cur.update(parent=nid, name=d["name"], img=d["img"], mem=d["mem"], date=d["date"])
                dl_total += len(items)
            time.sleep(1.2)
        log(f"  DLC 清單完成，共 {dl_total} 個")
    except Blocked as e:
        log(f"  資料介面暫時無法使用（{e}），先停止，下次再繼續")
    for nid, det in done:
        save(os.path.join(DOCS, "d", nid[-6:] + ".json"), det)


def refresh_all_dlc_lists(games, dlcs):
    """每週一次：重新查所有遊戲的 DLC 清單，找出新推出的 DLC。"""
    ids = [i for i, g in games.items() if g.get("status") in SELLING and g.get("meta")]
    log(f"重新整理 DLC 清單：{len(ids)} 款遊戲")
    found = 0
    try:
        for b in batches(ids, 10):
            for nid, items in fetch_dlc_lists(b).items():
                games[nid]["dlc"] = [d["id"] for d in items]
                for d in items:
                    if d["id"] not in dlcs:
                        found += 1
                    cur = dlcs.setdefault(d["id"], {"first_seen": TODAY})
                    cur.update(parent=nid, name=d["name"], img=d["img"], mem=d["mem"], date=d["date"])
            time.sleep(1.2)
    except Blocked as e:
        log(f"  資料介面暫時無法使用（{e}），先停止")
    log(f"  新發現 DLC {found} 個")


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


# ───────────── 匯率（台灣銀行即期賣出，失敗時改用 open.er-api.com，再失敗沿用上次） ─────────────
FX_CODES = ("HKD", "JPY")


def fetch_fx(prev):
    fx = {}
    try:
        for line in get("https://rate.bot.com.tw/xrt/flcsv/0/day").splitlines():
            c = [x.strip() for x in line.split(",")]
            if c and c[0] in FX_CODES and "本行賣出" in c:
                i = c.index("本行賣出")
                v = float(c[i + 2]) or float(c[i + 1])  # 即期賣出；沒有就用現金賣出
                if v > 0:
                    fx[c[0]] = v
    except Exception as e:
        log(f"  台銀匯率失敗：{e}")
    if len(fx) < len(FX_CODES):
        try:
            r = json.loads(get("https://open.er-api.com/v6/latest/TWD"))["rates"]
            for k in FX_CODES:
                if k not in fx and r.get(k):
                    fx[k] = round(1 / r[k], 4)
        except Exception as e:
            log(f"  備用匯率失敗：{e}")
    if not fx:
        log("  沿用上次匯率")
        return prev or {}
    out = dict(prev or {})
    out.update(fx)
    out["date"] = TODAY
    log(f"匯率：{ {k: out.get(k) for k in FX_CODES} }")
    return out


def build_site(games, dlcs, hist, hist_hk, asof, fx=None):
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
        "fx": fx or {},
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
    ap.add_argument("--full", action="store_true", help="從頭掃描全部編號，並重試查不到資料的商品")
    ap.add_argument("--meta-limit", type=int, default=5000)
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
    if args.full:
        refresh_all_dlc_lists(games, dlcs)
    refresh_dlc_prices({k: v for k, v in dlcs.items() if "status" not in v})  # 這次新發現的 DLC
    refresh_hk_prices({**games, **dlcs})

    items = {**games, **dlcs}
    drops = update_history(items, hist)
    update_hk_history(items, hist_hk)

    asof = now.strftime("%Y-%m-%d %H:%M")
    cat["asof"] = asof
    cat["fx"] = fetch_fx(cat.get("fx"))
    save(os.path.join(DATA, "catalog.json"), cat, pretty=True)
    save(os.path.join(DATA, "dlc.json"), dlcs, pretty=True)
    save(os.path.join(DATA, "history.json"), hist)
    save(os.path.join(DATA, "history_hk.json"), hist_hk)
    build_site(games, dlcs, hist, hist_hk, asof, cat["fx"])
    log(f"今日降價：{len(drops)} 筆")
    notify(items, drops)


TODAY = ""
if __name__ == "__main__":
    sys.exit(main())

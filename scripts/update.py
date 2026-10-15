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
            d = json.loads(get(PRICE_API.replace("lang=zh", "lang=ja" if cc == "JP" else "lang=zh").format(cc=cc, ids=",".join(map(str, ids)))))
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


def gql(body_fields, variables=None):
    """送一個 GraphQL 查詢；回傳 data.item（可能部分為 None）。"""
    body = json.dumps({"query": GQL_CTX % body_fields, "variables": variables or GQL_VARS}).encode()
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
                         dv=DETAIL_VERSION, app=det.get("titleId", ""), genres=det.get("genres", []), pmax=det.get("pmax", 0), poff=det.get("poff", 0),
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


# ───────────── 3b. 日本區對應與價格 ─────────────
JP_SEARCH = "https://search.nintendo.jp/nintendo_soft/search.json?limit=300&page={page}&{q}"
JP_VARS = {"shopId": 3, "country": "JP", "language": "ja"}


def list_jp_games():
    """日本官方搜尋：列出日本區所有下載版遊戲的 nsuid（約 1.5 萬款）。"""
    found = {}
    for q in ("opt_hard=1_HAC&opt_sform=HAC_DL", "opt_hard=1_HAC&opt_sform=HAC_DOWNLOADABLE", "opt_hard=05_BEE"):
        page = 1
        while page <= 200:
            r = json.loads(get(JP_SEARCH.format(page=page, q=q), timeout=40))["result"]
            for it in r.get("items", []):
                ns = str(it.get("nsuid") or "")
                if ns.startswith("7001"):
                    found[ns] = [s(it.get("title")), "BEE" if "BEE" in str(it.get("hard")) else "HAC"]
            if page * 300 >= r.get("total", 0) or not r.get("items"):
                break
            page += 1
            time.sleep(0.5)
    return found


def refresh_jp(games, jp, limit=6000):
    """用 applicationId 把台灣區遊戲對應到日本區，再查日本區價格（日圓）。"""
    apps = jp.setdefault("apps", {})      # 日本 nsuid → applicationId（"" 表示查無）
    titles = jp.setdefault("titles", {})
    try:
        listed = list_jp_games()
        log(f"日本區遊戲：{len(listed)} 款")
        titles.update(listed)
    except Exception as e:
        log(f"  日本區清單失敗，沿用上次：{e}")
        listed = {}
    todo = [n for n in (listed or titles) if n not in apps]
    todo.sort(key=lambda n: -int(n))
    todo = todo[:limit]
    if todo:
        log(f"  查日本區 applicationId：{len(todo)} 款")
        try:
            for k, b in enumerate(batches(todo, 20), 1):
                fields = " ".join(f"a{i}: applicationItem(nsUid: {n}) {{ nsUid applications {{ id }} }}" for i, n in enumerate(b))
                item = gql(fields, JP_VARS)
                if item is None:
                    for n in b:
                        one = gql(f"a0: applicationItem(nsUid: {n}) {{ nsUid applications {{ id }} }}", JP_VARS) or {}
                        a = (one.get("a0") or {}).get("applications") or []
                        apps[n] = a[0]["id"].lower() if a else ""
                        time.sleep(0.5)
                else:
                    for i, n in enumerate(b):
                        a = ((item.get(f"a{i}") or {}).get("applications")) or []
                        apps[n] = a[0]["id"].lower() if a else ""
                if k % 50 == 0:
                    log(f"    {k * 20}/{len(todo)}")
                time.sleep(1.0)
        except Blocked as e:
            log(f"  日本區資料介面暫時無法使用（{e}），下次再繼續")
    # applicationId → 日本 nsuid 候選（同一款可能有本體、同捆組合、Switch 2 版等多個商品）
    by_app = {}
    for n, a in apps.items():
        if a:
            by_app.setdefault(a, []).append(n)
    cands = {}
    for nid, g in games.items():
        a = (g.get("app") or "").lower()
        if a in by_app and g.get("status") in SELLING:
            cands[nid] = by_app[a]
    ids = sorted({n for c in cands.values() for n in c})
    prices = {}
    try:
        for b in batches(ids):
            for p in price_batch(b, "JP"):
                info = parse_price(p)
                if info["status"] != "not_found" and info["reg"] is not None:
                    prices[str(p.get("title_id"))] = {"reg": info["reg"], "sale": info["sale"], "end": info["end"]}
            time.sleep(0.4)
    except RuntimeError as e:
        log(f"  日本區查價失敗，略過：{e}")
        return
    # 選對應商品：同平台優先，再取原價最低的（通常是單買本體，不是同捆組合）
    matched = 0
    for nid, g in games.items():
        c = [n for n in cands.get(nid, []) if n in prices]
        if not c:
            g["jp"] = None
            g.pop("jpId", None)
            continue
        plat = g.get("plat") or "HAC"
        c.sort(key=lambda n: ((titles.get(n) or ["", "HAC"])[1] != plat if isinstance(titles.get(n), list) else 0,
                              prices[n]["reg"], -int(n)))
        g["jpId"], g["jp"] = c[0], prices[c[0]]
        matched += 1
    log(f"  台日對應（有價格）：{matched} 款")


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


def update_jp_history(games, hist):
    """日本區價格歷史（日圓），以日本區編號記錄。"""
    for g in games.values():
        jp, n = g.get("jp"), g.get("jpId")
        if not jp or not n or jp.get("reg") is None:
            continue
        p = jp["sale"] if jp.get("sale") is not None else jp["reg"]
        h = hist.setdefault(n, [])
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


def backfill_app_ids(games):
    """舊資料沒存 applicationId；從 d/<編號>.json 補上（跨區對應用）。"""
    for nsuid, g in games.items():
        if not g.get("meta") or g.get("app"):
            continue
        p = os.path.join(DOCS, "d", nsuid[-6:] + ".json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                g["app"] = json.load(f).get("titleId", "")


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


def build_site(games, dlcs, hist, hist_hk, asof, fx=None, hist_jp=None):
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
                     hk.get("reg"), hk.get("sale"), (g.get("jp") or {}).get("reg"), (g.get("jp") or {}).get("sale"),
                     g.get("jpId", "") if g.get("jp") else ""])
    rows.sort(key=lambda r: r[0])
    save(os.path.join(DOCS, "data.json"), {
        "asof": asof,
        "fx": fx or {},
        "fields": ["id", "name", "s2", "img", "reg", "sale", "end", "low", "lowDate", "nPrices", "status",
                   "firstSeen", "hasDetail", "genres", "zh", "pmax", "dlcN", "hkReg", "hkSale",
                   "jpReg", "jpSale", "jpId"],
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
    hist_jp = hist_jp or {}
    for nsuid in set(hist) | set(hist_hk):
        obj = {"tw": hist.get(nsuid, []), "hk": hist_hk.get(nsuid, [])}
        jid = (games.get(nsuid) or {}).get("jpId")
        if jid and hist_jp.get(jid):
            obj["jp"] = hist_jp[jid]
        changed += save_if_changed(os.path.join(hdir, nsuid + ".json"), obj)
    log(f"網站資料：遊戲 {len(rows)} 筆、DLC {len(drows)} 筆、價格歷史檔更新 {changed} 個")


# ───────────── 6. Telegram 通知（選用） ─────────────
def read_wishlist():
    """data/wishlist.txt：一行一個商品編號（14 碼），後面可加目標價，例如「70010000035483 500」。"""
    p = os.path.join(DATA, "wishlist.txt")
    wl = {}
    if not os.path.exists(p):
        return wl
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0]
            m = re.match(r"\s*(\d{14})(?:\D+(\d+))?", line)
            if m:
                wl[m.group(1)] = int(m.group(2)) if m.group(2) else None
    return wl


def cheaper_region(g, fx):
    """港／日區換算台幣後比台區便宜 10% 以上時，回傳說明文字。"""
    now = g.get("sale") if g.get("sale") is not None else g.get("reg")
    if not now:
        return ""
    best = None
    for name, key, cur in (("港區", "hk", "HKD"), ("日區", "jp", "JPY")):
        r = g.get(key) or {}
        p = r.get("sale") if r.get("sale") is not None else r.get("reg")
        if p is not None and fx.get(cur):
            twd = round(p * fx[cur])
            if twd <= now * 0.9 and (best is None or twd < best[1]):
                best = (name, twd)
    return f"\n　{best[0]}約 NT${best[1]:,}（便宜 {round((1 - best[1] / now) * 100)}%）" if best else ""


# ntfy 推播：手機裝 ntfy App、訂閱這個主題名稱就能收到。可用 GitHub 密鑰 NTFY_TOPIC 改成別的名稱。
NTFY_TOPIC = "eshop-radar-pw0ssnkn6ggo"
SITE = "https://jackie-1002.github.io/eshop-radar/"


def send_ntfy(topic, title, lines, tags):
    """ntfy 單則上限約 4KB，超過就拆成多則。"""
    chunks, cur = [], ""
    for line in lines:
        if cur and len((cur + "\n" + line).encode()) > 3500:
            chunks.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        chunks.append(cur)
    for i, c in enumerate(chunks):
        body = json.dumps({"topic": topic, "title": title + (f"（{i + 1}/{len(chunks)}）" if len(chunks) > 1 else ""),
                           "message": c, "tags": tags, "click": os.environ.get("SITE_URL") or SITE}).encode()
        req = urllib.request.Request("https://ntfy.sh/", data=body, headers={"Content-Type": "application/json", **UA})
        urllib.request.urlopen(req, timeout=20).read()


def notify(items, drops, fx):
    """推播通知（ntfy，另可加 Telegram）：收藏降價、達到目標價、特價快結束（同一件事只通知一次）。"""
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    topic = os.environ.get("NTFY_TOPIC") or NTFY_TOPIC
    wl = read_wishlist()
    if not wl:
        log("收藏清單是空的，略過通知")
        return
    sent = load("notified.json", {})
    sent = {k: v for k, v in sent.items() if v >= (datetime.now(TW) - timedelta(days=60)).strftime("%Y-%m-%d")}
    name = lambda n: items[n].get("name") or n
    price = lambda g: g.get("sale") if g.get("sale") is not None else g.get("reg")
    drop_lines, target_lines, end_lines = [], [], []
    drop_ids = {d[0]: d for d in drops}
    for nsuid, target in wl.items():
        g = items.get(nsuid)
        if not g or g.get("status") not in SELLING or price(g) is None:
            continue
        now = price(g)
        end = (g.get("end") or "")[:10]
        extra = cheaper_region(g, fx)
        # 達到目標價（價格變動後第一次低於等於目標價才通知）
        if target is not None and now <= target:
            key = f"t:{nsuid}:{now}"
            if key not in sent:
                target_lines.append(f"🎯 {name(nsuid)}\n　NT${now:,}（目標 NT${target:,}）"
                                    + (f"，特價到 {end[5:].replace('-', '/')}" if end else "") + extra)
                sent[key] = TODAY
                continue
        # 任何降價
        if nsuid in drop_ids:
            _, prev, p, is_low = drop_ids[nsuid]
            drop_lines.append(f"⬇️ {name(nsuid)}\n　NT${prev:,} → NT${p:,}" + ("（史低）" if is_low else "")
                              + (f"，特價到 {end[5:].replace('-', '/')}" if end else "") + extra)
        # 特價 2 天內結束（每次特價只提醒一次）
        if g.get("sale") is not None and end:
            left = (datetime.strptime(end, "%Y-%m-%d").date() - datetime.now(TW).date()).days
            key = f"e:{nsuid}:{end}"
            if 0 <= left <= 2 and key not in sent:
                end_lines.append(f"⏰ {name(nsuid)}\n　NT${now:,}，" + ("今天" if left == 0 else f"{left} 天後") + "結束" + extra)
                sent[key] = TODAY
    save(os.path.join(DATA, "notified.json"), sent)
    parts = []
    if target_lines:
        parts.append("【達到目標價】\n" + "\n".join(target_lines))
    if drop_lines:
        parts.append("【收藏降價】\n" + "\n".join(drop_lines))
    if end_lines:
        parts.append("【特價快結束】\n" + "\n".join(end_lines))
    if not parts:
        log("收藏清單沒有需要通知的事")
        return
    site = os.environ.get("SITE_URL") or SITE
    ok = []
    if topic:
        try:
            for lines, title, tag in ((target_lines, "達到目標價", "dart"), (drop_lines, "收藏的遊戲降價了", "chart_with_downwards_trend"),
                                      (end_lines, "特價快結束", "alarm_clock")):
                if lines:
                    send_ntfy(topic, title, [l for x in lines for l in (x + "\n").split("\n")][:-1], [tag])
            ok.append("ntfy")
        except Exception as e:
            log(f"ntfy 發送失敗：{e}")
    if token and chat:
        text = "\n\n".join(parts) + f"\n\n{site}"
        try:
            for k in range(0, len(text), 3800):  # Telegram 單則上限 4096 字
                body = urllib.parse.urlencode({"chat_id": chat, "text": text[k:k + 3800],
                                               "disable_web_page_preview": "true"}).encode()
                urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=20)
            ok.append("Telegram")
        except Exception as e:
            log(f"Telegram 發送失敗：{e}")
    log(f"已發送通知（{'、'.join(ok) or '無'}）：目標價 {len(target_lines)}、降價 {len(drop_lines)}、快結束 {len(end_lines)}")


def main():
    global TODAY
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="從頭掃描全部編號，並重試查不到資料的商品")
    ap.add_argument("--meta-limit", type=int, default=5000)
    ap.add_argument("--test-notify", action="store_true", help="只發一則測試通知")
    args = ap.parse_args()
    if args.test_notify:
        send_ntfy(os.environ.get("NTFY_TOPIC") or NTFY_TOPIC, "台區特價雷達：測試通知",
                  ["收到這則訊息，代表通知設定成功了 🎉", "之後收藏的遊戲降價、達到目標價、特價快結束時，都會在這裡通知你。"], ["tada"])
        log("已發送測試通知")
        return
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
    backfill_app_ids(games)
    jp = load("jp.json", {})
    refresh_jp(games, jp)

    items = {**games, **dlcs}
    drops = update_history(items, hist)
    update_hk_history(items, hist_hk)
    hist_jp = load("history_jp.json", {})
    update_jp_history(games, hist_jp)

    asof = now.strftime("%Y-%m-%d %H:%M")
    cat["asof"] = asof
    cat["fx"] = fetch_fx(cat.get("fx"))
    save(os.path.join(DATA, "catalog.json"), cat, pretty=True)
    save(os.path.join(DATA, "dlc.json"), dlcs, pretty=True)
    save(os.path.join(DATA, "history.json"), hist)
    save(os.path.join(DATA, "history_hk.json"), hist_hk)
    save(os.path.join(DATA, "jp.json"), jp)
    save(os.path.join(DATA, "history_jp.json"), hist_jp)
    build_site(games, dlcs, hist, hist_hk, asof, cat["fx"], hist_jp)
    log(f"今日降價：{len(drops)} 筆")
    notify(items, drops, cat.get("fx") or {})


TODAY = ""
if __name__ == "__main__":
    sys.exit(main())

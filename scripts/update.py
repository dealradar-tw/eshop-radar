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
import argparse, json, os, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA, DOCS = os.path.join(ROOT, "data"), os.path.join(ROOT, "docs")
TW = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "zh-TW,zh;q=0.9"}
PRICE_API = "https://api.ec.nintendo.com/v1/price?country={cc}&lang=zh&ids={ids}"
BASE = 70010000000000
SELLING = ("onsale", "preorder", "unreleased")
DETAIL_VERSION = 4   # 詳細資料格式版本；舊版會在之後的排程中重抓


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
    known = sorted(int(i) for i, g in games.items() if g.get("only") != "jp")  # 日本限定的另外處理
    top = max(known) if known else BASE
    scan = range(BASE, top + 6000) if full else range(max(BASE, top - 2000), top + 6000)
    targets = sorted(set(known) | set(scan))
    log(f"查價（台）：{len(targets)} 個編號（已知遊戲 {len(known)}）")
    seen, new, hk_new = set(), [], []
    bs = batches(targets)
    for n, b in enumerate(bs, 1):
        tw_hit = set()
        for p in price_batch(b):
            nsuid = str(p.get("title_id"))
            info = parse_price(p)
            if info["status"] == "not_found":
                continue
            seen.add(nsuid)
            tw_hit.add(nsuid)
            g = games.get(nsuid)
            if g is None:
                g = games[nsuid] = {"name": "", "plat": "", "img": "", "meta": False,
                                    "first_seen": TODAY}
                new.append(nsuid)
            g.update(info)
            g.pop("only", None)  # 台灣也有賣了
        # 港區限定：台灣沒有、港區有的遊戲
        cand = [str(i) for i in b if str(i) not in tw_hit and str(i) not in games]
        if cand:
            try:
                for p in price_batch(cand, "HK"):
                    info = parse_price(p)
                    nsuid = str(p.get("title_id"))
                    if info["status"] == "not_found" or nsuid in games:
                        continue
                    games[nsuid] = {"name": "", "plat": "", "img": "", "meta": False, "only": "hk",
                                    "first_seen": "" if full else TODAY, "status": info["status"],
                                    "reg": None, "sale": None, "end": "",
                                    "hk": {"reg": info["reg"], "sale": info["sale"], "end": info["end"]}}
                    hk_new.append(nsuid)
            except RuntimeError as e:
                log(f"  港區限定查詢失敗，略過：{e}")
        if n % 20 == 0:
            log(f"  {n}/{len(bs)}")
        time.sleep(0.4)
    known_set = set(map(str, known))
    for nsuid, g in games.items():
        if nsuid not in seen and nsuid in known_set and g.get("only") != "hk":
            g["status"] = "not_found"  # 已下架
    log(f"新發現 {len(new)} 個商品；港區限定新增 {len(hk_new)} 個")
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
    ids = [i for i, g in items.items() if g.get("status") in SELLING and g.get("only") != "jp"]
    log(f"查價（港）：{len(ids)} 個")
    for b in batches(ids):
        try:
            for p in price_batch(b, "HK"):
                nsuid = str(p.get("title_id"))
                if nsuid in items:
                    info = parse_price(p)
                    nf = info["status"] == "not_found"
                    items[nsuid]["hk"] = None if nf else {"reg": info["reg"], "sale": info["sale"], "end": info["end"]}
                    if items[nsuid].get("only") == "hk":  # 港區限定的商品，狀態以港區為準
                        items[nsuid]["status"] = "not_found" if nf else info["status"]
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
              "playStyles { name } ratingInfo { rating { name age } } applications { id } demoItems { nsUid } "
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
           "size": size, "modes": modes, "shots": shots, "vids": vids, "titleId": apps[0] if apps else "",
           "demo": [str(d.get("nsUid")) for d in (f.get("demoItems") or []) if d and d.get("nsUid")]}
    return {k: v for k, v in rec.items() if v not in ("", [], None, 0) or k == "v"}


HK_VARS = {"shopId": 3, "country": "HK", "language": "zh"}


def fetch_apps(ids, variables=None):
    """一次查多款遊戲（每款一個別名）。回傳 {nsuid: 資料或 None}。
    台灣區商店還沒公開頁面的（常見於剛上架、尚未發售的遊戲），改查港區（同一編號、同樣是中文）。"""
    fields = " ".join(f"a{i}: applicationItem(nsUid: {nid}) {{ {APP_FIELDS} }}" for i, nid in enumerate(ids))
    item = gql(fields, variables)
    if item is None:  # 整批被拒，改成一款一款查，找出問題的那款
        out = {}
        for nid in ids:
            one = gql(f"a0: applicationItem(nsUid: {nid}) {{ {APP_FIELDS} }}", variables)
            out[nid] = (one or {}).get("a0")
            time.sleep(0.5)
    else:
        out = {nid: item.get(f"a{i}") for i, nid in enumerate(ids)}
    missing = [n for n, f in out.items() if not f]
    if missing and variables is None:
        time.sleep(0.5)
        for n, f in fetch_apps(missing, HK_VARS).items():
            if f:
                out[n] = f
    return out


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


def refresh_movies(games, limit=3000):
    """補宣傳影片：優先用台灣區的，台灣沒有才用香港區的，香港也沒有才用日本區的（不混用）。
    影片可能在遊戲上架後才放上去，所以每 7 天重查一次。
    games[n]["mv2"] 記錄上次檢查的日期序號（舊的 mv 是只查台灣＋香港的版本，不再使用）。"""
    day = datetime.now(timezone(timedelta(hours=8))).date().toordinal()
    todo = [i for i, g in games.items() if g.get("meta") and g.get("status") in SELLING
            and day - (g.get("mv2") or 0) >= 7]
    todo.sort(key=lambda n: (games[n].get("mv2") or 0, -int(n)))
    todo = todo[:limit]
    if not todo:
        return
    log(f"補影片（台灣→香港→日本）：{len(todo)} 款")
    updated = 0
    mv = "movies { __typename ... on YoutubeMovie { youtubeId } }"

    def query(ids, vars_):
        """ids: {本地編號: 該區商店的編號} → {本地編號: [YouTube 影片]}"""
        out = {}
        keys = list(ids)
        for c in batches(keys, 20):
            item = gql(" ".join(f"a{i}: applicationItem(nsUid: {ids[n]}) {{ nsUid {mv} }}" for i, n in enumerate(c)), vars_) or {}
            for i, n in enumerate(c):
                out[n] = [m.get("youtubeId") for m in ((item.get(f"a{i}") or {}).get("movies") or []) if m and m.get("youtubeId")]
            time.sleep(0.5)
        return out

    try:
        for chunk in batches(todo, 100):
            found = {n: [] for n in chunk}
            # 日本限定的遊戲本身就是日本編號，只查日本區
            tw_ids = {n: n for n in chunk if games[n].get("only") != "jp"}
            if tw_ids:
                for n, v in query(tw_ids, GQL_VARS).items():
                    found[n] = v
                miss = {n: n for n in tw_ids if not found[n]}
                if miss:
                    for n, v in query(miss, HK_VARS).items():
                        found[n] = v
            jp_ids = {n: (n if games[n].get("only") == "jp" else games[n].get("jpId")) for n in chunk if not found[n]}
            jp_ids = {n: j for n, j in jp_ids.items() if j}
            if jp_ids:
                for n, v in query(jp_ids, JP_VARS).items():
                    found[n] = v
            for n in chunk:
                p = os.path.join(DOCS, "d", n[-6:] + ".json")
                if found[n] and os.path.exists(p):
                    with open(p, encoding="utf-8") as f:
                        det = json.load(f)
                    vids = list(dict.fromkeys(found[n]))
                    if vids != det.get("vids"):
                        det["vids"] = vids
                        save(p, det)
                        updated += 1
                games[n]["mv2"] = day
    except Blocked as e:
        log(f"  資料介面暫時無法使用（{e}），下次再繼續")
    log(f"  影片更新 {updated} 款")


def refresh_meta(games, dlcs, limit, retry_missing):
    """用任天堂商店的資料介面（台灣區）補齊名稱、平台、介紹與 DLC 清單。"""
    selling = lambda g: g.get("status") in SELLING
    need = [i for i, g in games.items() if selling(g) and g.get("only") != "jp" and (
            not g.get("meta") or g.get("dv", 1) < DETAIL_VERSION) ]  # 查無資料的每天都再試（多半是剛上架、商店頁還沒公開）
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
                         dv=DETAIL_VERSION, app=det.get("titleId", ""), rel=det.get("release", ""), demo=1 if det.get("demo") else 0, genres=det.get("genres", []), pmax=det.get("pmax", 0), poff=det.get("poff", 0),
                         zh=zh_code(det), zv=2, mv=0)
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
        if g.get("only") != "jp" and a in by_app and g.get("status") in SELLING:
            cands[nid] = list(by_app[a])
    # applicationId 各區不同的（不少 Switch 2 遊戲如此）：用 link_regions 比對出來的對應
    for jn, nids in jp.get("link", {}).items():
        for nid in nids:
            g = games.get(nid)
            if g and g.get("only") != "jp" and g.get("status") in SELLING and jn not in cands.get(nid, []):
                cands.setdefault(nid, []).append(jn)
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
        if g.get("only") == "jp":
            continue
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


FAMITSU = "https://www.famitsu.com/ranking/game-sales/"


def _nz(t):
    import unicodedata
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", t or "")).lower()


WD_ENDPOINT = "https://query.wikidata.org/sparql"
WD_QUERY = """SELECT ?en ?ja ?zh ?zhl WHERE { ?g wdt:P400 wd:Q19610114 .
 ?g rdfs:label ?zh . FILTER(LANG(?zh) IN ("zh-hant","zh-tw","zh-hk","zh-mo","zh")) BIND(LANG(?zh) AS ?zhl)
 OPTIONAL{?g rdfs:label ?en FILTER(LANG(?en)="en")} OPTIONAL{?g rdfs:label ?ja FILTER(LANG(?ja)="ja")} }"""
WD_PRI = {"zh-tw": 0, "zh-hant": 1, "zh-hk": 2, "zh-mo": 3, "zh": 4}
HAN = re.compile(r"[\u4e00-\u9fff]")


def norm_title(t):
    t = re.sub(r"[™®©]", "", t or "")
    t = unicodedata.normalize("NFKC", t).lower()
    return re.sub(r"[\W_]+", "", t)


# 知名系列的台灣慣用中文名（人工整理）。遊戲名稱「開頭」是這些系列名時，把系列名換成中文、其餘照留。
SERIES_ZH = [
    (["Resident Evil", "BIOHAZARD", "バイオハザード"], "惡靈古堡"),
    (["Monster Hunter", "モンスターハンター"], "魔物獵人"),
    (["Fire Emblem: Fortune's Weave", "Fire Emblem Fortune's Weave", "ファイアーエムブレム 万紫千紅"], "聖火降魔錄 萬縷千絲"),
    (["Fire Emblem", "ファイアーエムブレム"], "聖火降魔錄"),
    (["The Legend of Zelda", "Zelda", "ゼルダの伝説"], "薩爾達傳說"),
    (["Super Mario Bros. Wonder", "スーパーマリオブラザーズ ワンダー"], "超級瑪利歐兄弟 驚奇"),
    (["Super Mario Bros.", "スーパーマリオブラザーズ"], "超級瑪利歐兄弟"),
    (["Super Mario Odyssey", "スーパーマリオ オデッセイ"], "超級瑪利歐 奧德賽"),
    (["Super Mario Party", "スーパー マリオパーティ", "スーパーマリオパーティ"], "超級瑪利歐派對"),
    (["Super Mario", "スーパーマリオ"], "超級瑪利歐"),
    (["Mario Kart World", "マリオカート ワールド"], "瑪利歐賽車 世界"),
    (["Mario Kart", "マリオカート"], "瑪利歐賽車"),
    (["Mario Party", "マリオパーティ"], "瑪利歐派對"),
    (["Paper Mario", "ペーパーマリオ"], "紙片瑪利歐"),
    (["Luigi's Mansion", "ルイージマンション"], "路易吉洋樓"),
    (["Pokémon", "Pokemon", "ポケットモンスター", "ポケモン"], "寶可夢"),
    (["Animal Crossing", "あつまれ どうぶつの森", "どうぶつの森"], "動物森友會"),
    (["Splatoon", "スプラトゥーン"], "斯普拉遁"),
    (["Kirby", "星のカービィ"], "星之卡比"),
    (["Donkey Kong", "ドンキーコング"], "咚奇剛"),
    (["Super Smash Bros.", "大乱闘スマッシュブラザーズ"], "任天堂明星大亂鬥"),
    (["Pikmin", "ピクミン"], "皮克敏"),
    (["Xenoblade Chronicles", "ゼノブレイド"], "異度神劍"),
    (["Metroid", "メトロイド"], "銀河戰士"),
    (["Final Fantasy", "ファイナルファンタジー"], "最終幻想"),
    (["Dragon Quest Monsters", "ドラゴンクエストモンスターズ"], "勇者鬥惡龍 怪獸篇"),
    (["Dragon Quest Builders", "ドラゴンクエストビルダーズ"], "勇者鬥惡龍 創世小玩家"),
    (["Dragon Quest Treasures", "ドラゴンクエストトレジャーズ"], "勇者鬥惡龍 尋寶探險團"),
    (["Dragon Quest Heroes", "ドラゴンクエストヒーローズ"], "勇者鬥惡龍 英雄集結"),
    (["Dragon Quest", "ドラゴンクエスト"], "勇者鬥惡龍"),
    (["Persona 3", "ペルソナ3"], "女神異聞錄3"),
    (["Persona 4", "ペルソナ4"], "女神異聞錄4"),
    (["Persona 5", "ペルソナ5"], "女神異聞錄5"),
    (["Persona Q", "ペルソナQ"], "女神異聞錄Q"),
    (["Shin Megami Tensei", "真・女神転生"], "真・女神轉生"),
    (["Dragon Ball", "ドラゴンボール"], "七龍珠"),
    (["One Piece", "ONE PIECE", "ワンピース"], "航海王"),
    (["NARUTO－ナルト－ 疾風伝", "NARUTO SHIPPUDEN", "NARUTO: Ultimate Ninja STORM"], "火影忍者 疾風傳"),
    (["Naruto", "NARUTO", "ナルト"], "火影忍者"),
    (["Street Fighter", "ストリートファイター"], "快打旋風"),
    (["Sniper Elite", "スナイパーエリート"], "狙擊之神"),
    (["Little Nightmares", "リトルナイトメア"], "小小夢魘"),
    (["Tekken", "鉄拳"], "鐵拳"),
    (["Kingdom Hearts", "キングダム ハーツ", "キングダムハーツ"], "王國之心"),
    (["Metal Gear Solid", "メタルギア ソリッド", "メタルギアソリッド"], "潛龍諜影"),
    (["Metal Gear", "メタルギア"], "潛龍諜影"),
    (["Devil May Cry", "デビル メイ クライ"], "惡魔獵人"),
    (["Dark Souls", "DARK SOULS", "ダークソウル"], "黑暗靈魂"),
    (["Elden Ring", "ELDEN RING", "エルデンリング"], "艾爾登法環"),
    (["Minecraft", "マインクラフト"], "我的世界"),
    (["Ace Attorney", "逆転裁判"], "逆轉裁判"),
    (["Professor Layton", "レイトン教授"], "雷頓教授"),
    (["Bayonetta", "ベヨネッタ"], "魔兵驚天錄"),
    (["Octopath Traveler", "OCTOPATH TRAVELER", "オクトパストラベラー"], "歧路旅人"),
    (["Bravely Default", "ブレイブリーデフォルト"], "勇氣默示錄"),
    (["Disgaea", "魔界戦記ディスガイア"], "魔界戰記"),
    (["Yo-kai Watch", "妖怪ウォッチ"], "妖怪手錶"),
    (["Digimon", "デジモン"], "數碼寶貝"),
    (["Hatsune Miku", "初音ミク"], "初音未來"),
    (["Nobunaga's Ambition", "信長の野望"], "信長之野望"),
    (["Castlevania", "悪魔城ドラキュラ"], "惡魔城"),
    (["Gundam", "ガンダム"], "鋼彈"),
    (["Sword Art Online", "ソードアート・オンライン"], "刀劍神域"),
    (["Attack on Titan", "進撃の巨人"], "進擊的巨人"),
    (["Demon Slayer", "鬼滅の刃"], "鬼滅之刃"),
    (["My Hero Academia", "僕のヒーローアカデミア"], "我的英雄學院"),
    (["Puyo Puyo", "ぷよぷよ"], "魔法氣泡"),
]
EDITION_ZH = [(r"Deluxe Edition|デラックスエディション|\bDeluxe\b", "豪華版"), (r"Gold Edition|ゴールドエディション", "黃金版"),
              (r"Ultimate Edition|アルティメットエディション", "終極版"), (r"Complete Edition|コンプリートエディション", "完全版"),
              (r"Definitive Edition", "決定版"), (r"Standard Edition", "標準版"), (r"Collector'?s Edition", "典藏版"),
              (r"ナルティメットストーム|Ultimate Ninja Storm", "終極風暴")]
_SERIES = sorted(((a, zh) for al, zh in SERIES_ZH for a in al), key=lambda x: -len(x[0]))


def series_zh(name):
    """依系列名稱規則取得中文名；對不上回傳空字串。系列名必須在最前面，且後面不能緊接英文字母。"""
    nm = re.sub(r"[™®©]", "", name or "").strip()
    low = unicodedata.normalize("NFKC", nm).lower()
    for al, zh in _SERIES:
        a = unicodedata.normalize("NFKC", al).lower()
        if low.startswith(a) and not re.match(r"[a-z]", low[len(a):len(a) + 1]):
            rest = unicodedata.normalize("NFKC", nm)[len(a):].strip(" 　:：-－–—･・")
            for pat, rep in EDITION_ZH:
                rest = re.sub(pat, rep, rest, flags=re.I)
            return (zh + (" " + rest if rest else "")).strip()
    return ""


try:
    from opencc import OpenCC
    _S2T = OpenCC("s2twp")  # 簡體→台灣正體（含台灣慣用詞）
except Exception:
    _S2T = None


def to_tw(t):
    """維基數據的中文標題有些是簡體，一律轉成台灣用字；沒裝轉換套件就原樣回傳。"""
    try:
        return _S2T.convert(t) if _S2T else t
    except Exception:
        return t


def refresh_zhnames(games):
    """中文名稱：商店名稱是英文或日文的遊戲，優先用維基數據（Wikidata）裡 Switch 遊戲的中文標題（原名完全對得上才用），
    對不上再用知名系列名稱規則（SERIES_ZH）。維基數據每週更新一次，失敗就沿用上次。"""
    path = os.path.join(DOCS, "zhnames.json")
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    old = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    wd = old.get("wd") or old.get("n") or {}
    asof = old.get("asof") or today
    fresh = old.get("asof") and (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(old["asof"], "%Y-%m-%d")).days < 6
    table = None
    if not fresh:
        try:
            url = WD_ENDPOINT + "?format=json&query=" + urllib.parse.quote(WD_QUERY)
            req = urllib.request.Request(url, headers={"User-Agent": "eshop-radar/1.0 (github.com/dealradar-tw/eshop-radar)",
                                                       "Accept": "application/sparql-results+json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                rows = json.loads(r.read().decode("utf-8"))["results"]["bindings"]
            table = {}  # 原名（正規化）→ (優先序, 中文名)
            for b in rows:
                zh = re.sub(r"\s*[（(](?:遊戲|游戏|電子遊戲|电子游戏|視頻遊戲|视频游戏|電玩遊戲)[)）]\s*$", "", b["zh"]["value"].strip())
                if not HAN.search(zh):
                    continue
                pri = WD_PRI.get(b["zhl"]["value"], 9)
                for k in ("en", "ja"):
                    if k in b:
                        key = norm_title(b[k]["value"])
                        if len(key) >= 3 and (key not in table or pri < table[key][0]):
                            table[key] = (pri, zh)
            asof = today
        except Exception as e:
            log(f"  中文名稱（維基數據）抓取失敗，沿用上次：{e}")
    if table is not None:
        wd = {}
        for nid, g in games.items():
            nm = g.get("name") or ""
            if not nm or (HAN.search(nm) and not re.search(r"[\u3040-\u30ff]", nm)):
                continue
            base = re.sub(r"[（(][^)）]*[)）]", "", nm)
            for cand in (nm, base):
                hit = table.get(norm_title(cand))
                if hit and norm_title(hit[1]) != norm_title(nm):
                    wd[nid[-6:]] = hit[1]
                    break
    wd = {k: to_tw(v) for k, v in wd.items()}
    out = dict(wd)
    nser = 0
    for nid, g in games.items():
        nm = g.get("name") or ""
        k = nid[-6:]
        if not nm or k in out or (HAN.search(nm) and not re.search(r"[\u3040-\u30ff]", nm)):
            continue
        z = series_zh(nm)
        if z and norm_title(z) != norm_title(nm):
            out[k] = z; nser += 1
    log(f"  中文名稱：維基數據 {len(wd)} 款＋系列規則 {nser} 款")
    if out:
        save(path, {"asof": asof, "wd": wd, "n": out})


def _title_keys(t):
    """同一款遊戲的幾種寫法：原名、去掉括號（常附讀音或英文名）、只取括號裡的名字＋後綴。
    例：「Xenoblade2 (ゼノブレイド2) Nintendo Switch 2 Edition」→ 也能對上「ゼノブレイド2 Nintendo Switch 2 Edition」。"""
    keys = {_nz(t)}
    paren = re.compile(r"\s*[（(]([^)）]*)[)）]\s*")
    m = paren.search(t)
    if m:
        keys.add(_nz(paren.sub(" ", t)))                       # 去掉括號整段
        keys.add(_nz(m.group(1) + " " + t[m.end():]))           # 括號裡的名字＋後綴
        keys.add(_nz(m.group(1)))
    for sep in (" | ", " / ", "｜", " — ", " - "):                # 「日文名 | 英文名」這類
        if sep in t:
            keys.update(_nz(x) for x in t.split(sep))
    keys.discard("")
    return keys


def match_hot(items, games, jp):
    """把 Fami通 排行的日文名稱對到商店裡的遊戲，寫入 id（合輯則寫 parts）。回傳對不到的數量。
    對應順序：手動對照表 data/fami_alias.json → 日本區商品名（含括號變體）→
    同一 applicationId 的台／港區遊戲 → 日本區限定條目本身 → 對應到這個日本編號的遊戲。"""
    titles = jp.get("titles", {}); apps = jp.get("apps", {})
    alias = {_nz(k): v for k, v in load("fami_alias.json", {}).items() if not k.startswith("_")}
    idx = {}
    for n, v in titles.items():
        if isinstance(v, list):
            for k in _title_keys(v[0]):
                idx.setdefault((k, v[1]), []).append(n)
    by_app, by_jp = {}, {}
    for nid, g in games.items():
        if g.get("status") not in SELLING:
            continue
        if g.get("app"):
            by_app.setdefault(g["app"].lower(), []).append(nid)
        if g.get("jpId"):
            by_jp.setdefault(g["jpId"], []).append(nid)

    def pick(cands, plat, used):
        cands = [c for c in cands if c in games and (games[c].get("plat") or "HAC") == plat and c not in used]
        cands.sort(key=lambda c: (games[c].get("only") or "", c))   # 台灣區優先，其次港區，最後日本限定
        return cands[0] if cands else None

    used, miss = set(), 0
    for it in items:
        it.pop("id", None); it.pop("parts", None)
        plat = "BEE" if it["plat"] == "s2" else "HAC"
        a = alias.get(_nz(it["title"]))
        if isinstance(a, list):                       # 實體合輯：數位版分開賣
            parts = [x for x in a if x in games]
            if parts:
                it["id"], it["parts"] = None, parts
                continue
        elif isinstance(a, str) and a in games:
            it["id"] = a; used.add(a)
            continue
        found = None
        for jn in idx.get((_nz(it["title"]), plat), []):
            cands = list(by_app.get(apps.get(jn) or "-", [])) + [jn] + by_jp.get(jn, [])
            found = pick(cands, plat, used)
            if found:
                break
        it["id"] = found
        if found:
            used.add(found)
        else:
            miss += 1
            log(f"    Fami通 #{it['fr']} 對不到商店：{it['title']}（可在 data/fami_alias.json 手動對照）")
    return miss


def rematch_hot(games, jp):
    """沿用上次的排行，但重新對應商店（對照表或商店資料有更新時也會生效）。"""
    path = os.path.join(DOCS, "hot.json")
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return
    if old.get("items"):
        match_hot(old["items"], games, jp)
        save_if_changed(path, old)


def refresh_hot(games, jp):
    """熱門排行：抓 Fami通 每週銷量排行（日本實體＋下載卡等推估銷量），Switch／Switch 2 的全部列出，
    對得上商店的遊戲附上編號（可點進介紹頁）。"""
    try:
        h = get(FAMITSU, 40)
    except Exception as e:
        log(f"  Fami通排行抓取失敗，沿用上次：{e}")
        return rematch_hot(games, jp)
    pat = re.compile(r'__ranking">(\d+)</div><div><div class="[^"]*__platform[^"]*?__(\w+)">[^<]*</div>'
                     r'<div class="[^"]*__title">([^<]*)</div><div class="[^"]*salesCountLine">'
                     r'<p class="[^"]*salesCount">([\d,]+)')
    cards = []
    for chunk in re.split(r'<li class="[^"]*listItem">', h)[1:]:
        m = pat.search(chunk)
        if not m:
            continue
        tot = re.search(r"累計: </span>([\d,]+)", chunk)
        rel = re.search(r"発売日：(?:<!-- -->)?([^<]+)", chunk)
        cards.append(m.groups() + (tot.group(1) if tot else "", rel.group(1).strip() if rel else ""))
    m = re.search(r"集計期間：(.*?)</p>", h)
    period = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
    if not cards:
        log("  Fami通排行解析不到資料（網頁格式可能改了），沿用上次")
        return rematch_hot(games, jp)
    items = [{"fr": int(rk), "plat": "s2" if "switch2" in pl else "sw", "title": title.strip(),
              "sales": int(sales.replace(",", "")), "total": int(total.replace(",", "") or 0), "rel": rel}
             for rk, pl, title, sales, total, rel in cards if "switch" in pl]
    miss = match_hot(items, games, jp)
    out = {"week": period, "src": "Fami通", "items": items}
    log(f"Fami通排行（{period}）：Switch／Switch 2 共 {len(items)} 款，商店找不到 {miss} 款")
    if items:
        save_if_changed(os.path.join(DOCS, "hot.json"), out)



# ───────────── 3c. 日本限定遊戲（台灣、香港都沒有的） ─────────────
JP_GENRE = [("アクション", "動作"), ("アドベンチャー", "冒險"), ("シミュレーション", "模擬"), ("パズル", "益智"),
            ("アーケード", "街機"), ("ロールプレイング", "角色扮演"), ("RPG", "角色扮演"), ("ストラテジー", "策略"),
            ("パーティー", "派對"), ("シューティング", "射擊"), ("学習", "學習"), ("教育", "學習"), ("トレーニング", "訓練"),
            ("コミュニケーション", "交流"), ("実用", "實用"), ("レース", "競速"), ("スポーツ", "運動"),
            ("テーブル", "桌上遊戲"), ("ボード", "桌上遊戲"), ("格闘", "格鬥"), ("音楽", "音樂"), ("リズム", "音樂"),
            ("その他", "其他")]


def jp_genres(gs):
    out = []
    for g in gs or []:
        t = next((v for k, v in JP_GENRE if k in g), "其他")
        if t not in out:
            out.append(t)
    return out


def refresh_jponly(games, jp):
    """日本區有、台灣與香港都沒有的遊戲：以日本區編號建立商品，價格用日圓。"""
    titles, apps = jp.get("titles", {}), jp.get("apps", {})
    owned = {(g.get("app") or "").lower() for g in games.values() if g.get("app") and g.get("only") != "jp"}
    linked = set(jp.get("link", {}))
    for n in [n for n, g in games.items() if g.get("only") == "jp" and ((g.get("app") or "").lower() in owned or n in linked)]:
        del games[n]  # 後來發現台灣或香港也有賣，併回原本的商品
    first = not any(g.get("only") == "jp" for g in games.values())
    added = 0
    for n, a in apps.items():
        t = titles.get(n)
        if not a or a in owned or n in linked or n in games or not isinstance(t, list):
            continue
        games[n] = {"name": t[0], "plat": t[1] if t[1] in ("HAC", "BEE") else "HAC", "img": "", "meta": False,
                    "only": "jp", "first_seen": "" if first else TODAY, "status": "onsale",
                    "reg": None, "sale": None, "end": "", "app": "", "jp": None}
        added += 1
    ids = [n for n, g in games.items() if g.get("only") == "jp"]
    log(f"日本限定：{len(ids)} 款（新增 {added}）")
    try:
        for b in batches(ids):
            found = {str(p.get("title_id")): parse_price(p) for p in price_batch(b, "JP")}
            for n in b:
                info, g = found.get(n), games[n]
                if not info or info["status"] == "not_found" or info["reg"] is None:
                    g["status"], g["jp"] = "not_found", None
                    g.pop("jpId", None)
                else:
                    g["status"] = info["status"]
                    g["jp"] = {"reg": info["reg"], "sale": info["sale"], "end": info["end"]}
                    g["jpId"] = n
            time.sleep(0.4)
    except RuntimeError as e:
        log(f"  日本限定查價失敗，略過：{e}")


def refresh_meta_jponly(games, limit):
    """日本限定遊戲的名稱、封面、介紹（日本區商店，日文）。"""
    need = [n for n, g in games.items() if g.get("only") == "jp" and g.get("status") in SELLING
            and (not g.get("meta") or g.get("dv", 1) < DETAIL_VERSION)]
    need.sort(key=lambda n: (games[n].get("nodata", False), -int(n)))
    todo = need[:limit]
    log(f"抓日本限定商品資料：本次 {len(todo)} 筆（待處理 {len(need)}）")
    os.makedirs(os.path.join(DOCS, "d"), exist_ok=True)
    ok = nodata = 0
    try:
        for k, b in enumerate(batches(todo, 20), 1):
            res = fetch_apps(b, JP_VARS)
            for n in b:
                f, g = res.get(n), games[n]
                if not f or not s(f.get("formalName")):
                    g["nodata"] = True
                    nodata += 1
                    continue
                det = build_detail(f)
                det["genres"] = jp_genres(det.get("genres"))
                rel = re.sub(r"[./]", "-", det.get("release", ""))
                g.update(name=s(f["formalName"]), plat=f.get("labelPlatform") if f.get("labelPlatform") in ("HAC", "BEE") else g.get("plat", "HAC"),
                         img=img_hash(f.get("heroBannerUrl")) or g.get("img", ""), meta=True, nodata=False,
                         dv=DETAIL_VERSION, app=det.get("titleId", ""), rel=rel, demo=1 if det.get("demo") else 0,
                         genres=det["genres"], pmax=det.get("pmax", 0), poff=det.get("poff", 0),
                         zh=zh_code(det), zv=2, mv=1)
                save(os.path.join(DOCS, "d", n[-6:] + ".json"), det)
                ok += 1
            if k % 20 == 0:
                log(f"  {k * 20}/{len(todo)}（成功 {ok}、查無 {nodata}）")
            time.sleep(1.2)
    except Blocked as e:
        log(f"  資料介面暫時無法使用（{e}），下次再繼續")
    log(f"  日本限定商品資料完成 {ok} 筆，查無 {nodata} 筆")


def _shots(nid):
    """介紹頁截圖的圖檔編號（各區商店常共用同一批截圖，可用來認出同一款遊戲）。"""
    p = os.path.join(DOCS, "d", nid[-6:] + ".json")
    try:
        with open(p, encoding="utf-8") as f:
            return set(re.findall(r"/i/([0-9a-f]{40,})", " ".join(json.load(f).get("shots") or [])))
    except Exception:
        return set()


def _days(a, b):
    try:
        return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)
    except Exception:
        return 9999


LATIN_STOP = {"nintendo", "switch", "edition", "the", "for", "of", "and", "hd", "dx", "ver", "version", "2"}


def latin_key(t):
    """名稱裡的英文部分（例如「罪惡王權／Monark」→ "monark"），用來比對各區譯名不同的同一款遊戲。
    英文部分太短、或只有 Nintendo Switch 這類通用字時回傳空字串。"""
    t = unicodedata.normalize("NFKC", re.sub(r"[™®©]", "", t or "")).lower()
    words = re.findall(r"[a-z0-9]+", t)
    core = [w for w in words if w not in LATIN_STOP]
    if sum(len(w) for w in core if re.search(r"[a-z]", w)) < 4:
        return ""
    return " ".join(words)


def link_regions(games, jp):
    """台灣／港區與日本區 applicationId 不同的同一款遊戲（多數是 Switch 2 版，也有部分發行商各區分開上架），
    用下列任一條件認定為同一款（都要同平台）：
      1. 封面圖相同
      2. 名稱＋發售日都相同
      3. 介紹頁截圖有 3 張以上相同，或 2 張相同且發售日差 3 天內
      4. 名稱裡的英文部分完全相同（例如「罪惡王權／Monark」與「モナーク／Monark」），而且兩邊都只對得到這一款：
         台灣／港區只有一個名稱、日本那邊全是同一個 applicationId（本體與特典版），類型也要有重疊。
         這種多半是亞洲中文版另外上架，日本版常常沒有中文，所以會標記 jpNoZh 讓網頁註明「日區無中文」
    台灣那筆補上日本區價格，原本另外列出的「日本限定」條目併掉。對應記在 jp.json 的 link，之後每次沿用。"""
    link = jp.setdefault("link", {})          # 日本編號 → [台灣／港區編號]
    by_img, by_name, by_shot, shots, by_latin = {}, {}, {}, {}, {}
    for nid, g in games.items():
        if g.get("only") == "jp" or g.get("status") not in SELLING or g.get("jp"):
            continue
        plat = g.get("plat") or "HAC"
        if g.get("img"):
            by_img.setdefault((g["img"], plat), []).append(nid)
        if g.get("name") and g.get("rel"):
            by_name.setdefault((norm_title(g["name"]), plat, g["rel"][:10]), []).append(nid)
        if latin_key(g.get("name")):
            by_latin.setdefault((latin_key(g["name"]), plat), []).append(nid)
        shots[nid] = _shots(nid)
        for h in shots[nid]:
            by_shot.setdefault((h, plat), []).append(nid)
    jp_latin, jp_app = {}, {}
    for n, g in games.items():
        if g.get("only") == "jp" and g.get("meta") and latin_key(g.get("name")):
            jp_app[n] = (g.get("app") or n).lower()
            jp_latin.setdefault((latin_key(g["name"]), g.get("plat") or "HAC"), []).append(n)
    new = 0
    for n, g in list(games.items()):
        if g.get("only") != "jp" or not g.get("meta") or n in link:
            continue
        plat, rel = g.get("plat") or "HAC", g.get("rel") or ""
        c = set(by_img.get((g.get("img"), plat), [])) if g.get("img") else set()
        if g.get("name") and rel:
            c |= set(by_name.get((norm_title(g["name"]), plat, rel[:10]), []))
        if not c:
            cnt = {}
            for h in _shots(n):
                for x in by_shot.get((h, plat), []):
                    cnt[x] = cnt.get(x, 0) + 1
            ok = {x: v for x, v in cnt.items() if v >= 3 or (v >= 2 and _days(rel, games[x].get("rel") or "") <= 3)}
            if ok:
                top = max(ok.values())
                best = [x for x, v in ok.items() if v == top]
                if len({norm_title(games[x].get("name")) for x in best}) == 1:   # 同分但名稱不同（例如普通版／豪華版）就不猜
                    c = set(best)
        by_latin_rule, jp_side = False, []
        if not c and latin_key(g.get("name")):
            key = (latin_key(g["name"]), plat)
            tw = by_latin.get(key, [])
            jp_side = list(jp_latin.get(key, []))   # 含這次迴圈已併掉的特典版，才能把本體也併到同一筆
            gen = set(g.get("genres") or [])
            if (tw and len({norm_title(games[x].get("name")) for x in tw}) == 1
                    and len({jp_app.get(x, x) for x in jp_side}) == 1
                    and all(not gen or not games[x].get("genres") or gen & set(games[x]["genres"]) for x in tw)):
                c, by_latin_rule = set(tw), True
        ok_jp = {n} | (set(jp_side) if by_latin_rule else set())   # 同一款的本體與特典版可以都併到同一筆
        c = sorted(x for x in c if not games[x].get("jp") or games[x].get("jpId") in ok_jp)
        if not c:
            continue
        if by_latin_rule and not g.get("zh"):
            for nid in c:
                if games[nid].get("zh"):
                    games[nid]["jpNoZh"] = True
        link[n] = c
        new += 1
        for nid in c:                          # 這次就先用日本限定條目已查到的價格，下次起由 refresh_jp 查
            if g.get("jp") and not games[nid].get("jp"):
                games[nid]["jp"], games[nid]["jpId"] = g["jp"], n
        del games[n]
        for sub in ("d", "h"):                 # 舊的日本限定介紹頁／歷史頁不再使用
            f = os.path.join(DOCS, sub, n[-6:] + ".json")
            if os.path.exists(f):
                os.remove(f)
    log(f"跨區對應（applicationId 不同）：共 {len(link)} 款，本次新增 {new} 款")
    return link


def hk_dupes(games):
    """港區另外上架、但台灣區也有賣的同一款（applicationId 相同、同平台）：港區編號 → 台灣編號。
    台灣 eShop 開張後不少遊戲在台灣用新編號上架，港區仍用舊編號，查港區價格時用台灣編號查不到，
    原本會分成「台灣」「港區限定」兩筆；網站輸出時把港區價格併進台灣那筆。"""
    tw = {}
    for n, g in games.items():
        if not g.get("only") and g.get("app") and g.get("status") in SELLING:
            tw.setdefault((g["app"].lower(), g.get("plat") or "HAC"), []).append(n)
    out, used = {}, set()
    hk_items = sorted((n for n, g in games.items() if g.get("only") == "hk" and g.get("app") and g.get("status") in SELLING),
                      key=lambda n: ((games[n].get("hk") or {}).get("reg") or 1e12, n))
    for h in hk_items:
        g = games[h]
        c = [t for t in tw.get((g["app"].lower(), g.get("plat") or "HAC"), [])
             if t not in used and not (games[t].get("hk") or {}).get("reg")]
        if not c:
            continue
        c.sort(key=lambda t: (norm_title(games[t].get("name")) != norm_title(g.get("name")), t))
        out[h] = c[0]
        used.add(c[0])
    return out


def moved_ids(games, jp):
    """併掉的日本限定條目：舊網頁編號 → 現在對應的遊戲編號（台灣區優先）。"""
    out = {}
    for jn, nids in jp.get("link", {}).items():
        c = [x for x in nids if x in games]
        if c:
            out[jn[-6:]] = min(c, key=lambda x: (games[x].get("only") or "", x))[-6:]
    return out


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
    for nsuid, g in games.items():  # 發售日（近期發售分頁用）
        if g.get("meta") and "rel" not in g:
            p = os.path.join(DOCS, "d", nsuid[-6:] + ".json")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    g["rel"] = json.load(f).get("release", "")


PARTIAL_ZH = re.compile(
    r"(部分|部份)(的)?(繁體|簡體|繁簡)?中文|中文[^。\n]{0,4}(部分|部份)|僅[^。\n]{0,12}中文|只(有|包含)[^。\n]{0,12}中文"
    r"|(選單|菜單|介面|UI|說明)[^。\n]{0,12}(對應|支援|提供)[^。\n]{0,6}中文|中文[^。\n]{0,4}(介面|選單|菜單)")


def zh_code(det):
    """中文支援：1=繁體、2=簡體、4=部分中文（介紹裡寫只有選單／部分內容是中文）。"""
    langs = det.get("langs", [])
    code = (1 if any("繁體" in l or "繁体" in l for l in langs) else 0) | (2 if any("簡體" in l or "簡体" in l for l in langs) else 0)
    text = (det.get("desc") or "") + "\n" + (det.get("catch") or "")
    if PARTIAL_ZH.search(text):
        code |= 4
    return code


def backfill_zh(games):
    """用已存的 d/<編號>.json 重新判斷中文支援（含「部分中文」）。"""
    for nsuid, g in games.items():
        if not g.get("meta") or g.get("zv") == 2:
            continue
        p = os.path.join(DOCS, "d", nsuid[-6:] + ".json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                g["zh"] = zh_code(json.load(f))
            g["zv"] = 2


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
FX_CODES = ("HKD", "JPY", "USD")


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


# ───────────── 成人內容：不公開（不放進網站資料；編號記在 data/adult_ids.json，之後每次都排除） ─────────────
ADULT_NAME = re.compile(r"hentai|\bnude|naked|erotic|ecchi|waifu|sex(?:y)?[ -]*(?:girls?|women|ladies|babes|puzzle|slave|date)|"
                        r"エロ|ヌード|18禁|R-?18|成人向|セクシー|えっち|エッチ|裸|情色|色情|成人遊戲", re.I)
ADULT_DESC = re.compile(r"裸露|裸體|性愛|色情|成人向|情色|性行為|nudity|sexual content|erotic|hentai|エロ|18禁|性的表現|ヌード|性的な", re.I)
ADULT_RATING = {"限制級", "Z", "CLASSⅢ", "CLASSIII", "18+", "R18+", "R18"}


def adult_flag(g, det):
    nm = g.get("name") or ""
    if ADULT_NAME.search(nm):
        return 1
    rt = re.sub(r"\s+", "", str(det.get("rating") or "")).upper()
    if rt in ADULT_RATING and ADULT_DESC.search((det.get("catch") or "") + " " + (det.get("desc") or "")):
        return 1
    return 0


def pack_rows(rows, fields):
    """首頁資料瘦身：改成「欄式」格式（每個欄位一整串，壓縮效果更好）。
    id 只存與前一筆的差值、jpId 去掉共同前綴 70010000、遊戲類型改存編號表；
    圖片雜湊（最大宗）拆到 img.json，網頁先顯示清單、圖片之後再補上。
    回傳 (data.json 的欄位們, 圖片雜湊串列)。"""
    cols = {k: [r[i] for r in rows] for i, k in enumerate(fields)}
    ids = [int(x) for x in cols["id"]]
    cols["id"] = ([ids[0]] + [b - a for a, b in zip(ids, ids[1:])]) if ids else []
    cols["jpId"] = [(x[8:] if x and x.startswith("70010000") else x) for x in cols["jpId"]]
    table = sorted({g for gs in cols["genres"] for g in gs})
    gi = {g: i for i, g in enumerate(table)}
    cols["genres"] = [[gi[g] for g in gs] for gs in cols["genres"]]
    img = cols.pop("img")
    keep = [k for k in fields if k != "img"]
    return {"fields": keep, "genres": table, "cols": [cols[k] for k in keep]}, img


def build_site(games, dlcs, hist, hist_hk, asof, fx=None, hist_jp=None, moved=None):
    backfill_filters(games)
    backfill_zh(games)
    # 成人內容整個排除：名稱／分級判斷到的編號會累積記在 data/adult_ids.json（網站不公開），之後每次都不輸出
    ap = os.path.join(DATA, "adult_ids.json")
    adult_ids = set()
    if os.path.exists(ap):
        try:
            with open(ap, encoding="utf-8") as f:
                adult_ids = set(json.load(f))
        except Exception:
            adult_ids = set()
    for nsuid, g in games.items():
        if g.get("status") not in SELLING or nsuid[-6:] in adult_ids:
            continue
        det = {}
        dp = os.path.join(DOCS, "d", nsuid[-6:] + ".json")
        if os.path.exists(dp):
            try:
                with open(dp, encoding="utf-8") as f:
                    det = json.load(f)
            except Exception:
                det = {}
        if adult_flag(g, det):
            adult_ids.add(nsuid[-6:])
    save(ap, sorted(adult_ids))
    removed = 0
    for i6 in adult_ids:  # 清掉已公開的詳細資料檔
        for rel in (os.path.join("d", i6 + ".json"),):
            fp = os.path.join(DOCS, rel)
            if os.path.exists(fp):
                os.remove(fp); removed += 1
    adult_full = {n for n in games if n[-6:] in adult_ids}
    log(f"成人內容排除：{len(adult_ids)} 款（清掉公開檔 {removed} 個）")
    dlc_count = {}
    for d in dlcs.values():
        if d.get("status") in SELLING and d.get("parent"):
            dlc_count[d["parent"]] = dlc_count.get(d["parent"], 0) + 1
    hk2tw = hk_dupes(games)
    tw2hk = {t: h for h, t in hk2tw.items()}
    moved = dict(moved or {})
    moved.update({h[-6:]: t[-6:] for h, t in hk2tw.items()})
    log(f"港區另外上架、併進台灣區的遊戲：{len(hk2tw)} 款")
    rows = []
    for nsuid, g in games.items():
        if g.get("status") not in SELLING or nsuid in adult_full or nsuid in hk2tw:
            continue
        low, low_date, n = low_info(hist.get(nsuid, []))
        hk = g.get("hk") or {}
        if nsuid in tw2hk:                       # 港區價格、日本對應都用港區那筆
            hg = games[tw2hk[nsuid]]
            hk = hg.get("hk") or {}
            if not g.get("jp") and hg.get("jp"):
                g = {**g, "jp": hg["jp"], "jpId": hg.get("jpId"), "jpNoZh": hg.get("jpNoZh")}
        rows.append([nsuid[-6:], g.get("name", ""), 1 if g.get("plat") == "BEE" else 0, g.get("img", ""),
                     g.get("reg"), g.get("sale"), (g.get("end") or "")[:10], low, low_date,
                     n, STATUS_CODE[g["status"]], g.get("first_seen", ""), 1 if g.get("meta") else 0,
                     g.get("genres", []), g.get("zh", 0), g.get("pmax", 0), dlc_count.get(nsuid, 0) + dlc_count.get(tw2hk.get(nsuid), 0),
                     hk.get("reg"), hk.get("sale"), (g.get("jp") or {}).get("reg"), (g.get("jp") or {}).get("sale"),
                     g.get("jpId", "") if g.get("jp") else "", (g.get("rel") or "")[:10], g.get("demo", 0), g.get("only", ""),
                     1 if g.get("jpNoZh") and g.get("jp") else 0])
    rows.sort(key=lambda r: r[0])
    packed, img = pack_rows(rows, ["id", "name", "s2", "img", "reg", "sale", "end", "low", "lowDate", "nPrices", "status",
                                   "firstSeen", "hasDetail", "genres", "zh", "pmax", "dlcN", "hkReg", "hkSale",
                                   "jpReg", "jpSale", "jpId", "rel", "demo", "only", "jpNz"])
    save(os.path.join(DOCS, "data.json"), {
        "v": 2,
        "asof": asof,
        "fx": fx or {},
        "moved": moved or {},   # 併掉的舊編號 → 新編號（網頁會把收藏、已購買等搬過去）
        **packed})
    save(os.path.join(DOCS, "img.json"), {"v": 2, "asof": asof, "img": img})   # 與 data.json 同一個順序

    drows = []
    for nsuid, d in dlcs.items():
        if d.get("status") not in SELLING or (d.get("parent") or "")[-6:] in adult_ids:
            continue
        low, low_date, n = low_info(hist.get(nsuid, []))
        hk = d.get("hk") or {}
        drows.append([nsuid, d.get("name", ""), hk2tw.get(d.get("parent", ""), d.get("parent", ""))[-6:], d.get("img", ""),
                      d.get("reg"), d.get("sale"), (d.get("end") or "")[:10], low, low_date, n,
                      STATUS_CODE[d["status"]], d.get("first_seen", ""), 1 if d.get("mem") else 0,
                      hk.get("reg"), hk.get("sale"), (d.get("date") or "")[:10]])
    drows.sort(key=lambda r: r[0])
    save(os.path.join(DOCS, "dlc.json"), {
        "asof": asof,
        "fields": ["id", "name", "parent", "img", "reg", "sale", "end", "low", "lowDate", "nPrices",
                   "status", "firstSeen", "membersOnly", "hkReg", "hkSale", "rel"],
        "rows": drows})

    # 每個商品一個價格歷史檔，給價格走勢圖用（只在有變動時寫入）
    hdir = os.path.join(DOCS, "h")
    os.makedirs(hdir, exist_ok=True)
    changed = 0
    hist_jp = hist_jp or {}
    jp_only = {n for n, g in games.items() if g.get("only") == "jp" and hist_jp.get(n)}
    for nsuid in set(hist) | set(hist_hk) | jp_only | set(tw2hk):
        if nsuid[-6:] in adult_ids:
            hp = os.path.join(hdir, nsuid + ".json")
            if os.path.exists(hp):
                os.remove(hp)
            continue
        obj = {"tw": hist.get(nsuid, []), "hk": hist_hk.get(nsuid, []) or hist_hk.get(tw2hk.get(nsuid), [])}
        jid = (games.get(nsuid) or {}).get("jpId") or (games.get(tw2hk.get(nsuid)) or {}).get("jpId")
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
SITE = "https://dealradar-tw.github.io/eshop-radar/"


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
    refresh_movies(games)
    if args.full:
        refresh_all_dlc_lists(games, dlcs)
    refresh_dlc_prices({k: v for k, v in dlcs.items() if "status" not in v})  # 這次新發現的 DLC
    refresh_hk_prices({**games, **dlcs})
    backfill_app_ids(games)
    jp = load("jp.json", {})
    refresh_jp(games, jp)
    refresh_jponly(games, jp)
    refresh_meta_jponly(games, 6000)
    link_regions(games, jp)
    refresh_hot(games, jp)
    refresh_zhnames(games)

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
    build_site(games, dlcs, hist, hist_hk, asof, cat["fx"], hist_jp, moved_ids(games, jp))
    log(f"今日降價：{len(drops)} 筆")
    notify(items, drops, cat.get("fx") or {})


TODAY = ""
if __name__ == "__main__":
    sys.exit(main())

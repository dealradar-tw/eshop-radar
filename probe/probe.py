#!/usr/bin/env python3
"""測試任天堂商店的 GraphQL 介面（只讀取公開資料），結果寫到 probe/out/gql.txt。"""
import json, os, urllib.request, urllib.error
URL = "https://wb.lp1.savanna.srv.nintendo.net/graphql"
H = {"content-type": "application/json", "accept": "application/json",
     "X-Nintendo-Savanna-Client-Id": "042e4bd1f0eec144167dbc0c63f1d17876e7b9ec322713b613614214e3675df9",
     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Origin": "https://ec.nintendo.com", "Referer": "https://ec.nintendo.com/"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "gql.txt")
DLC = """query DlcItemList($shopId: Int!, $country: CountryCode!, $language: LanguageCode!, $applicationNsUid: NsUid!, $offset: Int!, $limit: Int!)
@inContext(shopId: $shopId, country: $country, language: $language) {
  item { dlcItemsByApplicationItem(nsUid: $applicationNsUid, offset: $offset, limit: $limit, publicStatuses: [PUBLIC], salesStatuses: [ONSALE, PRE_ORDER], isIncludeBundleItem: true) {
    items { __typename ... on DlcItem { nsUid formalName labelPlatform heroBannerUrl releaseDateOnEshop isMembershipRequired } }
    offsetInfo { total offset } } } }"""
APP = """query App($shopId: Int!, $country: CountryCode!, $language: LanguageCode!, $nsUid: NsUid!)
@inContext(shopId: $shopId, country: $country, language: $language) {
  item { applicationItem(nsUid: $nsUid) { nsUid formalName labelPlatform heroBannerUrl releaseDateOnEshop catchCopy description
    publisher { name } screenshotUrls movies { ... on YoutubeMovie { youtubeId } } languages { name } playerNumber { offlineMax localMax onlineMax }
    dlcItems { offsetInfo { total } } } } }"""
out = []
def q(label, query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(URL, data=body, headers=H), timeout=30) as r:
            txt = r.read().decode("utf-8", "replace")
            out.append(f"## {label} HTTP {r.status}\n{txt[:1500]}\n" + (f"ITEMS={txt.count(chr(34)+'nsUid'+chr(34))}\n"))
    except urllib.error.HTTPError as e:
        out.append(f"## {label} HTTP {e.code}\n{e.read().decode('utf-8','replace')[:2000]}\n")
    except Exception as e:
        out.append(f"## {label} ERR {e}\n")
base = {"shopId": 3, "language": "zh"}
def app(fields):
    return """query App($shopId: Int!, $country: CountryCode!, $language: LanguageCode!, $nsUid: NsUid!)
@inContext(shopId: $shopId, country: $country, language: $language) { item { applicationItem(nsUid: $nsUid) { """ + fields + """ } } }"""
for label, f in [("genre", "genre"), ("genres", "genres { name }"), ("tags", "tags { subCategory { categoryId name } }"),
                 ("playStyles", "playStyles { name }"), ("romSizes", "romSizes { hac { totalRomSize { rawValue } } bee { totalRomSize { rawValue } } }"),
                 ("players6", "playerNumber { offlineMin offlineMax localMin localMax onlineMin onlineMax }"),
                 ("rating", "ratingInfo { rating { name age } }"), ("apps", "applications { id }")]:
    q("APP 027622 " + label, app(f), {**base, "country": "TW", "nsUid": 70010000027622})
multi = """query M($shopId: Int!, $country: CountryCode!, $language: LanguageCode!)
@inContext(shopId: $shopId, country: $country, language: $language) { item {
 a: applicationItem(nsUid: 70010000027622) { nsUid formalName }
 b: applicationItem(nsUid: 70010000102139) { nsUid formalName }
 c: applicationItem(nsUid: 70010000999999) { nsUid formalName } } }"""
q("MULTI alias", multi, {**base, "country": "TW"})
open(OUT, "w", encoding="utf-8").write("\n".join(out))
print("\n".join(o[:400] for o in out))

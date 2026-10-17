import json,re,urllib.request,urllib.error,random,glob
UA={"User-Agent":"Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"}
H={"content-type":"application/json","accept":"application/json","X-Nintendo-Savanna-Client-Id":"042e4bd1f0eec144167dbc0c63f1d17876e7b9ec322713b613614214e3675df9","Origin":"https://ec.nintendo.com","Referer":"https://ec.nintendo.com/",**UA}
G="https://wb.lp1.savanna.srv.nintendo.net/graphql"
def gq(q,v):
    try:
        r=urllib.request.Request(G,data=json.dumps({"query":q,"variables":v}).encode(),headers=H)
        return json.loads(urllib.request.urlopen(r,timeout=40).read())
    except urllib.error.HTTPError as e: return {"http":e.code,"body":e.read().decode()[:300]}
    except Exception as e: return {"exc":str(e)}
Q="query Q($shopId: Int!, $country: CountryCode!, $language: LanguageCode!) @inContext(shopId: $shopId, country: $country, language: $language) { item { %s } }"
ids=[f.split('/')[-1][:-5] for f in sorted(glob.glob('docs/d/*.json'))]
random.seed(3); samp=random.sample(ids,60)
for cty in ["TW","HK"]:
    V={"shopId":3,"country":cty,"language":"zh"}
    f=" ".join(f"a{i}: applicationItem(nsUid: 70010000{n}) {{ nsUid formalName movies {{ __typename ... on YoutubeMovie {{ youtubeId }} }} }}" for i,n in enumerate(samp))
    r=gq(Q%f,V); item=((r.get("data") or {}).get("item") or {})
    print(cty,"keys",list(r.keys()),str(r)[:200] if not item else "")
    types={}
    for k,a in item.items():
        for m in (a or {}).get("movies") or []:
            types[m.get("__typename")]=types.get(m.get("__typename"),0)+1
    print(cty,"types",types, "with movies",sum(1 for a in item.values() if a and a.get("movies")))
# 網頁
for n in samp[:6]:
    for u in [f"https://ec.nintendo.com/tw/zh/titles/70010000{n}"]:
        try:
            h=urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=40).read().decode("utf-8","replace")
            print(n,len(h),{k:len(re.findall(k,h)) for k in ["youtube","youtu.be","\\.mp4","m3u8","movie","Movie","video"]})
            for m in re.finditer(r'.{80}(?:[Mm]ovie|\.mp4|m3u8).{120}',h):
                print("   ",m.group(0)[:230]); break
        except Exception as e: print(n,"ERR",e)

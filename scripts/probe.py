import json,re,urllib.request,urllib.error,random
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
d=json.load(open('docs/data.json')); ix={k:i for i,k in enumerate(d['fields'])}
han=re.compile(r'[一-鿿]'); kana=re.compile(r'[぀-ヿ]')
rows=d['rows']; random.seed(5)
def pick(pred,n):
    c=[r for r in rows if r[ix['name']] and pred(r)]; return random.sample(c,min(n,len(c)))
sets={"tw_latin":pick(lambda r:not r[ix['only']] and not han.search(r[ix['name']]) and not kana.search(r[ix['name']]),40),
      "hk_latin":pick(lambda r:r[ix['only']]=='hk' and not han.search(r[ix['name']]) and not kana.search(r[ix['name']]),40),
      "jp_only":pick(lambda r:r[ix['only']]=='jp',40)}
CTX=[("TW","zh"),("TW","en"),("TW","ja"),("HK","zh"),("HK","en"),("JP","ja"),("JP","zh"),("JP","en"),("KR","ko"),("US","en")]
for sn,S in sets.items():
    ids=["70010000"+r[0] if sn!="jp_only" else r[ix['jpId']] for r in S]
    ids=[i for i in ids if i]
    print("==",sn,len(ids))
    res={}
    for cty,lang in CTX:
        f=" ".join(f"a{i}: applicationItem(nsUid: {n}) {{ formalName }}" for i,n in enumerate(ids))
        r=gq(Q%f,{"shopId":3,"country":cty,"language":lang})
        item=((r.get("data") or {}).get("item") or {})
        if not item: print(cty,lang,"FAIL",str(r)[:200]); continue
        res[(cty,lang)]=[(item.get(f"a{i}") or {}).get("formalName") for i in range(len(ids))]
    cnt={k:sum(1 for x in v if x and han.search(x)) for k,v in res.items()}
    print("han counts",cnt)
    for i in range(min(12,len(ids))):
        print(S[i][ix['name']][:30],"|"," | ".join(f"{k[0]}{k[1]}:{(v[i] or '-')[:26]}" for k,v in res.items() if k in [("TW","zh"),("HK","zh"),("JP","zh"),("TW","ja"),("JP","ja")]))

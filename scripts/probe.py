import json,urllib.request,urllib.error
UA={"User-Agent":"Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"}
def get(u,h=None,data=None,n=1500):
    try:
        r=urllib.request.Request(u,data=data,headers={**UA,**(h or {})})
        with urllib.request.urlopen(r,timeout=30) as x:
            b=x.read().decode("utf-8","replace"); print("OK",x.status,u,len(b)); print(b[:n]); return b
    except urllib.error.HTTPError as e:
        print("ERR",e.code,u,e.read().decode("utf-8","replace")[:300])
    except Exception as e: print("EXC",u,e)
    print("-----")
for kind in ["ranking","popular","sales","new","recommend","search/ranking","search/popular","search/sales","search/new","search/popularity"]:
    for p in ["search/"+kind if not kind.startswith("search/") else kind]:
        get(f"https://ec.nintendo.com/api/TW/zh/{p}?count=5&offset=0",n=600)
for q in ["https://ec.nintendo.com/api/TW/zh/search/sales?count=5&offset=0&sort=popular",
          "https://ec.nintendo.com/api/TW/zh/search?count=5&sort=popular",
          "https://ec.nintendo.com/TW/zh/search?sort=popular"]:
    get(q,n=800)
H={"content-type":"application/json","accept":"application/json","X-Nintendo-Savanna-Client-Id":"042e4bd1f0eec144167dbc0c63f1d17876e7b9ec322713b613614214e3675df9","Origin":"https://ec.nintendo.com","Referer":"https://ec.nintendo.com/"}
G="https://wb.lp1.savanna.srv.nintendo.net/graphql"
def gq(q,v=None):
    print("GQL",q[:150]); return get(G,H,json.dumps({"query":q,"variables":v or {}}).encode(),n=3000)
gq("{ __schema { queryType { name fields { name args { name type { name kind } } type { name kind } } } } }")
gq("{ __type(name:\"Item\") { name kind possibleTypes { name } fields { name } } }")
gq("{ __type(name:\"SortOrder\") { enumValues { name } } }")
gq("{ __schema { types { name kind } } }")

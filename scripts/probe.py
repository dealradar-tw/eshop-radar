import json,re,urllib.request,urllib.parse,unicodedata,time
UA={"User-Agent":"eshop-radar/1.0 (https://github.com/jackie-1002/eshop-radar)","Accept":"application/sparql-results+json"}
def sparql(q):
    u="https://query.wikidata.org/sparql?format=json&query="+urllib.parse.quote(q)
    for a in range(3):
        try: return json.loads(urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=120).read())["results"]["bindings"]
        except Exception as e: print("ERR",e); time.sleep(10)
    return []
Q='''SELECT ?g ?en ?ja ?zh ?zhl WHERE { ?g wdt:P400 wd:Q19610114 .
 ?g rdfs:label ?zh . FILTER(LANG(?zh) IN ("zh-hant","zh-tw","zh-hk","zh")) BIND(LANG(?zh) AS ?zhl)
 OPTIONAL{?g rdfs:label ?en FILTER(LANG(?en)="en")} OPTIONAL{?g rdfs:label ?ja FILTER(LANG(?ja)="ja")} }'''
b=sparql(Q); print("rows",len(b))
def N(s): 
    s=unicodedata.normalize("NFKC",s).lower(); return re.sub(r"[\W_]+","",s)
m={}
for r in b:
    zh=r["zh"]["value"]
    for k in ("en","ja"):
        if k in r: m.setdefault(N(r[k]["value"]),set()).add(zh)
print("keys",len(m))
d=json.load(open('docs/data.json')); ix={k:i for i,k in enumerate(d['fields'])}
han=re.compile(r'[一-鿿]')
tot=hit=0; ex=[]
for r in d['rows']:
    n=r[ix['name']]
    if not n or (han.search(n) and not re.search(r'[぀-ヿ]',n)): continue
    tot+=1
    z=m.get(N(re.sub(r'[（(].*?[)）]','',n)))
    if z: hit+=1; ex.append((n,list(z)[:2])) 
print("candidates",tot,"hit",hit)
for e in ex[:60]: print(e)
print([ (r["zh"]["value"],r["zhl"]["value"],r.get("en",{}).get("value")) for r in b[:15]])

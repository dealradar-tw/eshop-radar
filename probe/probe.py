import urllib.request, os
os.makedirs("probe/out", exist_ok=True)
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36","Accept-Language":"ja"}
for name,u in [("rank","https://www.famitsu.com/ranking/game-sales/")]:
    try:
        r=urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30).read()
        open(f"probe/out/{name}.html","wb").write(r); print(name,len(r))
    except Exception as e: print(name,"ERR",e)

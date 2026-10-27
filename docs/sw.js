// 台區特價雷達：離線快取
// 頁面與價格資料：先上網拿最新的，沒網路時用上次存的；圖示與遊戲圖片：先用存的。
const VER = "v110";
const CORE = ["./", "./index.html", "./manifest.webmanifest", "./icons/icon-192.png"];
self.addEventListener("install", e => {
  e.waitUntil(caches.open("core-" + VER).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => !k.endsWith(VER)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
async function trimImages(){
  const c = await caches.open("img-" + VER), ks = await c.keys();
  for (const k of ks.slice(0, Math.max(0, ks.length - 400))) await c.delete(k);
}
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.hostname === "img-eshop.cdn.nintendo.net") {
    e.respondWith(caches.open("img-" + VER).then(async c => {
      const hit = await c.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok || res.type === "opaque") { c.put(req, res.clone()); trimImages(); }
      return res;
    }));
    return;
  }
  if (url.origin !== location.origin) return;
  if (url.pathname.includes("/icons/")) {
    e.respondWith(caches.match(req).then(h => h || fetch(req)));
    return;
  }
  // 頁面與資料一律向伺服器確認最新版（避開瀏覽器自己的 10 分鐘快取）
  e.respondWith(fetch(req.url, {cache: "no-cache", credentials: "same-origin"}).then(res => {
    if (res.ok) { const copy = res.clone(); caches.open("data-" + VER).then(c => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req, {ignoreSearch: true}).then(h => h || caches.match("./index.html"))));
});

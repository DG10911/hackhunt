/* HackHunt service worker — offline shell + fast loads */
const CACHE = "hackhunt-v1";
const SHELL = ["/", "/index.html", "/icon-192.png", "/icon-512.png", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // never cache API calls — always fresh data, fall back gracefully offline
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/")) {
    e.respondWith(fetch(req).catch(() => new Response("{}", { headers: { "Content-Type": "application/json" } })));
    return;
  }
  // app shell + assets: cache-first, then network, and refresh cache in background
  e.respondWith(
    caches.match(req).then((cached) => {
      const net = fetch(req)
        .then((res) => {
          if (res && res.status === 200 && url.origin === location.origin) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => cached);
      return cached || net;
    })
  );
});

/*
  GreenCart service worker  (makes the site installable as an app)

  - Pictures, CSS and JS are cached, so repeat visits are fast.
  - Pages are NEVER cached: carts, orders and logins are personal and must
    always be fresh. If the network is down, a friendly offline page shows.
*/
const VERSION = "greencart-v2";
const SHELL = ["/offline", "/assets/store.css", "/assets/store.js", "/assets/icons/icon-192.png"];

self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") return;                    // forms always go to the network
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return;

    // pages: network only, offline page as a fallback
    if (request.mode === "navigate") {
        event.respondWith(fetch(request).catch(() => caches.match("/offline")));
        return;
    }

    // our own static files: show the cached copy at once, refresh it in the background
    if (url.pathname.startsWith("/assets/") || url.pathname === "/style.css") {
        event.respondWith(
            caches.open(VERSION).then((cache) =>
                cache.match(request).then((cached) => {
                    const fresh = fetch(request).then((response) => {
                        if (response.ok) cache.put(request, response.clone());
                        return response;
                    }).catch(() => cached);
                    return cached || fresh;
                })
            )
        );
    }
});

const CACHE_NAME = "codex-relay-cloud-v17";
const ASSETS = [
  "/relay-chat.html",
  "/relay-chat.css",
  "/relay-chat.js",
  "/relay-chat.webmanifest",
  "/icon.svg",
  "/pairing.html",
  "/pairing.js",
  "/vendor/qrcode-generator.js"
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== location.origin || url.pathname.startsWith("/api/")) return;

  if (event.request.mode === "navigate") {
    event.respondWith(networkFirst(event.request, "/relay-chat.html"));
    return;
  }

  if (ASSETS.includes(url.pathname)) {
    event.respondWith(networkFirst(event.request, url.pathname));
  }
});

async function networkFirst(request, fallbackKey) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(fallbackKey, response.clone());
    return response;
  } catch (_error) {
    return caches.match(request) || caches.match(fallbackKey);
  }
}

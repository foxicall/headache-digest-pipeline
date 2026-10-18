"use strict";
// Version the schema validator in both the reader and worker so HTTP caches
// cannot combine the previous validator with the new JSON schema.
importScripts("./assets/guidelines.js?v=14");

// Scope-specific cache names keep different GitHub Pages project sites isolated.
const PREFIX = `headache-digest:${self.registration.scope}:`;
const CACHE = `${PREFIX}v14`;
const SHELL = [
  "./",
  "./index.html",
  "./assets/styles.css",
  "./assets/tailwind.css",
  "./assets/app.js",
  "./assets/paper-filters.js",
  "./assets/reading-library.js",
  "./assets/guidelines.js?v=14",
  "./assets/guidelines.css?v=14",
  "./assets/icon.svg",
  "./assets/icon-192.png",
  "./assets/icon-512.png",
  "./manifest.webmanifest",
];
const DATA_URL = new URL("./data/headache_papers.json", self.registration.scope)
  .href;
const METRICS_URL = new URL("./data/journal_metrics.json", self.registration.scope).href;
const SYNDROMES_URL = new URL("./data/headache_syndromes.json", self.registration.scope).href;
const GUIDELINES_URL = new URL("./data/headache_guidelines.json", self.registration.scope).href;
const SHELL_URLS = new Set(
  SHELL.map((path) => new URL(path, self.registration.scope).href),
);

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith(PREFIX) && key !== CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  // Navigation requests can retain #guidelines/#library in a service worker.
  // They identify a section of the same cached document, not another resource.
  url.hash = "";
  const isData = [DATA_URL, METRICS_URL, SYNDROMES_URL, GUIDELINES_URL].includes(url.href);
  if (!isData && !SHELL_URLS.has(url.href)) return;
  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE);
      try {
        // Network-first keeps the repository's daily JSON and UI fresh.
        const response = await fetch(event.request);
        if (!response.ok) throw new Error("Network response failed");
        if (isData) {
          const payload = await response.clone().json();
          if (url.href === GUIDELINES_URL) self.DigestGuidelines.validate(payload);
          if (url.href === DATA_URL ? !Array.isArray(payload)
              : url.href === METRICS_URL ? payload?.metric !== "Journal Impact Factor"
              : url.href === GUIDELINES_URL ? payload?.schema_version !== 2 || !Array.isArray(payload?.comparisons) || !Array.isArray(payload?.sources)
              : !Array.isArray(payload?.entries) || !Array.isArray(payload?.groups))
            throw new Error("Invalid data response");
        }
        await cache.put(url.href, response.clone());
        return response;
      } catch {
        const cached = await cache.match(url.href);
        if (!cached)
          return new Response("Offline and no cached copy is available", {
            status: 503,
          });
        if (!isData) return cached;
        const headers = new Headers(cached.headers);
        headers.set("X-Digest-Cache", "offline");
        return new Response(await cached.blob(), { status: 200, headers });
      }
    })(),
  );
});

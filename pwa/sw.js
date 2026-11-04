/**
 * Service Worker: makes the demo work with the phone offline.
 *
 * What a PWA buys us here, concretely: after one visit over HTTPS (or
 * localhost), every file the page needs is already on the phone. The PC can be
 * switched off, the hotspot can be gone, and the page still opens and still
 * classifies -- which is the "端侧" claim the project makes.
 *
 * It only runs in a secure context (https:, or localhost / 127.0.0.1). On a
 * plain http:// LAN address the browser refuses to register it, by design; the
 * page says so instead of pretending to be installable.
 *
 * Cache strategy: precache everything on install, then cache-first with a
 * network fallback. The whole payload is ~1.6 MB and the model is frozen, so
 * there is nothing to gain from revalidating; correctness comes from bumping
 * CACHE_VERSION when files change (tests/node/pwa-page.test.mjs enforces that
 * the list covers every file the page fetches).
 */

const CACHE_VERSION = "pcg-demo-v2";
const CACHE = `pcg-demo-${CACHE_VERSION}`;

// Everything the page asks for. Missing one entry means that file silently
// needs the network after install, so this list is kept in sync by a test.
const PRECACHE = [
  "./",
  "./index.html",
  "./app.js",
  "./diag.js",
  "./pwa.js",
  "./preprocess.js",
  "./wav.js",
  "./filters/resample-poly.js",
  "./manifest.json",
  "./vendor/tf.min.js",
  "./model/model.json",
  "./model/group1-shard1of1.bin",
  "./model/reference.json",
  "./model/New_AS_001.wav",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  // Labelled clips for the one-tap demo (see tools/ai/make_demo_samples.py).
  "./samples/index.json",
  "./samples/AS_001.wav",
  "./samples/AS_002.wav",
  "./samples/MS_001.wav",
  "./samples/MS_002.wav",
  "./samples/MR_001.wav",
  "./samples/MR_002.wav",
  "./samples/MVP_001.wav",
  "./samples/MVP_002.wav",
  "./samples/N_001.wav",
  "./samples/N_002.wav",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;   // never touch cross-origin

  event.respondWith(
    caches.match(request, { ignoreSearch: true }).then((hit) => {
      if (hit) return hit;
      return fetch(request)
        .then((response) => {
          // Cache successful, same-origin, complete responses only.
          if (response.ok && response.type === "basic") {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() =>
          // Offline and never cached: fall back to the shell, which can at
          // least explain itself instead of showing the browser error page.
          caches.match("./index.html"),
        );
    }),
  );
});

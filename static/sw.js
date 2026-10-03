/* Receipt Reader service worker: makes the site installable and keeps the shell fast.
   It never caches pages (they are per-user and behind login) - only static assets. */
const CACHE = 'rr-static-v1';
const PRECACHE = ['/static/css/app.css', '/static/icons/icon-192.png', '/offline'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // Static assets: serve the cached copy instantly, refresh it in the background.
  if (url.origin === self.location.origin && url.pathname.startsWith('/static/')) {
    event.respondWith(caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(req);
      const network = fetch(req)
        .then((res) => { if (res.ok) cache.put(req, res.clone()); return res; })
        .catch(() => cached);
      return cached || network;
    }));
    return;
  }

  // Pages: always the network; a friendly page if the phone has no signal.
  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(() => caches.match('/offline')));
  }
});

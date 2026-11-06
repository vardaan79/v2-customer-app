/* V2 Retail Customer App — service worker.
   Served from / so its scope is the whole app.

   Strategy by request type:
     app shell (HTML/CSS/JS/icons) : cache-first, refreshed in the background
     API GETs                      : network-first, fall back to the last good
                                     response so an installed app still opens
                                     your bills on a dead train
     API writes (POST/DELETE)      : never cached, never queued — a profile save
                                     or a nudge read must hit the server for real
*/
const VERSION = 'v2app-5';
const SHELL = `shell-${VERSION}`;
const DATA = `data-${VERSION}`;

const SHELL_URLS = [
  '/',
  '/offline',
  '/static/styles.css',
  '/static/app.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL)
      // addAll is all-or-nothing; one 404 would leave the app with no shell
      .then(c => Promise.allSettled(SHELL_URLS.map(u => c.add(u))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== SHELL && k !== DATA).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isApi(url) {
  return url.pathname.startsWith('/api/');
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // only handle our own origin; never touch admin or non-GET traffic
  if (url.origin !== self.location.origin) return;
  if (request.method !== 'GET') return;
  if (url.pathname.startsWith('/admin') || url.pathname.startsWith('/api/admin')) return;

  if (isApi(url)) {
    event.respondWith(
      fetch(request)
        .then((res) => {
          // Only cache a shopper's own data when it came back clean. An error
          // body cached here would be served as if it were their bills.
          if (res.ok) {
            const copy = res.clone();
            caches.open(DATA).then(c => c.put(request, copy));
          }
          return res;
        })
        .catch(() => caches.match(request).then(hit => hit || Response.json(
          { ok: false, error: 'You appear to be offline. Showing what we last loaded.' },
          { status: 503 })))
    );
    return;
  }

  // navigations: network first so a deployed change lands, offline page as backstop
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone();
          caches.open(SHELL).then(c => c.put('/', copy));
          return res;
        })
        .catch(() => caches.match('/').then(hit => hit || caches.match('/offline')))
    );
    return;
  }

  // static assets: cache first, revalidate quietly
  event.respondWith(
    caches.match(request).then((hit) => {
      const net = fetch(request).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(SHELL).then(c => c.put(request, copy));
        }
        return res;
      }).catch(() => hit);
      return hit || net;
    })
  );
});

// lets the page clear a stale shopper's cached data on log out
self.addEventListener('message', (event) => {
  if (event.data === 'clear-data-cache') caches.delete(DATA);
  if (event.data === 'skip-waiting') self.skipWaiting();
});

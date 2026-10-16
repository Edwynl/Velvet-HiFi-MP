// VELVET - Service Worker for PWA
const CACHE_NAME = 'velvet-v6';
const STATIC_ASSETS = [
  '/static/index.html',
  '/static/manifest.json',
  '/static/icon.png',
  '/static/js/library-views.js',
  '/static/js/settings-view.js',
  '/static/js/player-ui.js',
  '/static/js/library-cards.js',
  '/static/js/dsp-controls.js',
  '/static/js/remote-settings.js'
];

// Separate cache for thumbnails to avoid polluting main cache
const THUMB_CACHE_NAME = 'velvet-thumbs-v5';
const COVER_CACHE_NAME = 'velvet-covers-v3';
const MAX_THUMBS = 400;
const MAX_COVERS = 80;
const MAX_THUMB_BYTES = 24 * 1024 * 1024;
const MAX_COVER_BYTES = 40 * 1024 * 1024;

// Install event - cache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(async cache => {
        for (const asset of STATIC_ASSETS) {
          const response = await fetch(asset);
          if (canCache(response)) await cache.put(asset, response);
        }
      })
      .then(() => self.skipWaiting())
  );
});

// Activate event - clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => {
        return Promise.all(
          keys
            .filter(key => key !== CACHE_NAME && key !== THUMB_CACHE_NAME && key !== COVER_CACHE_NAME)
            .map(key => caches.delete(key))
        );
      })
      .then(() => self.clients.claim())
  );
});

// Cover caching respects both record count and the total bytes on disk.
function canCache(response) {
  return response.ok && response.status !== 206 && response.type === 'basic' &&
    !/\bno-store\b/i.test(response.headers.get('Cache-Control') || '');
}

async function cacheResponseBytes(response) {
  if (!response) return 0;
  const header = response.headers.get('Content-Length');
  if (header !== null && /^\d+$/.test(header)) return Number(header);
  // Streams can be chunked: measure a clone rather than consuming the response.
  try {
    return (await response.clone().arrayBuffer()).byteLength;
  } catch {
    return Infinity;
  }
}

async function trimCache(cacheName, maxItems, maxBytes) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  const entries = [];
  let total = 0;
  for (const request of keys) {
    const bytes = await cacheResponseBytes(await cache.match(request));
    entries.push({ request, bytes });
    total += bytes;
  }
  let remaining = entries.length;
  for (const item of entries) {
    if (remaining <= maxItems && total <= maxBytes) break;
    await cache.delete(item.request);
    remaining -= 1;
    total -= item.bytes;
  }
}

async function putBoundedCover(cacheName, request, response, maxItems, maxBytes) {
  const url = new URL(request.url);
  // Never persist tickets or credentials that are encoded in URLs.
  if (!canCache(response) || url.searchParams.has('ticket') ||
      url.searchParams.has('api_key') || url.searchParams.has('password')) return;
  const bytes = await cacheResponseBytes(response);
  if (!Number.isFinite(bytes) || bytes > maxBytes) return;
  const cache = await caches.open(cacheName);
  await cache.put(request, response.clone());
  await trimCache(cacheName, maxItems, maxBytes);
}

// Fetch event - network first, fallback to cache
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Service workers should never cache cross-origin content or mutations.
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;

  // Skip API requests except for covers (always network first)
  if (url.pathname.startsWith('/api/') && !url.pathname.startsWith('/api/covers/')) {
    event.respondWith(
      fetch(request).catch(() => {
        return new Response(
          JSON.stringify({ error: 'Offline - server unavailable' }),
          { status: 503, headers: { 'Content-Type': 'application/json' } }
        );
      })
    );
    return;
  }

  // Thumbnail caching - cache first, then network
  // This makes thumbnails load instantly from cache
  if (url.pathname.startsWith('/api/covers/') && url.pathname.includes('/thumb')) {
    event.respondWith(
      caches.open(THUMB_CACHE_NAME).then(async cache => {
        const cached = await cache.match(request);
        if (cached) return cached;

        try {
          const response = await fetch(request);
          // Covers use an HttpOnly credential cookie. Never persist a
          // response under a URL containing a long-lived API key.
          try {
            await putBoundedCover(THUMB_CACHE_NAME, request, response,
                                  MAX_THUMBS, MAX_THUMB_BYTES);
          } catch {
            // Storage quota errors should never interrupt cover display.
          }
          return response;
        } catch (err) {
          // If offline and not cached, return a placeholder or transparent 1x1 pixel
          return new Response(
            `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
              <rect width="120" height="120" fill="#1a1a1a"/>
              <text x="60" y="60" text-anchor="middle" dy=".3em" fill="#444" font-size="40">♪</text>
            </svg>`,
            { headers: { 'Content-Type': 'image/svg+xml' } }
          );
        }
      })
    );
    return;
  }

  // Full-size cover caching - network first, fallback to cache
  if (url.pathname.startsWith('/api/covers/')) {
    event.respondWith(
      fetch(request)
        .then(response => {
          event.waitUntil(
            putBoundedCover(COVER_CACHE_NAME, request, response,
                            MAX_COVERS, MAX_COVER_BYTES).catch(() => {})
          );
          return response;
        })
        .catch(() => {
          return caches.open(COVER_CACHE_NAME).then(cache => cache.match(request)).then(cached => {
            if (cached) return cached;
            // Fallback placeholder for missing covers
            return new Response(
              `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300">
                <rect width="300" height="300" fill="#1a1a1a"/>
                <text x="150" y="150" text-anchor="middle" dy=".3em" fill="#444" font-size="80">♪</text>
              </svg>`,
              { headers: { 'Content-Type': 'image/svg+xml' } }
            );
          });
        })
    );
    return;
  }

  // General static assets - network first, fallback to cache
  if (url.pathname !== '/' && !STATIC_ASSETS.includes(url.pathname)) return;
  event.respondWith(
    fetch(request)
      .then(response => {
        if (canCache(response)) {
          const clone = response.clone();
          event.waitUntil(caches.open(CACHE_NAME)
            .then(cache => cache.put(url.pathname, clone)).catch(() => {}));
        }
        return response;
      })
      .catch(() => {
        return caches.match(request).then(cached => {
          if (cached) return cached;
          if (request.mode === 'navigate') {
            return caches.match('/static/index.html');
          }
          return new Response('Offline', { status: 503 });
        });
      })
  );
});

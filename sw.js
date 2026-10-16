// VELVET - Service Worker for PWA
const CACHE_NAME = 'velvet-v4';
const STATIC_ASSETS = [
  '/',
  '/static/index.html',
  '/static/manifest.json',
  '/static/icon.png'
];

// Separate cache for thumbnails to avoid polluting main cache
const THUMB_CACHE_NAME = 'velvet-thumbs-v3';
const COVER_CACHE_NAME = 'velvet-covers-v1';
const MAX_THUMBS = 400;
const MAX_COVERS = 80;

// Install event - cache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_ASSETS))
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

// Helper to trim cache to max size
async function trimCache(cacheName, maxItems) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length > maxItems) {
    // Delete oldest items (first in the list)
    const toDelete = keys.slice(0, keys.length - maxItems);
    await Promise.all(toDelete.map(key => cache.delete(key)));
  }
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
          if (response.ok && response.type === 'basic' &&
              !response.headers.get('Cache-Control')?.includes('no-store')) {
            try {
              await cache.put(request, response.clone());
              await trimCache(THUMB_CACHE_NAME, MAX_THUMBS);
            } catch (cacheError) {
              // Storage quota exhaustion must not interrupt cover display.
            }
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
          if (response.ok && response.type === 'basic' &&
              !response.headers.get('Cache-Control')?.includes('no-store')) {
            const clone = response.clone();
            event.waitUntil(
              caches.open(COVER_CACHE_NAME)
                .then(async cache => {
                  await cache.put(request, clone);
                  await trimCache(COVER_CACHE_NAME, MAX_COVERS);
                }).catch(() => {})
            );
          }
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
  event.respondWith(
    fetch(request)
      .then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
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

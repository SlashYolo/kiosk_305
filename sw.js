/* sw.js — Service Worker для МАИ.Стенд
   Стратегия:
   - Статика (/,  /on.mp4, /logo.png):  cache-first
   - API (/api/*):                        network-first, cache fallback
   - Изображения (/lab-photos/*, etc.):   cache-first
*/
const CACHE_NAME = 'mai-kiosk-v1';
const STATIC_URLS = ['/', '/logo.png'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(c => c.addAll(STATIC_URLS).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Пропускаем не-GET
  if (e.request.method !== 'GET') return;

  // API: network-first → cache fallback
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request)
        .then(r => {
          if (r.ok) {
            const clone = r.clone();
            caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
          }
          return r;
        })
        .catch(() => caches.match(e.request).then(c => c || new Response('{"error":"offline"}', {
          status: 503, headers: {'Content-Type': 'application/json'}
        })))
    );
    return;
  }

  // Всё остальное: cache-first → network fallback
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(r => {
        if (r.ok && url.origin === self.location.origin) {
          const clone = r.clone();
          caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        }
        return r;
      });
    }).catch(() => {
      // Для навигации — отдаём кэш главной страницы
      if (e.request.mode === 'navigate') {
        return caches.match('/');
      }
      return new Response('', { status: 503 });
    })
  );
});

/* Quant Terminal PWA service worker：只缓存应用外壳，不缓存 API/WS/鉴权。 */
// 版本号变更会清掉上一版缓存。assets 是 cache-first，因此打包入口必须带内容哈希
// （Vite 默认就是 name-[hash].js）；固定文件名会让浏览器永远跑第一版前端。
const CACHE = 'quant-shell-v4'
const SHELL = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-maskable-512.png',
  '/icons/apple-touch-icon.png',
  '/icons/favicon-32.png',
]

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => Promise.all(SHELL.map((url) => cache.add(url).catch(() => undefined)))),
  )
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting()
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  // 业务接口、WebSocket 握手、健康检查、文档一律走网络
  if (/^\/(api|ws|healthz|docs|redoc|openapi\.json)/.test(url.pathname)) return
  if (url.pathname === '/sw.js') return

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone()
          caches
            .open(CACHE)
            .then((cache) => cache.put('/index.html', copy))
            .catch(() => undefined)
          return response
        })
        .catch(async () => {
          const cache = await caches.open(CACHE)
          return (await cache.match('/index.html')) || (await cache.match('/')) || Response.error()
        }),
    )
    return
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached
      return fetch(request).then((response) => {
        if (response.ok && (url.pathname.startsWith('/assets/') || url.pathname.startsWith('/icons/'))) {
          const copy = response.clone()
          caches
            .open(CACHE)
            .then((cache) => cache.put(request, copy))
            .catch(() => undefined)
        }
        return response
      })
    }),
  )
})

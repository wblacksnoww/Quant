/* Quant Terminal PWA service worker：只缓存应用外壳，不缓存 API/WS/鉴权。 */
// 版本号变更会清掉上一版缓存。assets 是 cache-first，因此打包入口必须带内容哈希
// （Vite 默认就是 name-[hash].js）；固定文件名会让浏览器永远跑第一版前端。
//
// 关于 index.html：**预缓存列表里不能放它**。HTML 里写的是带哈希的 assets 文件名，
// 一旦重新构建，旧 HTML 指向的那份 JS 在新镜像里已经不存在 —— 离线或网络抖动时
// SW 回退到旧 HTML，浏览器去取旧 JS 拿走 404，页面纯白屏且没有任何报错。
// 所以 HTML 只在每次导航成功时顺手更新，首次安装不预置。
const CACHE = 'quant-shell-v5'
const SHELL = [
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-maskable-512.png',
  '/icons/apple-touch-icon.png',
  '/icons/favicon-32.png',
]

// 连不上服务且没有可用外壳时的兜底页：至少告诉用户发生了什么，而不是白屏。
const OFFLINE_HTML = `<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quant Terminal · 离线</title>
<body style="margin:0;height:100vh;display:grid;place-items:center;background:#0d121b;color:#e6edf7;font-family:system-ui,-apple-system,'PingFang SC',sans-serif">
<div style="text-align:center;max-width:420px;padding:24px">
<div style="font-size:15px;font-weight:500;margin-bottom:8px">暂时连不上服务</div>
<div style="font-size:13px;line-height:1.7;color:#8b9bb4">检查网络后刷新页面。<br>如果刚更新过版本，按 Ctrl+Shift+R（Mac：Cmd+Shift+R）强制刷新可清掉旧缓存。</div>
</div></body></html>`

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
          // 只缓存成功的整页响应：缓存 404/500 会把错误页焊死在本地
          if (response.ok) {
            const copy = response.clone()
            caches
              .open(CACHE)
              .then((cache) => cache.put('/index.html', copy))
              .catch(() => undefined)
          }
          return response
        })
        .catch(async () => {
          const cache = await caches.open(CACHE)
          const cached = (await cache.match('/index.html')) || (await cache.match('/'))
          return cached || new Response(OFFLINE_HTML, { headers: { 'content-type': 'text/html; charset=utf-8' } })
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

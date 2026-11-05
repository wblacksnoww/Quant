/* 前端缓存自救页：注销 Service Worker + 清空 Cache Storage，然后回首页。
 *
 * 用于"部署后页面白屏"这类情况 —— 浏览器里留着旧 index.html，它引用的
 * 带哈希 JS 在新版本里已经不存在，请求 404 后 React 根本没机会挂载。
 * 独立成文件而不是内联，是因为 nginx 的 CSP 是 script-src 'self'，
 * 内联脚本会被拦掉。
 */
;(async () => {
  const title = document.getElementById('title')
  const detail = document.getElementById('detail')
  const notes = []

  try {
    if ('serviceWorker' in navigator) {
      const regs = await navigator.serviceWorker.getRegistrations()
      await Promise.all(regs.map((reg) => reg.unregister()))
      notes.push(`已注销 ${regs.length} 个 Service Worker`)
    }
    if (window.caches) {
      const keys = await caches.keys()
      await Promise.all(keys.map((key) => caches.delete(key)))
      notes.push(`已清空 ${keys.length} 个缓存区`)
    }
    notes.push('正在重新加载')
    if (title) title.textContent = '已重置'
    if (detail) detail.textContent = notes.join(' · ')
    // 带随机参数绕过可能残留的 HTTP 缓存
    window.setTimeout(() => window.location.replace('/?reset=' + Date.now()), 600)
  } catch (error) {
    if (title) title.textContent = '重置失败'
    if (detail) detail.textContent = String(error) + '；请手动在开发者工具里注销 Service Worker 后刷新。'
  }
})()

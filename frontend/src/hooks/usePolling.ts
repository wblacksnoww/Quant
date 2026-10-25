import { useEffect, useRef } from 'react'

export interface PollingOptions {
  /** 轮询间隔（毫秒）。<= 0 表示不轮询。 */
  interval: number
  /** 是否启用。默认 true。 */
  enabled?: boolean
  /** 挂载后是否立即执行一次。默认 true。 */
  immediate?: boolean
  /** 页面切到后台时是否暂停。默认 true。 */
  pauseWhenHidden?: boolean
}

/**
 * 声明式轮询：按固定间隔重复执行一个异步任务。
 *
 * 相比在各页面手写 `setInterval`，这里处理了四个容易漏掉的边界：
 *
 * 1. **回调不进入依赖**：任务用 ref 保存，渲染频率再高也不会重建定时器。
 *    手写版把 `load` 放进依赖数组，一旦调用方没包 `useCallback`，每次渲染
 *    都会 clear + 重建，轮询实际变成"永不触发"或"每秒触发"。
 * 2. **不重叠**：上一轮请求没返回就跳过这一轮，避免接口变慢时请求雪崩
 *    （tab 切回来的瞬间尤其明显）。
 * 3. **后台暂停**：`document.visibilityState !== 'visible'` 时跳过，
 *    用户切到别的标签页不再空转抓数据。
 * 4. **卸载安全**：清理后不再调用任务，也不残留定时器。
 */
export function usePolling(task: () => void | Promise<void>, options: PollingOptions): void {
  const { interval, enabled = true, immediate = true, pauseWhenHidden = true } = options

  const taskRef = useRef(task)
  const inFlight = useRef(false)

  useEffect(() => {
    taskRef.current = task
  }, [task])

  useEffect(() => {
    if (!enabled || interval <= 0) return

    let cancelled = false

    const run = async () => {
      if (cancelled) return
      if (pauseWhenHidden && document.visibilityState !== 'visible') return
      if (inFlight.current) return
      inFlight.current = true
      try {
        await taskRef.current()
      } catch {
        // 轮询失败不抛给上层：状态由任务自身的错误处理（setError / toast）表达，
        // 这里再抛会变成 unhandled rejection，控制台噪音掩盖真正的问题。
      } finally {
        inFlight.current = false
      }
    }

    if (immediate) void run()
    const timer = window.setInterval(() => void run(), interval)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [enabled, interval, immediate, pauseWhenHidden])
}

/** WebSocket 订阅：回测进度、纸盘成交都从这里推送。 */

import { useEffect, useRef, useState } from 'react'
import { getAccessToken, refreshSession } from '../api/client'
import type { WsEvent } from '../api/types'

function socketUrl(token: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws?token=${encodeURIComponent(token)}`
}

export function useWebSocket(handler: (event: WsEvent) => void, enabled = true): { connected: boolean } {
  const handlerRef = useRef(handler)
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    handlerRef.current = handler
  }, [handler])

  useEffect(() => {
    if (!enabled) return
    let closed = false
    let socket: WebSocket | null = null
    let timer: number | undefined
    let attempt = 0

    const connect = () => {
      const token = getAccessToken()
      if (!token || closed) return
      try {
        socket = new WebSocket(socketUrl(token))
      } catch {
        schedule()
        return
      }

      socket.onopen = () => {
        attempt = 0
        setConnected(true)
      }
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data as string) as WsEvent
          if (event.type === 'ping' || event.type === 'hello') return
          handlerRef.current(event)
        } catch {
          // 忽略非 JSON 帧
        }
      }
      socket.onclose = (event) => {
        setConnected(false)
        if (closed) return
        if (event.code === 4401) {
          // access token 过期：刷新后重连，别让用户看到断线
          void refreshSession().then((ok) => {
            if (ok && !closed) schedule(500)
          })
          return
        }
        schedule()
      }
      socket.onerror = () => {
        socket?.close()
      }
    }

    const schedule = (delay?: number) => {
      if (closed) return
      attempt += 1
      const wait = delay ?? Math.min(1000 * 2 ** Math.min(attempt, 4), 15000)
      timer = window.setTimeout(connect, wait)
    }

    connect()

    return () => {
      closed = true
      if (timer) window.clearTimeout(timer)
      if (socket) {
        socket.onclose = null
        socket.close()
      }
      setConnected(false)
    }
  }, [enabled])

  return { connected }
}

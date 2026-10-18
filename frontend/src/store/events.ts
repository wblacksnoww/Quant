/** 最近一条 WebSocket 事件。页面用 selector 过滤自己关心的 run_id。 */

import { create } from 'zustand'
import type { WsEvent } from '../api/types'

interface EventState {
  last: WsEvent | null
  count: number
  handle: (event: WsEvent) => void
}

export const useEvents = create<EventState>((set) => ({
  last: null,
  count: 0,
  handle: (event) => set((state) => ({ last: event, count: state.count + 1 })),
}))

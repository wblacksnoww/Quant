/** 轻量通知队列，替代 alert()。 */

import { create } from 'zustand'

export type ToastKind = 'success' | 'error' | 'info' | 'warning'

export interface Toast {
  id: number
  kind: ToastKind
  message: string
}

interface ToastState {
  items: Toast[]
  push: (kind: ToastKind, message: string) => void
  dismiss: (id: number) => void
}

let nextId = 1

export const useToast = create<ToastState>((set) => ({
  items: [],
  push: (kind, message) => {
    const id = nextId++
    set((state) => ({ items: [...state.items.slice(-4), { id, kind, message }] }))
    window.setTimeout(
      () => {
        set((state) => ({ items: state.items.filter((item) => item.id !== id) }))
      },
      kind === 'error' ? 6000 : 3200,
    )
  },
  dismiss: (id) => set((state) => ({ items: state.items.filter((item) => item.id !== id) })),
}))

export function notify(kind: ToastKind, message: string): void {
  useToast.getState().push(kind, message)
}

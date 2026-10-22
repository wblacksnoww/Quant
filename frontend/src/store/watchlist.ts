/** 账号级自选：服务端为准，localStorage 只做登录后首屏缓存和旧数据迁移。 */

import { create } from 'zustand'
import { watchlistApi } from '../api/client'
import { normalizeInstrumentId } from '../utils/format'
import { useAuth } from './auth'
import { notify } from './toast'

const LEGACY_KEY = 'quant.watchlist'
const MIGRATED_KEY = 'quant.watchlist.migrated'
const MAX_ITEMS = 30

function cacheKey(userId: number): string {
  return `quant.watchlist.${userId}`
}

function normalizeList(items: unknown[]): string[] {
  const out: string[] = []
  for (const item of items) {
    const value = normalizeInstrumentId(String(item)) ?? String(item).trim()
    if (value && !out.includes(value)) out.push(value)
  }
  return out
}

function readJsonArray(key: string): string[] {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? normalizeList(parsed) : []
  } catch {
    return []
  }
}

function readLegacy(): string[] {
  if (localStorage.getItem(MIGRATED_KEY)) return []
  return readJsonArray(LEGACY_KEY)
}

function writeCache(userId: number, items: string[]): void {
  try {
    localStorage.setItem(cacheKey(userId), JSON.stringify(items))
  } catch {
    // 存储不可用时忽略
  }
}

interface WatchlistState {
  items: string[]
  loadedFor: number | null
  loading: boolean
  load: () => Promise<void>
  setItems: (next: string[]) => Promise<void>
  add: (symbol: string) => Promise<boolean>
  remove: (symbol: string) => Promise<void>
}

let loadPromise: Promise<void> | null = null
let loadPromiseUser: number | null = null

export const useWatchlist = create<WatchlistState>((set, get) => ({
  items: [],
  loadedFor: null,
  loading: false,

  load: async () => {
    const user = useAuth.getState().user
    if (!user) return
    if (get().loadedFor === user.id) return
    if (loadPromise && loadPromiseUser === user.id) return loadPromise

    const cached = readJsonArray(cacheKey(user.id))
    const legacy = cached.length === 0 ? readLegacy() : []
    const initial = cached.length > 0 ? cached : legacy
    // 切换账号时先清掉上一个账号的自选，避免短暂串号
    set({ items: initial })

    loadPromiseUser = user.id
    loadPromise = (async () => {
      set({ loading: true })
      let ok = false
      try {
        const server = await watchlistApi.list()
        if (useAuth.getState().user?.id !== user.id) return
        if (server.length > 0) {
          const items = normalizeList(server)
          set({ items })
          writeCache(user.id, items)
        } else if (initial.length > 0) {
          const saved = await watchlistApi.replace(initial)
          if (useAuth.getState().user?.id !== user.id) return
          const items = normalizeList(saved)
          set({ items })
          writeCache(user.id, items)
        } else {
          set({ items: [] })
          writeCache(user.id, [])
        }
        localStorage.setItem(MIGRATED_KEY, '1')
        localStorage.removeItem(LEGACY_KEY)
        ok = true
      } catch {
        // 服务端失败时保留本地缓存；下次进入页面重试
      } finally {
        set({ loading: false, loadedFor: ok ? user.id : null })
        loadPromise = null
        loadPromiseUser = null
      }
    })()
    return loadPromise
  },

  setItems: async (next: string[]) => {
    const user = useAuth.getState().user
    const items = normalizeList(next).slice(0, MAX_ITEMS)
    set({ items })
    if (!user) return
    writeCache(user.id, items)
    try {
      const saved = await watchlistApi.replace(items)
      if (useAuth.getState().user?.id !== user.id) return
      const normalized = normalizeList(saved)
      set({ items: normalized })
      writeCache(user.id, normalized)
    } catch {
      notify('error', '自选保存失败，请检查登录状态')
    }
  },

  add: async (symbol: string) => {
    await get().load()
    const value = normalizeInstrumentId(symbol) ?? symbol.trim()
    if (!value) return false
    const current = get().items
    if (current.includes(value)) return false
    if (current.length >= MAX_ITEMS) {
      notify('warning', `自选最多 ${MAX_ITEMS} 个，请先移除部分标的`)
      return false
    }
    await get().setItems([...current, value])
    return true
  },

  remove: async (symbol: string) => {
    await get().setItems(get().items.filter((item) => item !== symbol))
  },
}))

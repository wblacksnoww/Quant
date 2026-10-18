/** 登录态 store。access token 存在 client 模块内存里，这里只镜像用户资料。 */

import { create } from 'zustand'
import { authApi, clearSession, hasRefreshToken, refreshSession, setSession } from '../api/client'
import type { User } from '../api/types'

interface AuthState {
  user: User | null
  status: 'booting' | 'anonymous' | 'authenticated'
  bootstrap: () => Promise<void>
  login: (username: string, password: string, totpCode?: string) => Promise<void>
  logout: () => Promise<void>
  reload: () => Promise<void>
  markAnonymous: () => void
  setUser: (user: User) => void
}

export const useAuth = create<AuthState>((set, get) => ({
  user: null,
  status: 'booting',

  bootstrap: async () => {
    if (!hasRefreshToken()) {
      set({ status: 'anonymous', user: null })
      return
    }
    const ok = await refreshSession()
    if (!ok) {
      set({ status: 'anonymous', user: null })
      return
    }
    try {
      const user = await authApi.me()
      set({ user, status: 'authenticated' })
    } catch {
      clearSession()
      set({ user: null, status: 'anonymous' })
    }
  },

  login: async (username, password, totpCode) => {
    const tokens = await authApi.login(username, password, totpCode)
    setSession(tokens.access_token, tokens.refresh_token)
    set({ user: tokens.user, status: 'authenticated' })
  },

  logout: async () => {
    try {
      await authApi.logout()
    } catch {
      // 令牌已失效时也要完成本地登出
    }
    clearSession()
    set({ user: null, status: 'anonymous' })
  },

  reload: async () => {
    if (get().status !== 'authenticated') return
    const user = await authApi.me()
    set({ user })
  },

  markAnonymous: () => set({ user: null, status: 'anonymous' }),
  setUser: (user) => set({ user }),
}))

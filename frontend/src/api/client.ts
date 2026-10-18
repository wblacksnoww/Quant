/**
 * API 客户端。
 *
 * 安全设计：access token 只放内存（刷新页面即失效），refresh token 放 localStorage，
 * 启动时用一次性刷新换回 access token。这样即使 XSS 偷到 localStorage，拿到的也只是
 * 一个可被服务端吊销、且每次刷新都会轮换的凭据；而 access token 不落盘。
 *
 * 401 会自动刷新一次并重放请求；刷新失败则清空会话并通知上层回到登录页。
 */

import type {
  AuditItem,
  AuditVerify,
  BacktestRequest,
  BarsResponse,
  Credential,
  AnyStrategySpec,
  CustomSpec,
  CustomStrategy,
  DataCacheItem,
  Health,
  MarketInfo,
  Message,
  PaperStartRequest,
  PaperState,
  QuotesResponse,
  RiskPresets,
  RiskRule,
  RunAccepted,
  RunDetail,
  RunSummary,
  SessionInfo,
  Strategy,
  SymbolInfo,
  SystemInfo,
  TokenResponse,
  TotpSetup,
  User,
} from './types'

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api'
const REFRESH_KEY = 'quant.refresh_token'

export class ApiError extends Error {
  status: number
  code: string
  detail: Record<string, unknown>
  requestId?: string

  constructor(message: string, status = 0, code = 'network_error', detail: Record<string, unknown> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

// --------------------------------------------------------------------------- //
// 会话状态
// --------------------------------------------------------------------------- //

let accessToken: string | null = null
let refreshInFlight: Promise<boolean> | null = null
let authListener: ((authenticated: boolean) => void) | null = null

export function setSession(access: string, refresh?: string | null): void {
  accessToken = access
  if (refresh) localStorage.setItem(REFRESH_KEY, refresh)
}

export function clearSession(): void {
  accessToken = null
  localStorage.removeItem(REFRESH_KEY)
}

export function getAccessToken(): string | null {
  return accessToken
}

export function hasRefreshToken(): boolean {
  return Boolean(localStorage.getItem(REFRESH_KEY))
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY)
}

/** 注册登录态变化回调（刷新失败时由 client 主动通知）。 */
export function onAuthChange(listener: (authenticated: boolean) => void): () => void {
  authListener = listener
  return () => {
    if (authListener === listener) authListener = null
  }
}

// --------------------------------------------------------------------------- //
// 底层请求
// --------------------------------------------------------------------------- //

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Record<string, string | number | boolean | null | undefined>
  signal?: AbortSignal
  auth?: boolean
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = `${API_BASE}${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

async function rawFetch(path: string, options: RequestOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (options.auth !== false && accessToken) headers.Authorization = `Bearer ${accessToken}`
  return fetch(buildUrl(path, options.query), {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
    credentials: 'same-origin',
  })
}

async function toApiError(response: Response): Promise<ApiError> {
  let payload: Record<string, unknown> = {}
  try {
    const parsed = (await response.json()) as unknown
    if (parsed && typeof parsed === 'object') payload = parsed as Record<string, unknown>
  } catch {
    // 反向代理返回的 HTML 错误页
  }
  const detail = payload.detail
  let message = typeof payload.message === 'string' ? payload.message : ''
  if (!message && typeof detail === 'string') message = detail
  if (!message && Array.isArray(detail)) {
    const first = detail[0] as { msg?: string; loc?: unknown[] } | undefined
    message = first?.msg ?? ''
  }
  if (!message) message = `请求失败（HTTP ${response.status}）`
  const code = typeof payload.code === 'string' ? payload.code : `http_${response.status}`
  return new ApiError(message, response.status, code, {
    ...(typeof detail === 'object' && detail !== null && !Array.isArray(detail)
      ? (detail as Record<string, unknown>)
      : {}),
    ...(Array.isArray(detail) ? { errors: detail } : {}),
  })
}

/** 用 refresh token 换新的一对令牌。并发调用会合并成一次请求。 */
export function refreshSession(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight
  const refresh = getRefreshToken()
  if (!refresh) return Promise.resolve(false)

  refreshInFlight = (async () => {
    try {
      const response = await fetch(buildUrl('/auth/refresh'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      })
      if (!response.ok) {
        clearSession()
        authListener?.(false)
        return false
      }
      const data = (await response.json()) as TokenResponse
      setSession(data.access_token, data.refresh_token)
      authListener?.(true)
      return true
    } catch {
      // 网络故障不清空 refresh token，下次还能再试
      return false
    } finally {
      refreshInFlight = null
    }
  })()
  return refreshInFlight
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const auth = options.auth !== false
  let response: Response
  try {
    response = await rawFetch(path, options)
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error
    throw new ApiError('无法连接服务器，请检查网络或服务状态', 0, 'network_error')
  }

  if (response.status === 401 && auth) {
    const refreshed = await refreshSession()
    if (refreshed) {
      response = await rawFetch(path, options)
    } else {
      clearSession()
      authListener?.(false)
      throw await toApiError(response)
    }
  }

  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

// --------------------------------------------------------------------------- //
// 认证
// --------------------------------------------------------------------------- //

export const authApi = {
  login: (username: string, password: string, totpCode?: string) =>
    request<TokenResponse>('/auth/login', {
      method: 'POST',
      auth: false,
      body: { username, password, totp_code: totpCode || null },
    }),
  me: () => request<User>('/auth/me'),
  logout: () =>
    request<Message>('/auth/logout', {
      method: 'POST',
      body: { refresh_token: getRefreshToken() },
    }),
  changePassword: (oldPassword: string, newPassword: string) =>
    request<Message>('/auth/password', {
      method: 'POST',
      body: { old_password: oldPassword, new_password: newPassword },
    }),
  sessions: () => request<SessionInfo[]>('/auth/sessions'),
  revokeSession: (id: number) => request<Message>(`/auth/sessions/${id}`, { method: 'DELETE' }),
  totpSetup: () => request<TotpSetup>('/auth/totp/setup', { method: 'POST' }),
  totpEnable: (code: string) =>
    request<Message>('/auth/totp/enable', { method: 'POST', body: { code } }),
  totpDisable: (password: string, code: string) =>
    request<Message>('/auth/totp/disable', { method: 'POST', body: { password, code } }),
}

// --------------------------------------------------------------------------- //
// 业务接口
// --------------------------------------------------------------------------- //

export const marketApi = {
  markets: () => request<MarketInfo[]>('/market/markets'),
  bars: (symbol: string, start?: string, end?: string, refresh = false) =>
    request<BarsResponse>('/market/bars', { query: { symbol, start, end, refresh } }),
  quote: (symbols: string[]) =>
    request<QuotesResponse>('/market/quote', {
      query: { symbols: symbols.join(',') },
    }),
  search: (keyword: string, limit = 10, market?: string | null) =>
    request<SymbolInfo[]>('/market/search', { query: { q: keyword, limit, market } }),
  symbols: () => request<string[]>('/market/symbols'),
  cache: () => request<DataCacheItem[]>('/market/cache'),
  refresh: (symbols: string[]) =>
    request<{ refreshed: Record<string, number> }>('/market/refresh', {
      method: 'POST',
      query: { symbols: symbols.join(',') },
    }),
  calendar: (start?: string, end?: string, market?: string) =>
    request<string[]>('/market/calendar', { query: { start, end, market } }),
}

export const strategyApi = {
  list: () => request<Strategy[]>('/strategies'),
  get: (id: string) => request<Strategy>(`/strategies/${id}`),
  custom: () => request<CustomStrategy[]>('/strategies/custom'),
  createCustom: (body: { name: string; spec: AnyStrategySpec }) =>
    request<CustomStrategy>('/strategies/custom', { method: 'POST', body }),
  updateCustom: (id: number, body: { name?: string; spec?: AnyStrategySpec; enabled?: boolean }) =>
    request<CustomStrategy>(`/strategies/custom/${id}`, { method: 'PATCH', body }),
  removeCustom: (id: number) =>
    request<Message>(`/strategies/custom/${id}`, { method: 'DELETE' }),
}

export const backtestApi = {
  list: (limit = 50, offset = 0, mine = true) =>
    request<RunSummary[]>('/backtests', { query: { limit, offset, mine } }),
  get: (id: string) => request<RunDetail>(`/backtests/${id}`),
  submit: (body: BacktestRequest) => request<RunAccepted>('/backtests', { method: 'POST', body }),
  cancel: (id: string) => request<Message>(`/backtests/${id}/cancel`, { method: 'POST' }),
  remove: (id: string) => request<Message>(`/backtests/${id}`, { method: 'DELETE' }),
}

export const paperApi = {
  start: (body: PaperStartRequest) => request<RunAccepted>('/paper/start', { method: 'POST', body }),
  stop: () => request<Message>('/paper/stop', { method: 'POST' }),
  state: () => request<PaperState | null>('/paper/state'),
  runs: () => request<RunSummary[]>('/paper/runs'),
}

export const riskApi = {
  rules: () => request<RiskRule[]>('/risk/rules'),
  presets: () => request<RiskPresets>('/risk/presets'),
}

export const systemApi = {
  health: () => request<Health>('/system/health'),
  info: () => request<SystemInfo>('/system/info'),
  audit: (limit = 100, offset = 0, action?: string) =>
    request<AuditItem[]>('/system/audit', { query: { limit, offset, action } }),
  verifyAudit: () => request<AuditVerify>('/system/audit/verify'),
  data: () => request<DataCacheItem[]>('/system/data'),
  refreshData: (symbols: string, years = 5) =>
    request<{ refreshed: Record<string, number> }>('/system/data/refresh', {
      method: 'POST',
      query: { symbols, years },
    }),
}

export const adminApi = {
  users: () => request<User[]>('/admin/users'),
  createUser: (body: { username: string; password: string; role: 'user' | 'admin' }) =>
    request<User>('/admin/users', { method: 'POST', body }),
  updateUser: (id: number, body: { role?: 'user' | 'admin'; is_active?: boolean }) =>
    request<User>(`/admin/users/${id}`, { method: 'PATCH', body }),
  resetPassword: (id: number, newPassword: string) =>
    request<Message>(`/admin/users/${id}/reset-password`, {
      method: 'POST',
      body: { new_password: newPassword },
    }),
}

export const credentialApi = {
  list: () => request<Credential[]>('/credentials'),
  create: (body: { name: string; kind: 'broker' | 'datasource'; payload: Record<string, unknown>; description?: string }) =>
    request<Credential>('/credentials', { method: 'POST', body }),
  update: (id: number, body: { name?: string; description?: string; payload?: Record<string, unknown> }) =>
    request<Credential>(`/credentials/${id}`, { method: 'PUT', body }),
  reveal: (id: number) =>
    request<{ id: number; name: string; payload: Record<string, unknown> }>(
      `/credentials/${id}/reveal`,
      { method: 'POST' },
    ),
  remove: (id: number) => request<Message>(`/credentials/${id}`, { method: 'DELETE' }),
}

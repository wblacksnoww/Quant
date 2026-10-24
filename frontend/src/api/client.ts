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
  AdminStrategy,
  CustomStrategy,
  DataCacheItem,
  FundNav,
  GatewayStatus,
  FundPortfolio,
  FundTrade,
  Health,
  IndexQuote,
  InstrumentInfo,
  LiveAdapter,
  LiveStartRequest,
  CaptchaChallenge,
  Feedback,
  LiveState,
  MarketInfo,
  Message,
  NewsFactor,
  NewsFetchStatus,
  NewsItem,
  NewsItemInput,
  PaperStartRequest,
  PaperState,
  QuotesResponse,
  RegisterRequest,
  Ranking,
  RiskPresets,
  RiskRule,
  RunAccepted,
  RunDetail,
  RunSummary,
  SharedStrategy,
  StrategyShare,
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
  captcha: () => request<CaptchaChallenge>('/auth/captcha', { auth: false }),
  register: (body: RegisterRequest) =>
    request<Message>('/auth/register', { method: 'POST', auth: false, body }),
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
  totpEnable: (code: string) => request<Message>('/auth/totp/enable', { method: 'POST', body: { code } }),
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
  indices: () => request<IndexQuote[]>('/market/indices'),
  instrument: (symbol: string) => request<InstrumentInfo>('/market/instrument', { query: { symbol } }),
  intraday: (symbol: string, freq: '1m' | '5m' | '15m' | '30m' | '60m' = '5m', limit = 240) =>
    request<BarsResponse>('/market/intraday', { query: { symbol, freq, limit } }),
  klines: (
    symbol: string,
    interval: '1m' | '5m' | '15m' | '30m' | '60m' | '1d' | '1w' | '1M' = '1d',
    limit = 240,
  ) => request<BarsResponse>('/market/klines', { query: { symbol, interval, limit } }),
  quote: (symbols: string[]) =>
    request<QuotesResponse>('/market/quote', {
      query: { symbols: symbols.join(',') },
    }),
  ranking: (market: string, sort = 'change_pct', order = 'desc', limit = 20, period = 'day') =>
    request<Ranking>('/market/ranking', { query: { market, sort, order, limit, period } }),
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
  removeCustom: (id: number) => request<Message>(`/strategies/custom/${id}`, { method: 'DELETE' }),
  shared: () => request<SharedStrategy[]>('/strategies/shared'),
  forkShared: (id: number) => request<CustomStrategy>(`/strategies/shared/${id}/fork`, { method: 'POST' }),
  syncCustom: (id: number) => request<CustomStrategy>(`/strategies/custom/${id}/sync`, { method: 'POST' }),
  shares: (id: number) => request<StrategyShare[]>(`/strategies/custom/${id}/shares`),
  shareCustom: (id: number, body: { scope: 'public' | 'user'; target_username?: string }) =>
    request<StrategyShare>(`/strategies/custom/${id}/shares`, { method: 'POST', body }),
  revokeShare: (id: number, shareId: number) =>
    request<Message>(`/strategies/custom/${id}/shares/${shareId}`, { method: 'DELETE' }),
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
  stop: (runId?: string) => request<Message>('/paper/stop', { method: 'POST', query: { run_id: runId } }),
  pause: (runId?: string) => request<Message>('/paper/pause', { method: 'POST', query: { run_id: runId } }),
  resume: (runId?: string) => request<Message>('/paper/resume', { method: 'POST', query: { run_id: runId } }),
  continueRun: (runId: string) =>
    request<RunAccepted>('/paper/continue', { method: 'POST', query: { run_id: runId } }),
  removeRun: (runId: string) => request<Message>(`/paper/runs/${runId}`, { method: 'DELETE' }),
  sessions: () => request<PaperState[]>('/paper/sessions'),
  state: () => request<PaperState | null>('/paper/state'),
  runs: () => request<RunSummary[]>('/paper/runs'),
  switchStrategy: (body: {
    strategy_id: string
    params?: Record<string, unknown>
    cancel_open_orders?: boolean
  }) => request<Message>('/paper/switch', { method: 'POST', body }),
}

export const riskApi = {
  rules: () => request<RiskRule[]>('/risk/rules'),
  presets: () => request<RiskPresets>('/risk/presets'),
}

export const fundApi = {
  search: (q: string, limit = 30) => request<SymbolInfo[]>('/funds/search', { query: { q, limit } }),
  nav: (symbol: string, start?: string, end?: string) =>
    request<FundNav>('/funds/nav', { query: { symbol, start, end } }),
  holdings: () => request<FundPortfolio>('/funds/holdings'),
  trades: (symbol?: string, limit = 200) =>
    request<FundTrade[]>('/funds/trades', { query: { symbol, limit } }),
  createTrade: (body: {
    symbol: string
    side: 'subscribe' | 'redeem'
    trade_date?: string
    confirm_date?: string
    amount?: number
    shares?: number
    nav?: number
    fee_rate?: number
    note?: string
  }) => request<FundTrade>('/funds/trades', { method: 'POST', body }),
  removeTrade: (id: number) => request<void>(`/funds/trades/${id}`, { method: 'DELETE' }),
}

export const liveApi = {
  adapters: () => request<LiveAdapter[]>('/live/adapters'),
  start: (body: LiveStartRequest) => request<RunAccepted>('/live/start', { method: 'POST', body }),
  stop: (credentialId?: number) =>
    request<Message>('/live/stop', { method: 'POST', query: { credential_id: credentialId } }),
  pause: (credentialId?: number) =>
    request<Message>('/live/pause', { method: 'POST', query: { credential_id: credentialId } }),
  resume: (credentialId?: number) =>
    request<Message>('/live/resume', { method: 'POST', query: { credential_id: credentialId } }),
  sessions: () => request<LiveState[]>('/live/sessions'),
  state: () => request<LiveState | null>('/live/state'),
  runs: () => request<RunSummary[]>('/live/runs'),
  switchStrategy: (body: {
    strategy_id: string
    params?: Record<string, unknown>
    cancel_open_orders?: boolean
  }) => request<Message>('/live/switch', { method: 'POST', body }),
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
  approveUser: (id: number, action: 'approve' | 'reject') =>
    request<User>(`/admin/users/${id}/approval`, { method: 'PATCH', body: { action } }),
  feedback: (status?: string, limit = 200) =>
    request<Feedback[]>('/admin/feedback', { query: { status, limit } }),
  updateFeedback: (id: number, status: 'new' | 'read' | 'resolved') =>
    request<Feedback>(`/admin/feedback/${id}`, { method: 'PATCH', body: { status } }),
  strategies: () => request<AdminStrategy[]>('/admin/strategies'),
  updateStrategy: (id: number, enabled: boolean) =>
    request<AdminStrategy>(`/admin/strategies/${id}`, { method: 'PATCH', body: { enabled } }),
  deleteStrategy: (id: number) => request<Message>(`/admin/strategies/${id}`, { method: 'DELETE' }),
}

export const feedbackApi = {
  create: (body: { category: 'suggestion' | 'bug' | 'other'; content: string }) =>
    request<Message>('/feedback', { method: 'POST', body }),
}

export const credentialApi = {
  list: () => request<Credential[]>('/credentials'),
  create: (body: {
    name: string
    kind: 'broker' | 'datasource'
    payload: Record<string, unknown>
    description?: string
  }) => request<Credential>('/credentials', { method: 'POST', body }),
  update: (id: number, body: { name?: string; description?: string; payload?: Record<string, unknown> }) =>
    request<Credential>(`/credentials/${id}`, { method: 'PUT', body }),
  reveal: (id: number) =>
    request<{ id: number; name: string; payload: Record<string, unknown> }>(`/credentials/${id}/reveal`, {
      method: 'POST',
    }),
  remove: (id: number) => request<Message>(`/credentials/${id}`, { method: 'DELETE' }),
}

export const newsApi = {
  items: (symbol?: string, limit = 100) => request<NewsItem[]>('/news/items', { query: { symbol, limit } }),
  create: (body: NewsItemInput) => request<NewsItem>('/news/items', { method: 'POST', body }),
  factor: (symbol: string, window = '1h', asOf?: string) =>
    request<NewsFactor>('/news/factors', { query: { symbol, window, as_of: asOf } }),
  remove: (id: number) => request<Message>(`/news/items/${id}`, { method: 'DELETE' }),
  fetch: () => request<Message>('/news/fetch', { method: 'POST' }),
  status: () => request<NewsFetchStatus>('/news/status'),
}

export const watchlistApi = {
  list: () => request<string[]>('/watchlist'),
  replace: (symbols: string[]) => request<string[]>('/watchlist', { method: 'PUT', body: { symbols } }),
}

export const gatewayApi = {
  status: () => request<GatewayStatus>('/gateway/status'),
  start: (body: {
    strategy_type: 'grid' | 'breakout' | 'tick_momentum'
    params: Record<string, unknown>
    symbols: string[]
    bar_interval_sec: number
    sandbox: boolean
    confirm_live?: boolean
    credential_id?: number | null
  }) => request<GatewayStatus>('/gateway/start', { method: 'POST', body }),
  stop: () => request<Message>('/gateway/stop', { method: 'POST' }),
}

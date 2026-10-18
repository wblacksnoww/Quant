/**
 * 与后端 Pydantic schema 一一对应的 TypeScript 类型。
 *
 * 约定：金额单位是元，比例是小数（0.12 表示 12%），日期是 ISO 字符串。
 * 后端多返回的字段一律用可选属性承接，避免接口加字段导致前端编译失败。
 */

// --------------------------------------------------------------------------- //
// 认证
// --------------------------------------------------------------------------- //

export interface User {
  id: number
  username: string
  role: 'admin' | 'user' | string
  is_active: boolean
  totp_enabled: boolean
  last_login_at?: string | null
  created_at?: string | null
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  refresh_expires_in: number
  user: User
}

export interface SessionInfo {
  id: number
  jti: string
  ip: string
  user_agent: string
  created_at: string
  expires_at: string
}

export interface TotpSetup {
  secret: string
  provisioning_uri: string
  recovery_codes: string[]
  issuer: string
}

// --------------------------------------------------------------------------- //
// 行情
// --------------------------------------------------------------------------- //

export interface Bar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  prev_close?: number | null
  limit_up?: number | null
  limit_down?: number | null
}

export interface BarsResponse {
  symbol: string
  code?: string
  market?: string
  currency?: string
  name: string
  bars: Bar[]
  source: string
  cached: boolean
}

export interface SymbolInfo {
  symbol: string
  code?: string
  name: string
  board: string
  industry: string
  is_st: boolean
  market?: string
  kind?: string
}

export interface MarketInfo {
  code: string
  label: string
  currency: string
  currency_symbol: string
}

export interface QuoteItem {
  symbol: string
  name: string
  price: number
  prev_close: number | null
  open: number | null
  high: number | null
  low: number | null
  volume: number | null
  amount: number | null
  change: number | null
  change_pct: number | null
  ts: string
  source: string
  stale: boolean
  market?: string
  currency?: string
}

export interface QuotesResponse {
  quotes: Record<string, QuoteItem>
  ts: string
  note: string
}

export interface DataCacheItem {
  symbol: string
  code?: string
  market?: string
  rows: number
  start: string | null
  end: string | null
  size_kb: number
}

// --------------------------------------------------------------------------- //
// 策略
// --------------------------------------------------------------------------- //

export interface StrategyParam {
  name: string
  type: 'int' | 'float' | 'bool' | 'str' | 'choice' | string
  default: unknown
  min?: number | null
  max?: number | null
  step?: number | null
  choices?: unknown[]
  description?: string
}

export interface Strategy {
  id: string
  name: string
  description: string
  category: string
  params: StrategyParam[]
}

// --------------------------------------------------------------------------- //
// 用户自建策略（条件拼装）
// --------------------------------------------------------------------------- //

/** 操作数：价格字段 / 指标 / 常数 */
export type OperandSpec =
  | { kind: 'field'; field: string }
  | { kind: 'indicator'; name: string; params: Record<string, number> }
  | { kind: 'const'; value: number }

/** 一条条件：type 决定比较方式 */
export interface ConditionSpec {
  type: 'gt' | 'gte' | 'lt' | 'lte' | 'cross_above' | 'cross_below' | string
  left: OperandSpec
  right: OperandSpec
}

/** 条件拼装策略 spec（与后端 parse_spec 白名单对应） */
export interface CustomSpec {
  version?: number
  description?: string
  category?: string
  entry: ConditionSpec[]
  exit: ConditionSpec[]
  position_pct?: number
  stop_loss?: number
  take_profit?: number
}

/** v2 蓝图节点图 spec（与后端 quant/strategy/custom_graph.py 白名单对应） */
export interface CustomGraphNode {
  id: string
  type: string
  params?: Record<string, number>
  value?: number
  pos?: { x: number; y: number }
}

export interface CustomGraphEdge {
  from: string
  to: string
  to_port?: 'in' | 'left' | 'right'
}

export interface CustomGraphSpec {
  version: 2
  description?: string
  category?: string
  position_pct?: number
  stop_loss?: number
  take_profit?: number
  nodes: CustomGraphNode[]
  edges: CustomGraphEdge[]
  outputs: { entry: string; exit: string }
}

export type AnyStrategySpec = CustomSpec | CustomGraphSpec

export interface CustomStrategy {
  id: number
  user_id: number
  name: string
  description: string
  category: string
  spec: CustomSpec | CustomGraphSpec | null
  enabled: boolean
  created_at?: string | null
  updated_at?: string | null
}

// --------------------------------------------------------------------------- //
// 回测
// --------------------------------------------------------------------------- //

export interface CostConfig {
  commission_rate: number
  min_commission: number
  stamp_tax_rate: number
  transfer_fee_rate: number
  slippage_bps: number
  stamp_both_sides?: boolean
}

export interface RiskRuleInput {
  id: string
  enabled?: boolean
  limit?: number | null
  max_orders?: number | null
  window_seconds?: number | null
  symbols?: string[] | null
}

export interface BacktestRequest {
  strategy_id: string
  symbols: string[]
  start?: string | null
  end?: string | null
  initial_cash?: number
  params?: Record<string, unknown>
  cost?: CostConfig
  risk?: RiskRuleInput[] | null
  benchmark?: string | null
  risk_free_rate?: number
}

export interface PaperStartRequest extends BacktestRequest {
  mode: 'replay' | 'live'
  poll_interval?: number
  replay_days?: number
  replay_interval?: number
  respect_market_hours?: boolean
}

export type RunStatus = 'pending' | 'running' | 'finished' | 'failed' | 'cancelled' | string

export interface Metrics {
  total_return?: number
  annual_return?: number
  volatility?: number
  sharpe?: number
  sortino?: number
  max_drawdown?: number
  max_drawdown_days?: number
  calmar?: number
  win_rate?: number
  profit_factor?: number
  trade_count?: number
  win_count?: number
  loss_count?: number
  avg_win?: number
  avg_loss?: number
  expectancy?: number
  total_fees?: number
  turnover?: number
  exposure?: number
  start?: string | null
  end?: string | null
  initial_equity?: number
  final_equity?: number
  benchmark_return?: number | null
  excess_return?: number | null
  monthly_returns?: Record<string, number>
  [key: string]: unknown
}

export interface RunSummary {
  run_id: string
  kind: 'backtest' | 'paper' | string
  strategy_id: string
  symbols: string[]
  status: RunStatus
  progress: number
  metrics: Metrics
  error?: string | null
  created_at?: string | null
  finished_at?: string | null
  elapsed_seconds: number
}

export interface EquityPoint {
  dt: string
  cash: number
  frozen_cash: number
  market_value: number
  total_equity: number
  initial_cash: number
  realized_pnl: number
  unrealized_pnl: number
  total_fees: number
  total_return: number
  cash_ratio: number
  position_ratio: number
  position_count: number
}

export interface FillRecord {
  id: string
  order_id: string
  symbol: string
  side: 'buy' | 'sell' | string
  quantity: number
  price: number
  gross: number
  fees: {
    commission: number
    stamp_tax: number
    transfer_fee: number
    slippage: number
    total: number
  }
  cash_flow: number
  realized_pnl: number
  dt: string
  strategy: string
  tag: string
}

export interface OrderRecord {
  id: string
  symbol: string
  side: 'buy' | 'sell' | string
  quantity: number
  order_type: string
  limit_price: number | null
  tif: string
  status: string
  filled_quantity: number
  avg_fill_price: number
  fees: Record<string, number>
  created_at: string
  submitted_at?: string | null
  updated_at?: string | null
  strategy: string
  tag: string
  reason: string
  meta?: Record<string, unknown>
}

export interface PositionRecord {
  symbol: string
  quantity: number
  available: number
  avg_cost: number
  last_price: number
  cost_value: number
  market_value: number
  unrealized_pnl: number
  unrealized_pct: number
  realized_pnl: number
  total_pnl: number
  opened_at?: string | null
}

export interface LogEntry {
  dt?: string
  time?: string
  level?: string
  message?: string
  [key: string]: unknown
}

export interface RunDetail extends RunSummary {
  config: Record<string, unknown>
  equity_curve: EquityPoint[]
  trades: FillRecord[]
  orders: OrderRecord[]
  positions: PositionRecord[]
  logs: LogEntry[]
}

export interface RunAccepted {
  run_id: string
  status: string
}

// --------------------------------------------------------------------------- //
// 纸盘
// --------------------------------------------------------------------------- //

export interface PaperState {
  run_id: string
  strategy_id: string
  symbols: string[]
  mode: 'replay' | 'live' | string
  status: string
  started_at?: string | null
  last_prices: Record<string, number>
  cash: number
  frozen_cash: number
  equity: number
  total_return: number
  positions: PositionRecord[]
  open_orders: OrderRecord[]
  recent_fills: FillRecord[]
  equity_curve: EquityPoint[]
  error?: string | null
  steps: number
}

// --------------------------------------------------------------------------- //
// 风控
// --------------------------------------------------------------------------- //

export interface RiskRule {
  id: string
  name: string
  description: string
  enabled: boolean
  severity: string
  params: Record<string, unknown>
  trigger_count: number
}

export interface RiskPresetRule {
  id: string
  enabled?: boolean
  limit?: number
  max_orders?: number
  window_seconds?: number
  symbols?: string[]
}

export interface RiskPresets {
  presets: Record<string, RiskPresetRule[]>
  default: string
}

// --------------------------------------------------------------------------- //
// 系统
// --------------------------------------------------------------------------- //

export interface Health {
  status: string
  version: string
  env: string
  database: boolean
  uptime_seconds: number
}

export interface SystemInfo {
  name: string
  version: string
  env: string
  python: string
  database: string
  data_provider: string
  available_providers: string[]
  encryption: Record<string, unknown>
  auth: Record<string, unknown>
  paper_running: boolean
  strategies: string[]
  warnings: string[]
}

export interface AuditItem {
  id: number
  ts: string
  actor: string
  action: string
  target: string
  detail: Record<string, unknown>
  ip: string
  prev_hash: string
  hash: string
}

export interface AuditVerify {
  valid: boolean
  checked: number
  message: string
}

export interface Credential {
  id: number
  name: string
  kind: 'broker' | 'datasource' | string
  description: string
  created_at?: string | null
  updated_at?: string | null
}

export interface Message {
  message: string
  detail: Record<string, unknown>
}

// --------------------------------------------------------------------------- //
// WebSocket
// --------------------------------------------------------------------------- //

export interface WsEvent<T = Record<string, unknown>> {
  type: string
  source?: string
  ts: string
  payload: T
}

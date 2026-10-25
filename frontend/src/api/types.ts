import type { components } from './schema'

type Schemas = components['schemas']

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
  approval_status?: 'approved' | 'pending' | 'rejected' | string
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

export interface IndexQuote {
  id: string
  name: string
  market: string
  currency: string
  price: number
  prev_close: number | null
  change: number | null
  change_pct: number | null
}

export interface InstrumentInfo {
  symbol: string
  code: string
  market: string
  currency: string
  currency_symbol: string
  name: string
  board: string
  limit_pct: number
  lot_size: number
  t_plus_1: boolean
  price_digits: number
  price: number
  change: number | null
  change_pct: number | null
  prev_close: number | null
  volume: number | null
  amount: number | null
  limit_up: number | null
  limit_down: number | null
  source: string
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

export interface Ranking {
  market: string
  sort: string
  order: string
  period: string
  items: QuoteItem[]
  note: string
  ts: string
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
  version: number
  source_strategy_id?: number | null
  source_version?: number | null
  forked_at?: string | null
  update_available?: boolean
  created_at?: string | null
  updated_at?: string | null
}

export interface SharedStrategy {
  share_id: number
  share_type: 'public' | 'user' | string
  strategy_id: number
  name: string
  description: string
  category: string
  spec: CustomSpec | CustomGraphSpec | null
  version: number
  owner_username: string
  updated_at?: string | null
  fork_id?: number | null
  fork_version?: number | null
  update_available: boolean
}

export interface StrategyShare {
  id: number
  scope: 'public' | 'user' | string
  target_username?: string | null
  created_at?: string | null
}

export interface AdminStrategy extends CustomStrategy {
  owner_username: string
  share_count: number
  fork_count: number
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

export type BarInterval = '1d' | '1m' | '5m' | '15m' | '30m' | '60m'

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
  bar_interval?: BarInterval
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

export type RunSummary = Omit<Schemas['RunSummary'], 'kind' | 'status' | 'metrics' | 'symbols'> & {
  kind: 'backtest' | 'paper' | string
  status: RunStatus
  metrics: Metrics
  symbols: string[]
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

export type RunDetail = Omit<
  Schemas['RunDetail'],
  | 'kind'
  | 'status'
  | 'metrics'
  | 'config'
  | 'equity_curve'
  | 'trades'
  | 'orders'
  | 'positions'
  | 'logs'
  | 'symbols'
> & {
  kind: 'backtest' | 'paper' | string
  status: RunStatus
  metrics: Metrics
  symbols: string[]
  config: Record<string, unknown>
  equity_curve: EquityPoint[]
  trades: FillRecord[]
  orders: OrderRecord[]
  positions: PositionRecord[]
  logs: LogEntry[]
}

export type RunAccepted = Schemas['RunAccepted']

// --------------------------------------------------------------------------- //
// 纸盘
// --------------------------------------------------------------------------- //

export type PaperState = Omit<
  Schemas['PaperStateOut'],
  'mode' | 'last_prices' | 'positions' | 'open_orders' | 'recent_fills' | 'equity_curve' | 'symbols'
> & {
  mode: 'replay' | 'live' | string
  symbols: string[]
  last_prices: Record<string, number>
  positions: PositionRecord[]
  open_orders: OrderRecord[]
  recent_fills: FillRecord[]
  equity_curve: EquityPoint[]
}

// --------------------------------------------------------------------------- //
// 实盘
// --------------------------------------------------------------------------- //

export interface LiveAdapter {
  name: string
  supports_sandbox: boolean
  requires_api_key: boolean
  markets: string[]
}

export interface LiveStartRequest extends BacktestRequest {
  adapter: 'ccxt' | 'miniqmt' | string
  credential_id: number
  sandbox: boolean
  confirm_live?: boolean
  poll_interval?: number
  warmup_days?: number
}

export type LiveState = Omit<
  Schemas['LiveStateOut'],
  | 'mode'
  | 'last_prices'
  | 'positions'
  | 'open_orders'
  | 'recent_fills'
  | 'equity_curve'
  | 'credential_id'
  | 'symbols'
> & {
  mode: 'replay' | 'live' | string
  symbols: string[]
  last_prices: Record<string, number>
  positions: PositionRecord[]
  open_orders: OrderRecord[]
  recent_fills: FillRecord[]
  equity_curve: EquityPoint[]
  credential_id?: number | null
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
// 场外基金
// --------------------------------------------------------------------------- //

export interface FundNavPoint {
  date: string
  nav: number
}

export interface FundNav {
  symbol: string
  name: string
  points: FundNavPoint[]
}

export interface FundTrade {
  id: number
  symbol: string
  name: string
  side: 'subscribe' | 'redeem' | string
  trade_date: string | null
  confirm_date?: string | null
  nav: number
  shares: number
  amount: number
  fee_rate: number
  fee: number
  note: string
  created_at?: string | null
}

export interface FundHolding {
  symbol: string
  name: string
  shares: number
  avg_cost: number
  cost: number
  nav: number
  market_value: number
  unrealized_pnl: number
  realized_pnl: number
  total_pnl: number
  return_pct: number
  trades: number
}

export interface FundPortfolio {
  holdings: FundHolding[]
  totals: {
    cost?: number
    market_value?: number
    unrealized_pnl?: number
    realized_pnl?: number
    total_pnl?: number
    return_pct?: number
    count?: number
  }
  nav_note: string
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

// --------------------------------------------------------------------------- //
// 新闻 / 政策
// --------------------------------------------------------------------------- //

export type NewsItem = Schemas['NewsItemOut']
export type NewsItemInput = Schemas['NewsItemIn']
export type NewsFactor = Schemas['NewsFactorOut']

export type NewsFetchStatus = Schemas['NewsFetchStatusOut']

export interface CaptchaChallenge {
  token: string
  image: string
  expires_in: number
}

export interface RegisterRequest {
  username: string
  password: string
  captcha_token: string
  captcha_answer: string
}

export interface Feedback {
  id: number
  user_id?: number | null
  username: string
  category: 'suggestion' | 'bug' | 'other' | string
  content: string
  status: 'new' | 'read' | 'resolved' | string
  created_at?: string | null
  updated_at?: string | null
}

// --------------------------------------------------------------------------- //
// C++ 交易网关
// --------------------------------------------------------------------------- //

export type GatewayStatus = Schemas['GatewayStatusOut']

// --------------------------------------------------------------------------- //
// 参数寻优
// --------------------------------------------------------------------------- //

export type OptimizeStatus = 'queued' | 'running' | 'finished' | 'failed' | 'cancelled'
export type OptimizeMode = 'search' | 'walk_forward'
export type OptimizeObjective =
  'sharpe' | 'sortino' | 'calmar' | 'annual_return' | 'total_return' | 'win_rate' | 'profit_factor'

/** 参数候选：枚举数组、等差区间，或直接给固定值。 */
export type ParamCandidates = unknown[] | { start: number; stop: number; step?: number } | number | string

export interface OptimizeStartRequest {
  strategy_id: string
  symbols: string[]
  start: string
  end: string
  /** 参数名 → 候选值。固定值用 `{ position_pct: 0.95 }` 这种写法。 */
  space: Record<string, ParamCandidates>
  /** 不参与寻优的固定参数。 */
  params?: Record<string, unknown>
  objective?: OptimizeObjective
  initial_cash?: number
  cost?: Record<string, unknown> | null
  risk?: Record<string, unknown>[] | null
  benchmark?: string | null
  /** 成交次数门槛：低于该值的组合不参与排名。 */
  min_trades?: number
  method?: 'grid' | 'random'
  sample_size?: number | null
  seed?: number | null
  walk_forward?: boolean
  walk_forward_options?: { train_days?: number; test_days?: number; step_days?: number | null }
}

export interface OptimizeJobSummary {
  job_id: string
  mode: OptimizeMode
  status: OptimizeStatus
  progress: number
  message: string
  created_at: string
  finished_at?: string | null
  error?: string | null
}

export interface OptimizeTrial {
  index: number
  params: Record<string, unknown>
  status: 'ok' | 'failed' | 'skipped'
  metrics: Record<string, number>
  error?: string | null
  run_id: string
}

export interface OptimizeFold {
  fold: {
    index: number
    train_start: string
    train_end: string
    test_start: string
    test_end: string
    train_days: number
    test_days: number
  }
  best_params: Record<string, unknown>
  train_metrics: Record<string, number>
  test_metrics: Record<string, number>
  trial_count: number
  error?: string | null
}

export interface OptimizeJob extends OptimizeJobSummary {
  summary: Record<string, unknown>
  best: OptimizeTrial | null
  trials: OptimizeTrial[]
  folds: OptimizeFold[]
  total_trials: number
}

export interface OptimizeObjectiveOption {
  key: OptimizeObjective
  label: string
}

// --------------------------------------------------------------------------- //
// 组合（权重优化 + 指数成分股）
// --------------------------------------------------------------------------- //

export type PortfolioMethod = 'equal_weight' | 'inverse_vol' | 'risk_parity' | 'min_variance' | 'max_sharpe'

export interface PortfolioOptimizeRequest {
  symbols: string[]
  method?: PortfolioMethod
  start?: string | null
  end?: string | null
  lookback_days?: number
  risk_free_rate?: number
  max_weight?: number
  min_weight?: number
}

export interface PortfolioOptimizeResult {
  method: string
  method_label: string
  weights: Record<string, number>
  expected_return: number
  volatility: number
  sharpe: number
  diversification: number
  note: string
  diagnostics: Record<string, number>
  price_start?: string | null
  price_end?: string | null
}

export interface PortfolioMethodOption {
  key: PortfolioMethod
  label: string
}

export interface IndexSummary {
  index_code: string
  entries: number
  active: number
  earliest?: string | null
  sources: string[]
}

export interface IndexMember {
  symbol: string
  start_date?: string | null
  end_date?: string | null
  source: string
}

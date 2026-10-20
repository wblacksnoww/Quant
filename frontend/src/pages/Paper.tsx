/** 模拟盘：启动会话、实时状态、成交与权益。 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, paperApi, riskApi, strategyApi } from '../api/client'
import type {
  PaperStartRequest,
  PaperState,
  RiskPresets,
  RiskRuleInput,
  RunSummary,
  Strategy,
} from '../api/types'
import { EquityChart } from '../components/Charts'
import { CustomStrategySelect } from '../components/CustomStrategySelect'
import { SwitchStrategyModal } from '../components/SwitchStrategy'
import { IconPlay, IconRefresh, IconStop } from '../components/icons'
import { Badge, Banner, Button, Card, Empty, Field, Loading, Stat, StatusBadge } from '../components/ui'
import { useEvents } from '../store/events'
import { notify } from '../store/toast'
import {
  cx,
  durationText,
  fmtDateTime,
  fmtMoney,
  fmtNumber,
  fmtSignedPct,
  parseSymbols,
} from '../utils/format'

function defaultsFor(strategy: Strategy | undefined): Record<string, unknown> {
  if (!strategy) return {}
  const values: Record<string, unknown> = {}
  for (const param of strategy.params) values[param.name] = param.default
  return values
}

export function PaperPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [presets, setPresets] = useState<RiskPresets | null>(null)
  const [sessions, setSessions] = useState<PaperState[]>([])
  const [activeRun, setActiveRun] = useState<string | null>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [togglingPause, setTogglingPause] = useState(false)
  const [resumingRun, setResumingRun] = useState<string | null>(null)
  const [switchOpen, setSwitchOpen] = useState(false)
  const [error, setError] = useState('')

  const [strategyId, setStrategyId] = useState('')
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [symbolsText, setSymbolsText] = useState('600519, 000001')
  const [initialCash, setInitialCash] = useState(1_000_000)
  const [mode, setMode] = useState<'replay' | 'live'>('replay')
  const [replayDays, setReplayDays] = useState(120)
  const [replayInterval, setReplayInterval] = useState(0.35)
  const [pollInterval, setPollInterval] = useState(5)
  const [respectMarketHours, setRespectMarketHours] = useState(true)
  const [riskPreset, setRiskPreset] = useState('balanced')
  const [advanced, setAdvanced] = useState(false)
  // 条件拼装策略：选中的「我的拼装策略」id
  const [selectedCustom, setSelectedCustom] = useState<number | null>(null)

  const isCustomRule = strategyId === 'custom_rule'

  const strategy = useMemo(
    () => strategies.find((item) => item.id === strategyId),
    [strategies, strategyId],
  )

  // 通用参数表单（拼装策略的 spec 由下拉携带，不渲染 JSON 输入）
  const formParams = useMemo(
    () => (strategy?.params ?? []).filter((param) => param.type !== 'json'),
    [strategy],
  )

  const running = sessions.length > 0
  // 当前选中的会话；没有选中时回落到第一个运行中的会话
  const state = useMemo(
    () => sessions.find((item) => item.run_id === activeRun) ?? sessions[0] ?? null,
    [sessions, activeRun],
  )

  const loadSessions = useCallback(async () => {
    try {
      const list = await paperApi.sessions()
      setSessions(list)
      setActiveRun((current) =>
        current && list.some((item) => item.run_id === current)
          ? current
          : (list[0]?.run_id ?? null),
      )
    } catch {
      // 状态轮询失败不弹窗，下一次重试
    }
  }, [])

  const loadRuns = useCallback(async () => {
    try {
      setRuns(await paperApi.runs())
    } catch {
      // 历史记录失败不阻塞实时状态
    }
  }, [])

  useEffect(() => {
    let alive = true
    void (async () => {
      const [strategyResult, presetResult] = await Promise.allSettled([
        strategyApi.list(),
        riskApi.presets(),
      ])
      if (!alive) return
      const strategyValue = strategyResult.status === 'fulfilled' ? strategyResult.value : []
      setStrategies(strategyValue)
      if (presetResult.status === 'fulfilled') {
        setPresets(presetResult.value)
        setRiskPreset(presetResult.value.default || 'balanced')
      }
      if (strategyValue[0]) {
        setStrategyId(strategyValue[0].id)
        setParams(defaultsFor(strategyValue[0]))
      }
      await Promise.all([loadSessions(), loadRuns()])
      if (alive) setLoading(false)
    })()
    return () => {
      alive = false
    }
  }, [loadSessions, loadRuns])

  // 运行中高频轮询，结束后降低频率
  useEffect(() => {
    const interval = running ? 2000 : 10000
    const timer = window.setInterval(() => {
      void loadSessions()
    }, interval)
    return () => window.clearInterval(timer)
  }, [running, loadSessions])

  // 事件驱动的即时刷新
  const lastEvent = useEvents((store) => store.last)
  useEffect(() => {
    if (!lastEvent) return
    const payload = lastEvent.payload as { run_id?: string; status?: string }
    if (lastEvent.type === 'run.finished' || lastEvent.type === 'run.failed') {
      void loadSessions()
      void loadRuns()
      return
    }
    if (state && payload.run_id && payload.run_id === state.run_id) {
      // 成交/状态事件：稍等一拍再拉快照，确保引擎已写入
      const timer = window.setTimeout(() => void loadSessions(), 250)
      return () => window.clearTimeout(timer)
    }
  }, [lastEvent, state, loadSessions, loadRuns])

  const start = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    const symbols = parseSymbols(symbolsText)
    if (!strategyId) {
      setError('请选择策略')
      return
    }
    if (isCustomRule && !selectedCustom) {
      setError('请选择要运行的拼装策略；若还没有，去策略库用条件拼装器创建')
      notify('warning', '请先选择拼装策略')
      return
    }
    if (symbols.length === 0) {
      setError('请填写至少一个标的')
      return
    }
    const cleanParams: Record<string, unknown> = {}
    for (const spec of strategy?.params ?? []) {
      if (spec.type === 'json') continue
      const value = params[spec.name]
      if (value === '' || value === undefined || value === null) continue
      cleanParams[spec.name] = value
    }
    if (isCustomRule && selectedCustom) cleanParams.custom_strategy_id = selectedCustom
    const riskRules: RiskRuleInput[] | null = presets?.presets[riskPreset]
      ? presets.presets[riskPreset].map((rule) => ({
          id: rule.id,
          enabled: rule.enabled ?? true,
          limit: rule.limit,
          max_orders: rule.max_orders,
          window_seconds: rule.window_seconds,
          symbols: rule.symbols,
        }))
      : null

    const body: PaperStartRequest = {
      strategy_id: strategyId,
      symbols,
      initial_cash: initialCash,
      params: cleanParams,
      risk: riskRules,
      mode,
      replay_days: replayDays,
      replay_interval: replayInterval,
      poll_interval: pollInterval,
      respect_market_hours: respectMarketHours,
    }

    setStarting(true)
    try {
      const accepted = await paperApi.start(body)
      setActiveRun(accepted.run_id)
      notify('success', '模拟盘已启动')
      window.setTimeout(() => void loadSessions(), 600)
      window.setTimeout(() => void loadRuns(), 1500)
    } catch (err) {
      const message = err instanceof ApiError ? err.message : '启动失败'
      setError(message)
      notify('error', message)
    } finally {
      setStarting(false)
    }
  }

  const stop = async (runId?: string) => {
    setStopping(true)
    try {
      const message = await paperApi.stop(runId)
      notify('info', message.message)
      window.setTimeout(() => {
        void loadSessions()
        void loadRuns()
      }, 800)
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '停止失败')
    } finally {
      setStopping(false)
    }
  }

  const continueRun = async (runId: string) => {
    setResumingRun(runId)
    try {
      const accepted = await paperApi.continueRun(runId)
      setActiveRun(accepted.run_id)
      notify('success', '已从停止处继续，账户与持仓已恢复')
      window.setTimeout(() => {
        void loadSessions()
        void loadRuns()
      }, 600)
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '继续失败')
    } finally {
      setResumingRun(null)
    }
  }

  const togglePause = async (runId: string, paused: boolean) => {
    setTogglingPause(true)
    try {
      const message = paused ? await paperApi.resume(runId) : await paperApi.pause(runId)
      notify('info', message.message)
      window.setTimeout(() => void loadSessions(), 400)
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '操作失败')
    } finally {
      setTogglingPause(false)
    }
  }

  const changeStrategy = (id: string) => {
    setStrategyId(id)
    setParams(defaultsFor(strategies.find((item) => item.id === id)))
  }

  if (loading) return <Loading text="正在加载模拟盘…" />

  return (
    <div className="col" style={{ gap: 18 }}>
      {sessions.length > 0 && (
        <Card
          title="运行中会话"
          description="同一账号可同时运行多个模拟盘，每个会话使用独立的虚拟资金"
          actions={<Badge tone="success">{sessions.length} 个运行中</Badge>}
          flush
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>运行 ID</th>
                  <th>策略</th>
                  <th>标的</th>
                  <th>模式</th>
                  <th className="right">权益</th>
                  <th className="right">收益率</th>
                  <th className="right">操作</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((item) => {
                  const selected = item.run_id === state?.run_id
                  return (
                    <tr
                      key={item.run_id}
                      style={{
                        cursor: 'pointer',
                        background: selected ? 'var(--accent-soft)' : undefined,
                      }}
                      onClick={() => setActiveRun(item.run_id)}
                    >
                      <td className="mono tiny">{item.run_id}</td>
                      <td>{item.strategy_id}</td>
                      <td className="mono tiny">{item.symbols.join(' · ')}</td>
                      <td>{item.mode === 'replay' ? '回放' : '实时'}</td>
                      <td className="right num">{fmtMoney(item.equity)}</td>
                      <td className={`right num ${item.total_return >= 0 ? 'up' : 'down'}`}>
                        {fmtSignedPct(item.total_return)}
                      </td>
                      <td className="right">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={(event) => {
                            event.stopPropagation()
                            void stop(item.run_id)
                          }}
                        >
                          停止
                        </Button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="grid grid-sidebar">
        <Card
          title="启动模拟盘"
          description="与回测共用撮合、风控与费用模型；每个会话独立虚拟资金，同一账号可同时运行多个"
          actions={<Badge tone={running ? 'success' : 'neutral'}>{running ? '运行中' : '空闲'}</Badge>}
        >
          <form className="col" style={{ gap: 14 }} onSubmit={start}>
            <div className="form-grid">
              <Field label="策略">
                <select className="select" value={strategyId} onChange={(event) => changeStrategy(event.target.value)}>
                  {strategies.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}（{item.id}）
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="模式" hint={mode === 'replay' ? '按固定间隔重放最近 N 个交易日' : '盘中轮询实时价，非交易时段休眠'}>
                <select className="select" value={mode} onChange={(event) => setMode(event.target.value as 'replay' | 'live')}>
                  <option value="replay">回放（历史行情）</option>
                  <option value="live">实时（盘中轮询）</option>
                </select>
              </Field>
            </div>

            <div className="form-grid">
              <Field label="标的代码" hint="逗号分隔，自动补零去重">
                <input
                  className="input mono"
                  value={symbolsText}
                  onChange={(event) => setSymbolsText(event.target.value)}
                  placeholder="600519, 000001"
                />
              </Field>

              {isCustomRule && (
                <CustomStrategySelect value={selectedCustom} onChange={setSelectedCustom} />
              )}
              <Field label="初始资金（元）">
                <input
                  className="input mono"
                  type="number"
                  min={10000}
                  step={10000}
                  value={initialCash}
                  onChange={(event) => setInitialCash(Number(event.target.value))}
                />
              </Field>
            </div>

            {mode === 'replay' ? (
              <div className="form-grid-3">
                <Field label="回放天数">
                  <input
                    className="input mono"
                    type="number"
                    min={10}
                    max={1000}
                    value={replayDays}
                    onChange={(event) => setReplayDays(Number(event.target.value))}
                  />
                </Field>
                <Field label="每根间隔（秒）" hint="0.35 ≈ 每根 350ms">
                  <input
                    className="input mono"
                    type="number"
                    min={0}
                    max={10}
                    step={0.05}
                    value={replayInterval}
                    onChange={(event) => setReplayInterval(Number(event.target.value))}
                  />
                </Field>
                <Field label="风控预设">
                  <select className="select" value={riskPreset} onChange={(event) => setRiskPreset(event.target.value)}>
                    {(presets ? Object.keys(presets.presets) : ['balanced']).map((key) => (
                      <option key={key} value={key}>
                        {key === 'conservative' ? '保守' : key === 'balanced' ? '均衡' : key === 'aggressive' ? '激进' : key}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
            ) : (
              <div className="form-grid-3">
                <Field label="轮询间隔（秒）">
                  <input
                    className="input mono"
                    type="number"
                    min={0.5}
                    max={600}
                    step={0.5}
                    value={pollInterval}
                    onChange={(event) => setPollInterval(Number(event.target.value))}
                  />
                </Field>
                <Field label="风控预设">
                  <select className="select" value={riskPreset} onChange={(event) => setRiskPreset(event.target.value)}>
                    {(presets ? Object.keys(presets.presets) : ['balanced']).map((key) => (
                      <option key={key} value={key}>
                        {key === 'conservative' ? '保守' : key === 'balanced' ? '均衡' : key === 'aggressive' ? '激进' : key}
                      </option>
                    ))}
                  </select>
                </Field>
                <div className="field">
                  <span className="label">交易时段</span>
                  <label className="checkbox" style={{ paddingTop: 7 }}>
                    <input
                      type="checkbox"
                      checked={respectMarketHours}
                      onChange={(event) => setRespectMarketHours(event.target.checked)}
                    />
                    仅盘中运行
                  </label>
                </div>
              </div>
            )}

            {strategy && formParams.length > 0 && (
              <>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  style={{ alignSelf: 'flex-start', paddingLeft: 0 }}
                  onClick={() => setAdvanced((prev) => !prev)}
                >
                  {advanced ? '收起策略参数' : '展开策略参数'}
                </button>
                {advanced && (
                  <div className="form-grid-3">
                    {formParams.map((param) => {
                      const value = params[param.name]
                      return (
                        <div className="field" key={param.name}>
                          <span className="label">{param.name}</span>
                          {param.type === 'bool' ? (
                            <label className="checkbox">
                              <input
                                type="checkbox"
                                checked={Boolean(value)}
                                onChange={(event) => setParams({ ...params, [param.name]: event.target.checked })}
                              />
                              启用
                            </label>
                          ) : param.type === 'choice' ? (
                            <select
                              className="select"
                              value={String(value ?? '')}
                              onChange={(event) => {
                                const raw = event.target.value
                                const matched = (param.choices ?? []).find((choice) => String(choice) === raw)
                                setParams({ ...params, [param.name]: matched ?? raw })
                              }}
                            >
                              {(param.choices ?? []).map((choice) => (
                                <option key={String(choice)} value={String(choice)}>
                                  {String(choice)}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <input
                              className="input mono"
                              type="number"
                              step={param.step ?? (param.type === 'int' ? 1 : 0.01)}
                              min={param.min ?? undefined}
                              max={param.max ?? undefined}
                              value={value === undefined || value === null ? '' : String(value)}
                              onChange={(event) =>
                                setParams({
                                  ...params,
                                  [param.name]: event.target.value === '' ? '' : Number(event.target.value),
                                })
                              }
                            />
                          )}
                          <span className="hint">{param.description || ''}</span>
                        </div>
                      )
                    })}
                  </div>
                )}
              </>
            )}

            {error && <Banner kind="danger">{error}</Banner>}
            {running && (
              <Banner kind="info">
                同一账号可同时运行多个模拟会话（各自独立虚拟资金）；「暂停」保留持仓与进度，可随时继续。
              </Banner>
            )}

            <div className="row between">
              <span className="tiny faint">实时模式需要数据源支持快照，否则回退到最近收盘价。</span>
              <div className="row" style={{ gap: 8 }}>
                {running && (
                  <Button variant="danger" loading={stopping} onClick={() => void stop()}>
                    <IconStop /> 停止
                  </Button>
                )}
                <Button type="submit" variant="primary" loading={starting}>
                  <IconPlay /> 启动
                </Button>
              </div>
            </div>
          </form>
        </Card>

        <Card
          title="会话状态"
          actions={
            <Button size="sm" variant="ghost" onClick={() => void loadSessions()}>
              <IconRefresh />
            </Button>
          }
        >
          {state ? (
            <div className="col" style={{ gap: 12 }}>
              <div className="row between">
                <StatusBadge status={state.status} />
                <Badge tone="accent">{state.mode === 'replay' ? '回放' : '实时'}</Badge>
              </div>
              <dl className="kv">
                <dt>运行 ID</dt>
                <dd className="mono tiny">{state.run_id}</dd>
                <dt>策略</dt>
                <dd>{state.strategy_id}</dd>
                <dt>标的</dt>
                <dd className="mono">{state.symbols.join(', ')}</dd>
                <dt>启动时间</dt>
                <dd>{fmtDateTime(state.started_at)}</dd>
                <dt>已推进</dt>
                <dd>{state.steps} 步</dd>
                <dt>最后价格</dt>
                <dd className="mono tiny">
                  {Object.entries(state.last_prices)
                    .map(([symbol, price]) => `${symbol} ${fmtMoney(price)}`)
                    .join(' · ') || '—'}
                </dd>
              </dl>
              {state.error && <Banner kind="danger">{state.error}</Banner>}
              {(state.status === 'running' || state.status === 'paused') && (
                <>
                  {state.status === 'running' && (
                    <Button block onClick={() => setSwitchOpen(true)}>
                      切换策略（下一轮生效）
                    </Button>
                  )}
                  <Button
                    block
                    loading={togglingPause}
                    onClick={() =>
                      void togglePause(state.run_id, state.status === 'paused')
                    }
                  >
                    {state.status === 'paused' ? '继续运行' : '暂停（保留进度）'}
                  </Button>
                  <Button variant="danger" block loading={stopping} onClick={() => void stop(state?.run_id)}>
                    <IconStop /> 停止当前会话（结束）
                  </Button>
                </>
              )}
            </div>
          ) : (
            <Empty
              title="尚未启动"
              hint="左侧配置策略与标的，启动后这里显示实时状态。"
            />
          )}
        </Card>
      </div>

      {state && (
        <>
          <div className="grid grid-4">
            <Stat
              label="总权益"
              value={fmtMoney(state.equity)}
              sub={`现金 ${fmtMoney(state.cash)}`}
              tone={state.total_return >= 0 ? 'up' : 'down'}
            />
            <Stat
              label="总收益率"
              value={fmtSignedPct(state.total_return)}
              sub={`冻结 ${fmtMoney(state.frozen_cash)}`}
              tone={state.total_return >= 0 ? 'up' : 'down'}
            />
            <Stat label="持仓数量" value={`${state.positions.length} 只`} sub={`挂单 ${state.open_orders.length} 笔`} />
            <Stat label="成交笔数" value={`${state.recent_fills.length} 笔`} sub={`已推进 ${state.steps} 步`} />
          </div>

          <Card title="实时权益曲线" description="最近 500 个快照">
            <EquityChart points={state.equity_curve} height={320} showDrawdown={false} />
          </Card>

          <div className="grid grid-2">
            <Card title="持仓" flush>
              {state.positions.length === 0 ? (
                <Empty title="空仓" />
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>代码</th>
                        <th className="right">数量</th>
                        <th className="right">可用</th>
                        <th className="right">成本</th>
                        <th className="right">现价</th>
                        <th className="right">浮动盈亏</th>
                      </tr>
                    </thead>
                    <tbody>
                      {state.positions.map((position) => (
                        <tr key={position.symbol}>
                          <td className="strong mono">{position.symbol}</td>
                          <td className="right num">{position.quantity.toLocaleString('zh-CN')}</td>
                          <td className="right num">{position.available.toLocaleString('zh-CN')}</td>
                          <td className="right num">{fmtMoney(position.avg_cost)}</td>
                          <td className="right num">{fmtMoney(position.last_price)}</td>
                          <td className={cx('right num', position.unrealized_pnl >= 0 ? 'up' : 'down')}>
                            {fmtMoney(position.unrealized_pnl)}（{fmtSignedPct(position.unrealized_pct)}）
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <Card title="挂单" flush>
              {state.open_orders.length === 0 ? (
                <Empty title="没有未完成委托" />
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>时间</th>
                        <th>代码</th>
                        <th>方向</th>
                        <th className="right">数量</th>
                        <th className="right">限价</th>
                        <th>状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {state.open_orders.map((order) => (
                        <tr key={order.id}>
                          <td className="tiny faint">{fmtDateTime(order.created_at)}</td>
                          <td className="strong mono">{order.symbol}</td>
                          <td className={order.side === 'buy' ? 'up' : 'down'}>
                            {order.side === 'buy' ? '买入' : '卖出'}
                          </td>
                          <td className="right num">{order.quantity.toLocaleString('zh-CN')}</td>
                          <td className="right num">{order.limit_price ? fmtMoney(order.limit_price) : '市价'}</td>
                          <td><StatusBadge status={order.status} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>

          <Card title="最近成交" description="最多展示 50 笔" flush>
            {state.recent_fills.length === 0 ? (
              <Empty title="还没有成交" hint="策略触发信号后，成交会实时出现在这里。" />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>代码</th>
                      <th>方向</th>
                      <th className="right">数量</th>
                      <th className="right">价格</th>
                      <th className="right">金额</th>
                      <th className="right">费用</th>
                      <th className="right">已实现盈亏</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...state.recent_fills].reverse().map((fill) => (
                      <tr key={fill.id}>
                        <td className="tiny faint nowrap">{fmtDateTime(fill.dt)}</td>
                        <td className="strong mono">{fill.symbol}</td>
                        <td className={fill.side === 'buy' ? 'up' : 'down'}>
                          {fill.side === 'buy' ? '买入' : '卖出'}
                        </td>
                        <td className="right num">{fill.quantity.toLocaleString('zh-CN')}</td>
                        <td className="right num">{fmtMoney(fill.price)}</td>
                        <td className="right num">{fmtMoney(fill.gross)}</td>
                        <td className="right num tiny">{fmtMoney(fill.fees.total)}</td>
                        <td className={cx('right num', fill.realized_pnl > 0 ? 'up' : fill.realized_pnl < 0 ? 'down' : '')}>
                          {fill.realized_pnl ? fmtMoney(fill.realized_pnl) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      <Card title="历史会话" description="纸盘结果同样落库；停止后可点「继续」从停止处接着跑（同一运行 ID）" flush>
        {runs.length === 0 ? (
          <Empty title="暂无历史会话" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>运行 ID</th>
                  <th>策略</th>
                  <th>标的</th>
                  <th>状态</th>
                  <th className="right">收益率</th>
                  <th className="right">耗时</th>
                  <th>结束时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.run_id}>
                    <td className="mono tiny">{run.run_id}</td>
                    <td className="strong">{run.strategy_id}</td>
                    <td className="mono tiny">{run.symbols.join(', ')}</td>
                    <td><StatusBadge status={run.status} /></td>
                    <td className={cx('right num', Number(run.metrics?.total_return ?? 0) >= 0 ? 'up' : 'down')}>
                      {run.metrics?.total_return !== undefined ? fmtSignedPct(run.metrics.total_return) : '—'}
                    </td>
                    <td className="right num tiny">{durationText(run.elapsed_seconds)}</td>
                    <td className="tiny faint">{fmtDateTime(run.finished_at ?? run.created_at)}</td>
                    <td>
                      {run.status === 'stopped' ||
                      (run.status === 'running' &&
                        !sessions.some((item) => item.run_id === run.run_id)) ? (
                        <Button
                          size="sm"
                          loading={resumingRun === run.run_id}
                          onClick={() => void continueRun(run.run_id)}
                          title="从停止处继续（恢复现金、持仓与回放进度，沿用同一运行 ID）"
                        >
                          继续
                        </Button>
                      ) : (
                        <span className="tiny faint">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <SwitchStrategyModal
        open={switchOpen}
        onClose={() => setSwitchOpen(false)}
        kind="paper"
        current={state?.strategy_id}
        onDone={() => void loadSessions()}
      />
    </div>
  )
}

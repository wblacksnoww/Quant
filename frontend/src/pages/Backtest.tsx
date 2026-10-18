/** 回测页：参数表单 + 任务列表 + 结果分析。 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ApiError, backtestApi, riskApi, strategyApi } from '../api/client'
import type {
  BacktestRequest,
  CostConfig,
  RiskPresets,
  RiskRuleInput,
  RunDetail,
  RunSummary,
  Strategy,
  StrategyParam,
} from '../api/types'
import { EquityChart, MonthlyReturnChart } from '../components/Charts'
import { CustomStrategySelect } from '../components/CustomStrategySelect'
import { IconFlask, IconRefresh, IconStop, IconTrash } from '../components/icons'
import { Badge, Banner, Button, Card, Empty, Field, Loading, Progress, Stat, StatusBadge, Tabs } from '../components/ui'
import { useEvents } from '../store/events'
import { notify } from '../store/toast'
import {
  cx,
  durationText,
  fmtDateTime,
  fmtMoney,
  fmtNumber,
  fmtPct,
  fmtSignedPct,
  parseSymbols,
  todayISO,
  yearsAgoISO,
} from '../utils/format'

type ResultTab = 'trades' | 'orders' | 'positions' | 'logs' | 'config'

const DEFAULT_COST: CostConfig = {
  commission_rate: 3e-4,
  min_commission: 5,
  stamp_tax_rate: 5e-4,
  transfer_fee_rate: 1e-5,
  slippage_bps: 0,
}

function defaultsFor(strategy: Strategy | undefined): Record<string, unknown> {
  if (!strategy) return {}
  const values: Record<string, unknown> = {}
  for (const param of strategy.params) values[param.name] = param.default
  return values
}

function paramBounds(param: StrategyParam): string {
  const parts: string[] = []
  if (param.min !== null && param.min !== undefined) parts.push(`min ${param.min}`)
  if (param.max !== null && param.max !== undefined) parts.push(`max ${param.max}`)
  return parts.join(' · ')
}

function isActive(status: string | undefined): boolean {
  return status === 'running' || status === 'pending' || status === 'queued'
}

export function BacktestPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [presets, setPresets] = useState<RiskPresets | null>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [detailError, setDetailError] = useState('')
  const [detailLoading, setDetailLoading] = useState(false)
  const [tab, setTab] = useState<ResultTab>('trades')
  const [advanced, setAdvanced] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')

  const [strategyId, setStrategyId] = useState('')
  const [symbolsText, setSymbolsText] = useState('600519, 000001')
  const [start, setStart] = useState(yearsAgoISO(3))
  const [end, setEnd] = useState(todayISO())
  const [initialCash, setInitialCash] = useState(1_000_000)
  const [riskPreset, setRiskPreset] = useState('balanced')
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [cost, setCost] = useState<CostConfig>(DEFAULT_COST)
  // 条件拼装策略：选中的「我的拼装策略」id
  const [selectedCustom, setSelectedCustom] = useState<number | null>(null)

  const isCustomRule = strategyId === 'custom_rule'

  const strategy = useMemo(
    () => strategies.find((item) => item.id === strategyId),
    [strategies, strategyId],
  )

  // 通用参数表单要渲染的参数（拼装策略的 spec 由下面专门的下拉处理，不渲染 JSON 输入）
  const formParams = useMemo(
    () => (strategy?.params ?? []).filter((param) => param.type !== 'json'),
    [strategy],
  )

  const refreshRuns = useCallback(async () => {
    try {
      const list = await backtestApi.list(30)
      setRuns(list)
    } catch {
      // 列表刷新失败不打断结果展示
    }
  }, [])

  const selectRun = useCallback(
    (runId: string, updateUrl = true) => {
      setSelectedId(runId)
      setDetail(null)
      setDetailError('')
      setTab('trades')
      if (updateUrl) setSearchParams({ run: runId }, { replace: true })
    },
    [setSearchParams],
  )

  // 首次加载：策略、风控预设、历史记录
  useEffect(() => {
    let alive = true
    void (async () => {
      const [strategyResult, presetResult, runResult] = await Promise.allSettled([
        strategyApi.list(),
        riskApi.presets(),
        backtestApi.list(30),
      ])
      if (!alive) return
      const strategiesValue = strategyResult.status === 'fulfilled' ? strategyResult.value : []
      const runsValue = runResult.status === 'fulfilled' ? runResult.value : []
      setStrategies(strategiesValue)
      setRuns(runsValue)
      if (presetResult.status === 'fulfilled') {
        setPresets(presetResult.value)
        setRiskPreset(presetResult.value.default || 'balanced')
      }
      const requested = searchParams.get('strategy')
      const chosen = strategiesValue.find((item) => item.id === requested) ?? strategiesValue[0]
      if (chosen) {
        setStrategyId(chosen.id)
        setParams(defaultsFor(chosen))
        const customParam = searchParams.get('custom')
        if (chosen.id === 'custom_rule' && customParam && Number.isFinite(Number(customParam))) {
          setSelectedCustom(Number(customParam))
        }
      }
      const runParam = searchParams.get('run')
      const initialRun = runParam ?? runsValue[0]?.run_id
      if (initialRun) selectRun(initialRun, Boolean(runParam))
    })()
    return () => {
      alive = false
    }
    // 仅首次执行：URL 变化由 selectRun 自行处理
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 选中运行后拉取详情
  useEffect(() => {
    if (!selectedId) return
    let alive = true
    setDetailLoading(true)
    backtestApi
      .get(selectedId)
      .then((result) => {
        if (alive) setDetail(result)
      })
      .catch((err: Error) => {
        if (alive) setDetailError(err.message)
      })
      .finally(() => {
        if (alive) setDetailLoading(false)
      })
    return () => {
      alive = false
    }
  }, [selectedId])

  // 运行中轮询（WS 只是加速，轮询保证最终一致）
  const running = isActive(detail?.status)
  useEffect(() => {
    if (!selectedId || !running) return
    const timer = window.setInterval(() => {
      void backtestApi
        .get(selectedId)
        .then((result) => {
          setDetail(result)
          if (!isActive(result.status)) void refreshRuns()
        })
        .catch(() => undefined)
    }, 2500)
    return () => window.clearInterval(timer)
  }, [selectedId, running, refreshRuns])

  // WebSocket：进度与完成事件
  const lastEvent = useEvents((state) => state.last)
  useEffect(() => {
    if (!lastEvent || !selectedId) return
    const payload = lastEvent.payload as { run_id?: string; progress?: number; error?: string }
    if (payload.run_id !== selectedId) return
    if (lastEvent.type === 'run.progress') {
      const progress = Number(payload.progress ?? 0)
      setDetail((prev) => (prev ? { ...prev, progress } : prev))
      return
    }
    if (['run.finished', 'run.failed', 'run.cancelled'].includes(lastEvent.type)) {
      void backtestApi.get(selectedId).then(setDetail).catch(() => undefined)
      void refreshRuns()
    }
  }, [lastEvent, selectedId, refreshRuns])

  const changeStrategy = (id: string) => {
    setStrategyId(id)
    const next = strategies.find((item) => item.id === id)
    setParams(defaultsFor(next))
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setFormError('')
    const symbols = parseSymbols(symbolsText)
    if (!strategyId) {
      setFormError('请选择策略')
      return
    }
    if (isCustomRule && !selectedCustom) {
      setFormError('请选择要运行的拼装策略；若还没有，去策略库用条件拼装器创建')
      return
    }
    if (symbols.length === 0) {
      setFormError('请至少填写一个标的代码')
      return
    }
    if (symbols.length > 30) {
      setFormError('单次回测最多 30 个标的')
      return
    }
    if (start > end) {
      setFormError('开始日期不能晚于结束日期')
      return
    }

    const cleanParams: Record<string, unknown> = {}
    for (const spec of strategy?.params ?? []) {
      if (spec.type === 'json') continue // 拼装策略的 spec 由 custom_strategy_id 携带
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

    const body: BacktestRequest = {
      strategy_id: strategyId,
      symbols,
      start,
      end,
      initial_cash: initialCash,
      params: cleanParams,
      cost,
      risk: riskRules,
      risk_free_rate: 0.02,
    }

    setSubmitting(true)
    try {
      const accepted = await backtestApi.submit(body)
      notify('success', '回测已提交，正在排队执行')
      selectRun(accepted.run_id)
      await refreshRuns()
    } catch (err) {
      const message = err instanceof ApiError ? err.message : '提交失败'
      setFormError(message)
      notify('error', message)
    } finally {
      setSubmitting(false)
    }
  }

  const removeRun = async (runId: string) => {
    if (!window.confirm('确定删除这条回测记录？该操作不可恢复。')) return
    try {
      await backtestApi.remove(runId)
      notify('success', '记录已删除')
      if (selectedId === runId) {
        setSelectedId(null)
        setDetail(null)
        setSearchParams({}, { replace: true })
      }
      await refreshRuns()
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '删除失败')
    }
  }

  const cancelRun = async (runId: string) => {
    try {
      await backtestApi.cancel(runId)
      notify('info', '已发送取消请求')
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '取消失败')
    }
  }

  const metrics = detail?.metrics ?? {}
  const totalRealized = detail?.trades.reduce((sum, trade) => sum + trade.realized_pnl, 0) ?? 0

  return (
    <div className="col" style={{ gap: 18 }}>
      <div className="grid grid-sidebar">
        <Card
          title="新建回测"
          description="事件驱动 · 收盘价撮合在下一根 K 线 · 默认含 A 股涨跌停与整手约束"
          actions={
            <Badge tone="accent">
              默认风控：{riskPreset === 'balanced' ? '均衡' : riskPreset === 'conservative' ? '保守' : riskPreset === 'aggressive' ? '激进' : riskPreset}
            </Badge>
          }
        >
          <form className="col" style={{ gap: 14 }} onSubmit={submit}>
            <div className="form-grid">
              <Field label="策略">
                <select className="select" value={strategyId} onChange={(event) => changeStrategy(event.target.value)}>
                  {strategies.length === 0 && <option value="">加载中…</option>}
                  {strategies.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}（{item.id}）
                    </option>
                  ))}
                </select>
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

              <Field
                label="标的代码"
                hint="规范符号如 CN:600519 / US:AAPL / HK:00700，兼容旧 6 位代码；逗号或换行分隔。"
              >
                <input
                  className="input mono"
                  value={symbolsText}
                  onChange={(event) => setSymbolsText(event.target.value)}
                  placeholder="600519, 000001, 300059"
                />
              </Field>

              {isCustomRule && (
                <CustomStrategySelect value={selectedCustom} onChange={setSelectedCustom} />
              )}

            <div className="form-grid-3">
              <Field label="开始日期">
                <input className="input" type="date" value={start} onChange={(event) => setStart(event.target.value)} />
              </Field>
              <Field label="结束日期">
                <input className="input" type="date" value={end} onChange={(event) => setEnd(event.target.value)} />
              </Field>
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

            <button
              type="button"
              className="btn btn-ghost btn-sm"
              style={{ alignSelf: 'flex-start', paddingLeft: 0 }}
              onClick={() => setAdvanced((prev) => !prev)}
            >
              {advanced ? '收起成本参数' : '展开成本参数'}
            </button>

            {advanced && (
              <div className="form-grid-3">
                <Field label="佣金率" hint="0.0003 = 万三">
                  <input
                    className="input mono"
                    type="number"
                    step={0.0001}
                    min={0}
                    max={0.01}
                    value={cost.commission_rate}
                    onChange={(event) => setCost({ ...cost, commission_rate: Number(event.target.value) })}
                  />
                </Field>
                <Field label="最低佣金（元）">
                  <input
                    className="input mono"
                    type="number"
                    step={1}
                    min={0}
                    value={cost.min_commission}
                    onChange={(event) => setCost({ ...cost, min_commission: Number(event.target.value) })}
                  />
                </Field>
                <Field label="印花税率" hint="卖出单边收取">
                  <input
                    className="input mono"
                    type="number"
                    step={0.0001}
                    min={0}
                    max={0.01}
                    value={cost.stamp_tax_rate}
                    onChange={(event) => setCost({ ...cost, stamp_tax_rate: Number(event.target.value) })}
                  />
                </Field>
                <Field label="过户费率">
                  <input
                    className="input mono"
                    type="number"
                    step={0.00001}
                    min={0}
                    value={cost.transfer_fee_rate}
                    onChange={(event) => setCost({ ...cost, transfer_fee_rate: Number(event.target.value) })}
                  />
                </Field>
                <Field label="滑点（bp）" hint="1 bp = 0.01%">
                  <input
                    className="input mono"
                    type="number"
                    step={0.5}
                    min={0}
                    max={200}
                    value={cost.slippage_bps}
                    onChange={(event) => setCost({ ...cost, slippage_bps: Number(event.target.value) })}
                  />
                </Field>
              </div>
            )}

            {(strategy && formParams.length > 0) && (
              <>
                <div className="divider" />
                <div className="label">策略参数</div>
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
                              onChange={(event) =>
                                setParams({ ...params, [param.name]: event.target.checked })
                              }
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
                        <span className="hint">{param.description || paramBounds(param)}</span>
                      </div>
                    )
                  })}
                </div>
              </>
            )}

            {formError && <Banner kind="danger">{formError}</Banner>}

            <div className="row between">
              <span className="tiny faint">
                多标的策略请使用 <span className="mono">on_bars</span>；回测结果可复现。
              </span>
              <Button type="submit" variant="primary" loading={submitting}>
                <IconFlask /> 运行回测
              </Button>
            </div>
          </form>
        </Card>

        <Card
          title="历史回测"
          description={`${runs.length} 条记录`}
          actions={
            <Button size="sm" variant="ghost" onClick={() => void refreshRuns()}>
              <IconRefresh />
            </Button>
          }
          flush
        >
          {runs.length === 0 ? (
            <Empty title="暂无记录" hint="提交后这里会出现运行状态与收益。" />
          ) : (
            <div className="table-wrap" style={{ maxHeight: 560 }}>
              <table>
                <thead>
                  <tr>
                    <th>策略 / 标的</th>
                    <th className="right">收益</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr
                      key={run.run_id}
                      onClick={() => selectRun(run.run_id)}
                      style={{
                        cursor: 'pointer',
                        background: run.run_id === selectedId ? 'rgba(91, 140, 255, 0.08)' : undefined,
                      }}
                    >
                      <td>
                        <div className="strong truncate" style={{ maxWidth: 180 }}>{run.strategy_id}</div>
                        <div className="tiny faint mono">{run.symbols.join(' · ')}</div>
                        <div className="tiny faint">{fmtDateTime(run.created_at)}</div>
                      </td>
                      <td className={`right num ${Number(run.metrics?.total_return ?? 0) >= 0 ? 'up' : 'down'}`}>
                        {run.status === 'finished' ? fmtSignedPct(run.metrics?.total_return) : '—'}
                        {isActive(run.status) && (
                          <div style={{ marginTop: 4 }}>
                            <Progress value={run.progress} />
                          </div>
                        )}
                      </td>
                      <td><StatusBadge status={run.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {detailError && (
        <Banner kind="danger">
          结果加载失败：{detailError}
        </Banner>
      )}

      {detailLoading && !detail ? (
        <Card><Loading text="正在读取回测结果…" /></Card>
      ) : detail ? (
        <>
          <Card
            title={
              <span className="row" style={{ gap: 8 }}>
                运行详情
                <Badge>{detail.run_id}</Badge>
              </span>
            }
            description={`${detail.strategy_id} · ${detail.symbols.join(', ')} · ${durationText(detail.elapsed_seconds)}`}
            actions={
              <>
                <StatusBadge status={detail.status} />
                {isActive(detail.status) && (
                  <Button size="sm" onClick={() => void cancelRun(detail.run_id)}>
                    <IconStop /> 取消
                  </Button>
                )}
                <Button size="sm" variant="danger" onClick={() => void removeRun(detail.run_id)}>
                  <IconTrash />
                </Button>
              </>
            }
          >
            {isActive(detail.status) && (
              <div className="col" style={{ gap: 8 }}>
                <div className="row between">
                  <span className="small muted">正在回放行情并撮合订单…</span>
                  <span className="mono small">{(detail.progress * 100).toFixed(0)}%</span>
                </div>
                <Progress value={detail.progress} />
              </div>
            )}
            {detail.error && <Banner kind="danger">运行失败：{detail.error}</Banner>}
            {!isActive(detail.status) && (
              <div className="grid grid-4">
                <Stat
                  label="总收益率"
                  value={fmtSignedPct(metrics.total_return)}
                  sub={`年化 ${fmtSignedPct(metrics.annual_return)}`}
                  tone={Number(metrics.total_return ?? 0) >= 0 ? 'up' : 'down'}
                />
                <Stat
                  label="夏普 / 索提诺"
                  value={`${fmtNumber(metrics.sharpe, 2)} / ${fmtNumber(metrics.sortino, 2)}`}
                  sub={`波动率 ${fmtPct(metrics.volatility)}`}
                />
                <Stat
                  label="最大回撤"
                  value={fmtPct(metrics.max_drawdown)}
                  sub={`${metrics.max_drawdown_days ?? 0} 个交易日`}
                  tone="down"
                />
                <Stat
                  label="最终权益"
                  value={fmtMoney(metrics.final_equity)}
                  sub={`初始 ${fmtMoney(metrics.initial_equity)}`}
                />
                <Stat
                  label="胜率"
                  value={fmtPct(metrics.win_rate)}
                  sub={`${metrics.win_count ?? 0} 胜 / ${metrics.loss_count ?? 0} 负`}
                />
                <Stat
                  label="盈亏比"
                  value={fmtNumber(metrics.profit_factor, 2)}
                  sub={`期望 ${fmtMoney(metrics.expectancy)} / 笔`}
                />
                <Stat
                  label="成交笔数"
                  value={fmtNumber(metrics.trade_count, 0)}
                  sub={`换手 ${fmtNumber(metrics.turnover)} · 暴露 ${fmtPct(metrics.exposure)}`}
                />
                <Stat
                  label="总费用"
                  value={fmtMoney(metrics.total_fees)}
                  sub={`已实现盈亏 ${fmtMoney(totalRealized)}`}
                />
              </div>
            )}
          </Card>

          <Card title="权益曲线" description="上方总权益，下方回撤；滚轮或拖拽缩放">
            <EquityChart points={detail.equity_curve} height={360} />
          </Card>

          <div className="grid grid-2">
            <Card title="月度收益">
              <MonthlyReturnChart returns={metrics.monthly_returns ?? {}} height={200} />
            </Card>
            <Card title="交易统计" flush>
              <div className="table-wrap">
                <table>
                  <tbody>
                    <tr><td>总收益率</td><td className={cx('right num', Number(metrics.total_return ?? 0) >= 0 ? 'up' : 'down')}>{fmtSignedPct(metrics.total_return)}</td></tr>
                    <tr><td>年化收益</td><td className="right num">{fmtSignedPct(metrics.annual_return)}</td></tr>
                    <tr><td>卡玛比率</td><td className="right num">{fmtNumber(metrics.calmar, 2)}</td></tr>
                    <tr><td>平均盈利 / 平均亏损</td><td className="right num">{fmtMoney(metrics.avg_win)} / {fmtMoney(metrics.avg_loss)}</td></tr>
                    <tr><td>基准收益 / 超额</td><td className="right num">{fmtSignedPct(metrics.benchmark_return)} / {fmtSignedPct(metrics.excess_return)}</td></tr>
                    <tr><td>回测区间</td><td className="right num tiny">{metrics.start ?? '—'} → {metrics.end ?? '—'}</td></tr>
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          <Card
            title="明细"
            actions={
              <Tabs<ResultTab>
                value={tab}
                onChange={setTab}
                options={[
                  { value: 'trades', label: `成交 ${detail.trades.length}` },
                  { value: 'orders', label: `委托 ${detail.orders.length}` },
                  { value: 'positions', label: `持仓 ${detail.positions.length}` },
                  { value: 'logs', label: `日志 ${detail.logs.length}` },
                  { value: 'config', label: '配置' },
                ]}
              />
            }
            flush
          >
            {tab === 'trades' && (
              detail.trades.length === 0 ? (
                <Empty title="没有成交" hint="策略可能未触发信号，或订单被风控拒绝（见委托页）。" />
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>成交时间</th>
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
                      {detail.trades.map((trade) => (
                        <tr key={trade.id}>
                          <td className="tiny faint nowrap">{fmtDateTime(trade.dt)}</td>
                          <td className="strong mono">{trade.symbol}</td>
                          <td>
                            <span className={trade.side === 'buy' ? 'up' : 'down'}>
                              {trade.side === 'buy' ? '买入' : '卖出'}
                            </span>
                          </td>
                          <td className="right num">{trade.quantity.toLocaleString('zh-CN')}</td>
                          <td className="right num">{fmtMoney(trade.price)}</td>
                          <td className="right num">{fmtMoney(trade.gross)}</td>
                          <td className="right num tiny">{fmtMoney(trade.fees.total)}</td>
                          <td className={cx('right num', trade.realized_pnl > 0 ? 'up' : trade.realized_pnl < 0 ? 'down' : '')}>
                            {trade.realized_pnl ? fmtMoney(trade.realized_pnl) : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            )}

            {tab === 'orders' && (
              detail.orders.length === 0 ? (
                <Empty title="没有委托" />
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>创建时间</th>
                        <th>代码</th>
                        <th>方向</th>
                        <th>类型</th>
                        <th className="right">数量</th>
                        <th className="right">成交价</th>
                        <th>状态</th>
                        <th>拒绝原因</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.orders.map((order) => (
                        <tr key={order.id}>
                          <td className="tiny faint nowrap">{fmtDateTime(order.created_at)}</td>
                          <td className="strong mono">{order.symbol}</td>
                          <td className={order.side === 'buy' ? 'up' : 'down'}>
                            {order.side === 'buy' ? '买入' : '卖出'}
                          </td>
                          <td className="tiny">{order.order_type}</td>
                          <td className="right num">{order.quantity.toLocaleString('zh-CN')}</td>
                          <td className="right num">
                            {order.avg_fill_price ? fmtMoney(order.avg_fill_price) : order.limit_price ? fmtMoney(order.limit_price) : '—'}
                          </td>
                          <td><StatusBadge status={order.status} /></td>
                          <td className="tiny faint truncate" style={{ maxWidth: 220 }} title={order.reason}>
                            {order.reason || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            )}

            {tab === 'positions' && (
              detail.positions.length === 0 ? (
                <Empty title="期末无持仓" hint="回测结束时所有仓位已平掉。" />
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>代码</th>
                        <th className="right">数量</th>
                        <th className="right">可用</th>
                        <th className="right">成本价</th>
                        <th className="right">最新价</th>
                        <th className="right">市值</th>
                        <th className="right">浮动盈亏</th>
                        <th className="right">已实现</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.positions.map((position) => (
                        <tr key={position.symbol}>
                          <td className="strong mono">{position.symbol}</td>
                          <td className="right num">{position.quantity.toLocaleString('zh-CN')}</td>
                          <td className="right num">{position.available.toLocaleString('zh-CN')}</td>
                          <td className="right num">{fmtMoney(position.avg_cost)}</td>
                          <td className="right num">{fmtMoney(position.last_price)}</td>
                          <td className="right num">{fmtMoney(position.market_value)}</td>
                          <td className={cx('right num', position.unrealized_pnl >= 0 ? 'up' : 'down')}>
                            {fmtMoney(position.unrealized_pnl)}（{fmtSignedPct(position.unrealized_pct)}）
                          </td>
                          <td className={cx('right num', position.realized_pnl >= 0 ? 'up' : 'down')}>
                            {fmtMoney(position.realized_pnl)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            )}

            {tab === 'logs' && (
              detail.logs.length === 0 ? (
                <Empty title="本次运行没有日志" hint="引擎只在异常或重要节点记录日志。" />
              ) : (
                <div className="table-wrap" style={{ maxHeight: 420 }}>
                  <table>
                    <thead>
                      <tr><th>时间</th><th>级别</th><th>内容</th></tr>
                    </thead>
                    <tbody>
                      {detail.logs.map((log, index) => (
                        <tr key={`${log.dt ?? log.time ?? index}-${index}`}>
                          <td className="tiny faint nowrap">{fmtDateTime(String(log.dt ?? log.time ?? ''))}</td>
                          <td className="tiny">{String(log.level ?? 'info')}</td>
                          <td className="tiny">{String(log.message ?? JSON.stringify(log))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            )}

            {tab === 'config' && (
              <div className="card-body">
                <pre className="code-block">{JSON.stringify(detail.config, null, 2)}</pre>
              </div>
            )}
          </Card>
        </>
      ) : (
        <Card>
          <Empty
            title="选择一条记录查看结果"
            hint="或在上方配置策略并提交新的回测。"
          />
        </Card>
      )}
    </div>
  )
}

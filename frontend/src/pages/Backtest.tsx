/** 回测页：参数表单 + 任务列表 + 结果分析。 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ApiError, backtestApi, riskApi, strategyApi } from '../api/client'
import type {
  BacktestRequest,
  BarInterval,
  CostConfig,
  RiskPresets,
  RiskRuleInput,
  RunDetail,
  RunSummary,
  Strategy,
  StrategyParam,
} from '../api/types'
import { CustomStrategySelect } from '../components/CustomStrategySelect'
import { SymbolPicker } from '../components/SymbolPicker'
import { IconFlask, IconRefresh, IconStop, IconTrash } from '../components/icons'
import { RunDetailBody, RunMetrics } from '../components/RunDetail'
import { Badge, Banner, Button, Card, Empty, Field, Loading, Progress, StatusBadge } from '../components/ui'
import { useEvents } from '../store/events'
import { useWatchlist } from '../store/watchlist'
import { notify } from '../store/toast'
import { durationText, fmtDateTime, fmtSignedPct, todayISO, yearsAgoISO } from '../utils/format'

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
  const [advanced, setAdvanced] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')

  const [strategyId, setStrategyId] = useState('')
  const [symbols, setSymbols] = useState<string[]>([])
  const [barInterval, setBarInterval] = useState<BarInterval>('1d')
  const [start, setStart] = useState(yearsAgoISO(3))
  const [end, setEnd] = useState(todayISO())
  const [initialCash, setInitialCash] = useState(1_000_000)
  const [riskPreset, setRiskPreset] = useState('balanced')
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [cost, setCost] = useState<CostConfig>(DEFAULT_COST)
  // 条件拼装策略：选中的「我的拼装策略」id
  const [selectedCustom, setSelectedCustom] = useState<number | null>(null)

  const isCustomRule = strategyId === 'custom_rule'

  const strategy = useMemo(() => strategies.find((item) => item.id === strategyId), [strategies, strategyId])

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
      void backtestApi
        .get(selectedId)
        .then(setDetail)
        .catch(() => undefined)
      void refreshRuns()
    }
  }, [lastEvent, selectedId, refreshRuns])

  const watchlist = useWatchlist((state) => state.items)
  const loadWatchlist = useWatchlist((state) => state.load)

  useEffect(() => {
    void loadWatchlist()
  }, [loadWatchlist])

  const changeStrategy = (id: string) => {
    setStrategyId(id)
    const next = strategies.find((item) => item.id === id)
    setParams(defaultsFor(next))
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setFormError('')
    if (!strategyId) {
      setFormError('请选择策略')
      return
    }
    if (isCustomRule && !selectedCustom) {
      setFormError('请选择要运行的拼装策略；若还没有，去策略库用条件拼装器创建')
      return
    }
    if (symbols.length === 0) {
      setFormError('请至少选择一个标的')
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
      bar_interval: barInterval,
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

  return (
    <div className="col" style={{ gap: 18 }}>
      <div className="grid grid-sidebar">
        <Card
          title="新建回测"
          description="事件驱动 · 收盘价撮合在下一根 K 线 · 默认含 A 股涨跌停与整手约束"
          actions={
            <Badge tone="accent">
              默认风控：
              {riskPreset === 'balanced'
                ? '均衡'
                : riskPreset === 'conservative'
                  ? '保守'
                  : riskPreset === 'aggressive'
                    ? '激进'
                    : riskPreset}
            </Badge>
          }
        >
          <form className="col" style={{ gap: 14 }} onSubmit={submit}>
            <div className="form-grid">
              <Field label="策略">
                <select
                  className="select"
                  value={strategyId}
                  onChange={(event) => changeStrategy(event.target.value)}
                >
                  {strategies.length === 0 && <option value="">加载中…</option>}
                  {strategies.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}（{item.id}）
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="风控预设">
                <select
                  className="select"
                  value={riskPreset}
                  onChange={(event) => setRiskPreset(event.target.value)}
                >
                  {(presets ? Object.keys(presets.presets) : ['balanced']).map((key) => (
                    <option key={key} value={key}>
                      {key === 'conservative'
                        ? '保守'
                        : key === 'balanced'
                          ? '均衡'
                          : key === 'aggressive'
                            ? '激进'
                            : key}
                    </option>
                  ))}
                </select>
              </Field>
            </div>

            <Field label="标的" hint="优先从账号自选勾选；也可以搜索或直接输入规范代码。">
              <SymbolPicker watchlist={watchlist} selected={symbols} onChange={setSymbols} />
            </Field>

            {isCustomRule && <CustomStrategySelect value={selectedCustom} onChange={setSelectedCustom} />}

            <Field label="K 线周期" hint="分钟级目前仅虚拟货币支持历史回测；日线策略不受影响">
              <select
                className="select"
                value={barInterval}
                onChange={(event) => setBarInterval(event.target.value as BarInterval)}
              >
                <option value="1d">日线（默认）</option>
                <option value="60m">60 分钟</option>
                <option value="30m">30 分钟</option>
                <option value="15m">15 分钟</option>
                <option value="5m">5 分钟</option>
                <option value="1m">1 分钟（高频）</option>
              </select>
            </Field>

            <div className="form-grid-3">
              <Field label="开始日期">
                <input
                  className="input"
                  type="date"
                  value={start}
                  onChange={(event) => setStart(event.target.value)}
                />
              </Field>
              <Field label="结束日期">
                <input
                  className="input"
                  type="date"
                  value={end}
                  onChange={(event) => setEnd(event.target.value)}
                />
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

            {strategy && formParams.length > 0 && (
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
                        <div className="strong truncate" style={{ maxWidth: 180 }}>
                          {run.strategy_id}
                        </div>
                        <div className="tiny faint mono">{run.symbols.join(' · ')}</div>
                        <div className="tiny faint">{fmtDateTime(run.created_at)}</div>
                      </td>
                      <td
                        className={`right num ${Number(run.metrics?.total_return ?? 0) >= 0 ? 'up' : 'down'}`}
                      >
                        {run.status === 'finished' ? fmtSignedPct(run.metrics?.total_return) : '—'}
                        {isActive(run.status) && (
                          <div style={{ marginTop: 4 }}>
                            <Progress value={run.progress} />
                          </div>
                        )}
                      </td>
                      <td>
                        <StatusBadge status={run.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {detailError && <Banner kind="danger">结果加载失败：{detailError}</Banner>}

      {detailLoading && !detail ? (
        <Card>
          <Loading text="正在读取回测结果…" />
        </Card>
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
            {!isActive(detail.status) && <RunMetrics detail={detail} />}
          </Card>

          <RunDetailBody detail={detail} />
        </>
      ) : (
        <Card>
          <Empty title="选择一条记录查看结果" hint="或在上方配置策略并提交新的回测。" />
        </Card>
      )}
    </div>
  )
}

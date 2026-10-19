/** 实盘页：真实券商/交易所下单。
 *
 * 三道闸门在页面里也要体现：服务端总开关（403 提示）、默认测试网、
 * 关闭测试网时必须勾选「我确认使用真实资金」。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, credentialApi, liveApi, strategyApi } from '../api/client'
import type { Credential, LiveAdapter, LiveState, LiveStartRequest, Strategy } from '../api/types'
import { EquityChart } from '../components/Charts'
import { SwitchStrategyModal } from '../components/SwitchStrategy'
import { Badge, Banner, Button, Card, Empty, Field, Loading, Stat } from '../components/ui'
import { IconPlay, IconRefresh, IconStop } from '../components/icons'
import {
  classForSign,
  fmtDateTime,
  fmtMoney,
  fmtNumber,
  fmtSignedPct,
  parseSymbols,
} from '../utils/format'
import { notify } from '../store/toast'

function defaultsFor(strategy?: Strategy): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const spec of strategy?.params ?? []) {
    if (spec.type === 'json') continue
    out[spec.name] = spec.default
  }
  return out
}

export function LivePage() {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [adapters, setAdapters] = useState<LiveAdapter[]>([])
  const [credentials, setCredentials] = useState<Credential[]>([])
  const [state, setState] = useState<LiveState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [switchOpen, setSwitchOpen] = useState(false)

  // 表单
  const [strategyId, setStrategyId] = useState('')
  const [adapter, setAdapter] = useState('ccxt')
  const [credentialId, setCredentialId] = useState<number | null>(null)
  const [symbolsText, setSymbolsText] = useState('CRYPTO:BTC/USDT')
  const [initialCash, setInitialCash] = useState(100000)
  const [sandbox, setSandbox] = useState(true)
  const [confirmLive, setConfirmLive] = useState(false)
  const [pollInterval, setPollInterval] = useState(5)
  const [params, setParams] = useState<Record<string, unknown>>({})

  const strategy = useMemo(
    () => strategies.find((item) => item.id === strategyId),
    [strategies, strategyId],
  )
  const brokerCredentials = useMemo(
    () => credentials.filter((item) => item.kind === 'broker'),
    [credentials],
  )
  const active = state?.status === 'running'
  const adapterMeta = adapters.find((item) => item.name === adapter)

  const loadAll = useCallback(async () => {
    const [strategyResult, adapterResult, credentialResult, stateResult] = await Promise.allSettled([
      strategyApi.list(),
      liveApi.adapters(),
      credentialApi.list(),
      liveApi.state(),
    ])
    if (strategyResult.status === 'fulfilled') {
      setStrategies(strategyResult.value)
      if (!strategyId && strategyResult.value.length > 0) {
        const first = strategyResult.value[0]
        setStrategyId(first.id)
        setParams(defaultsFor(first))
      }
    }
    if (adapterResult.status === 'fulfilled') setAdapters(adapterResult.value)
    if (credentialResult.status === 'fulfilled') {
      setCredentials(credentialResult.value)
      const firstBroker = credentialResult.value.find((item) => item.kind === 'broker')
      if (firstBroker) setCredentialId((current) => current ?? firstBroker.id)
    }
    if (stateResult.status === 'fulfilled') setState(stateResult.value)
    setLoading(false)
  }, [strategyId])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  // 运行中：2 秒轮询；空闲 15 秒
  useEffect(() => {
    const interval = active ? 2000 : 15000
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        liveApi
          .state()
          .then(setState)
          .catch(() => undefined)
      }
    }, interval)
    return () => window.clearInterval(timer)
  }, [active])

  const changeStrategy = (id: string) => {
    setStrategyId(id)
    setParams(defaultsFor(strategies.find((item) => item.id === id)))
  }

  const start = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    const symbols = parseSymbols(symbolsText)
    if (!strategyId) {
      setError('请选择策略')
      return
    }
    if (symbols.length === 0) {
      setError('请填写至少一个标的')
      return
    }
    if (credentialId === null) {
      setError('请先在「设置 → 凭据」创建券商凭据，并在此选择')
      return
    }
    if (!sandbox && !confirmLive) {
      setError('关闭测试网意味着真实资金下单，请勾选确认')
      return
    }
    const cleanParams: Record<string, unknown> = {}
    for (const spec of strategy?.params ?? []) {
      if (spec.type === 'json') continue
      const value = params[spec.name]
      if (value === '' || value === undefined || value === null) continue
      cleanParams[spec.name] = value
    }
    const body: LiveStartRequest = {
      strategy_id: strategyId,
      symbols,
      initial_cash: initialCash,
      params: cleanParams,
      adapter,
      credential_id: credentialId,
      sandbox,
      confirm_live: confirmLive,
      poll_interval: pollInterval,
    }
    setStarting(true)
    try {
      await liveApi.start(body)
      notify('success', `实盘已启动（${sandbox ? '测试网' : '真实资金'}）`)
      window.setTimeout(() => void loadAll(), 800)
    } catch (err) {
      const message = err instanceof ApiError ? err.message : '启动失败'
      setError(message)
      notify('error', message)
    } finally {
      setStarting(false)
    }
  }

  const stop = async () => {
    setStopping(true)
    try {
      const message = await liveApi.stop()
      notify('info', message.message)
      window.setTimeout(() => void loadAll(), 800)
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '停止失败')
    } finally {
      setStopping(false)
    }
  }

  if (loading) return <Loading text="正在加载实盘配置…" />

  return (
    <div className="col" style={{ gap: 18 }}>
      {error && <Banner kind="danger">{error}</Banner>}
      {!sandbox && (
        <Banner kind="warn">
          当前选择<strong>真实资金</strong>：委托会直接发往交易所/券商。请确认额度、标的与策略逻辑，
          并自行承担交易结果。
        </Banner>
      )}

      <div className="grid grid-sidebar">
        <Card
          title="启动实盘"
          description="三重闸门：服务端总开关 · 默认测试网 · 真实资金二次确认"
          actions={<Badge tone={active ? 'success' : 'neutral'}>{active ? '运行中' : '空闲'}</Badge>}
        >
          <form className="col" style={{ gap: 14 }} onSubmit={start}>
            <div className="form-grid">
              <Field label="策略">
                <select
                  className="select"
                  value={strategyId}
                  onChange={(event) => changeStrategy(event.target.value)}
                >
                  {strategies.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}（{item.id}）
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="通道" hint={adapterMeta ? `支持市场：${adapterMeta.markets.join(' / ') || '—'}` : ''}>
                <select className="select" value={adapter} onChange={(event) => setAdapter(event.target.value)}>
                  {adapters.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </Field>
            </div>

            <Field label="标的代码" hint="单一市场；如 CRYPTO:BTC/USDT（ccxt）或 CN:600519（miniQMT）">
              <input
                className="input mono"
                value={symbolsText}
                onChange={(event) => setSymbolsText(event.target.value)}
                placeholder="CRYPTO:BTC/USDT"
              />
            </Field>

            <div className="form-grid">
              <Field label="券商凭据" hint="在「设置 → 凭据」创建，kind 选 broker">
                <select
                  className="select"
                  value={credentialId ?? ''}
                  onChange={(event) => setCredentialId(event.target.value ? Number(event.target.value) : null)}
                >
                  <option value="">— 请选择 —</option>
                  {brokerCredentials.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="初始资金（仅展示口径）" hint="实盘以交易所资金为准，启动时会自动绑定">
                <input
                  className="input mono"
                  type="number"
                  min={1000}
                  step={1000}
                  value={initialCash}
                  onChange={(event) => setInitialCash(Number(event.target.value))}
                />
              </Field>
            </div>

            <div className="form-grid">
              <Field label="轮询间隔（秒）" hint="每轮：同步回报 → 取行情 → 策略决策">
                <input
                  className="input mono"
                  type="number"
                  min={1}
                  max={600}
                  value={pollInterval}
                  onChange={(event) => setPollInterval(Number(event.target.value))}
                />
              </Field>
              <Field label="环境">
                <label className="row" style={{ gap: 8, alignItems: 'center', paddingTop: 6 }}>
                  <input
                    type="checkbox"
                    checked={sandbox}
                    onChange={(event) => {
                      setSandbox(event.target.checked)
                      if (event.target.checked) setConfirmLive(false)
                    }}
                  />
                  <span>{sandbox ? '测试网 / 模拟账户' : '真实资金'}</span>
                </label>
              </Field>
            </div>

            {!sandbox && (
              <label className="row" style={{ gap: 8, alignItems: 'center' }}>
                <input
                  type="checkbox"
                  checked={confirmLive}
                  onChange={(event) => setConfirmLive(event.target.checked)}
                />
                <span className="small">我确认使用真实资金，并已核对策略、标的与额度</span>
              </label>
            )}

            {(strategy?.params ?? [])
              .filter((spec) => spec.type !== 'json')
              .map((spec) => (
                <Field key={spec.name} label={spec.description || spec.name}>
                  {spec.type === 'bool' ? (
                    <input
                      type="checkbox"
                      checked={Boolean(params[spec.name])}
                      onChange={(event) =>
                        setParams((prev) => ({ ...prev, [spec.name]: event.target.checked }))
                      }
                    />
                  ) : (
                    <input
                      className="input mono"
                      type={spec.type === 'int' || spec.type === 'float' ? 'number' : 'text'}
                      step={spec.step ?? (spec.type === 'int' ? 1 : 'any')}
                      value={String(params[spec.name] ?? '')}
                      onChange={(event) =>
                        setParams((prev) => ({ ...prev, [spec.name]: event.target.value }))
                      }
                    />
                  )}
                </Field>
              ))}

            <Button type="submit" block loading={starting} disabled={active}>
              <IconPlay /> 启动实盘
            </Button>
            {active && (
              <>
                <Button block onClick={() => setSwitchOpen(true)}>
                  切换策略（下一轮生效）
                </Button>
                <Button variant="danger" block loading={stopping} onClick={() => void stop()}>
                  <IconStop /> 停止（逐单撤单）
                </Button>
              </>
            )}
          </form>
        </Card>

        <Card
          title="会话状态"
          actions={
            <Button size="sm" variant="ghost" onClick={() => void loadAll()}>
              <IconRefresh />
            </Button>
          }
        >
          {state ? (
            <div className="col" style={{ gap: 12 }}>
              <div className="row between">
                <Badge tone={active ? 'success' : 'neutral'}>{state.status}</Badge>
                <Badge tone={state.sandbox ? 'accent' : 'danger'}>
                  {state.sandbox ? '测试网' : '真实资金'}
                </Badge>
              </div>
              <dl className="kv">
                <dt>通道</dt>
                <dd className="mono">{state.adapter}</dd>
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
                <dt>最近对账</dt>
                <dd>{state.reconcile_at ? fmtDateTime(state.reconcile_at) : '—'}</dd>
              </dl>
              {state.error && <Banner kind="danger">{state.error}</Banner>}
            </div>
          ) : (
            <Empty title="尚未启动" hint="左侧配置通道、凭据与策略，启动后这里显示实时状态。" />
          )}
        </Card>
      </div>

      {state && (
        <>
          <div className="grid grid-4">
            <Stat
              label="总权益"
              value={fmtMoney(state.equity)}
              sub={`现金 ${fmtMoney(state.cash)} · 冻结 ${fmtMoney(state.frozen_cash)}`}
              tone={state.total_return >= 0 ? 'up' : 'down'}
            />
            <Stat
              label="总收益率"
              value={fmtSignedPct(state.total_return)}
              sub={`${state.positions.length} 个持仓`}
              tone={state.total_return >= 0 ? 'up' : 'down'}
            />
            <Stat label="挂单" value={state.open_orders.length} sub="交易所未终态委托" />
            <Stat label="成交" value={state.recent_fills.length} sub="最近 50 笔" />
          </div>

          <Card title="权益曲线" flush>
            <EquityChart points={state.equity_curve} height={280} />
          </Card>

          <Card title="持仓" flush>
            {state.positions.length === 0 ? (
              <div style={{ padding: 16 }}>
                <Empty title="暂无持仓" />
              </div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>标的</th>
                      <th className="right">数量</th>
                      <th className="right">可卖</th>
                      <th className="right">成本价</th>
                      <th className="right">现价</th>
                      <th className="right">市值</th>
                      <th className="right">浮动盈亏</th>
                    </tr>
                  </thead>
                  <tbody>
                    {state.positions.map((item) => (
                      <tr key={item.symbol}>
                        <td className="mono strong">{item.symbol}</td>
                        <td className="right num">{fmtNumber(item.quantity, 4)}</td>
                        <td className="right num">{fmtNumber(item.available, 4)}</td>
                        <td className="right num">{fmtNumber(item.avg_cost, 4)}</td>
                        <td className="right num">{fmtNumber(item.last_price, 4)}</td>
                        <td className="right num">{fmtMoney(item.market_value)}</td>
                        <td className={`right num ${classForSign(item.unrealized_pnl)}`}>
                          {fmtMoney(item.unrealized_pnl)}
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
              <div style={{ padding: 16 }}>
                <Empty title="无挂单" />
              </div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>标的</th>
                      <th>方向</th>
                      <th className="right">数量</th>
                      <th className="right">已成交</th>
                      <th className="right">限价</th>
                      <th>状态</th>
                      <th>交易所委托号</th>
                    </tr>
                  </thead>
                  <tbody>
                    {state.open_orders.map((item) => (
                      <tr key={item.id}>
                        <td className="tiny">{fmtDateTime(item.created_at)}</td>
                        <td className="mono">{item.symbol}</td>
                        <td>
                          <Badge tone={item.side === 'buy' ? 'accent' : 'warn'}>
                            {item.side === 'buy' ? '买入' : '卖出'}
                          </Badge>
                        </td>
                        <td className="right num">{fmtNumber(item.quantity, 4)}</td>
                        <td className="right num">{fmtNumber(item.filled_quantity, 4)}</td>
                        <td className="right num">{item.limit_price ? fmtNumber(item.limit_price, 4) : '市价'}</td>
                        <td>{item.status}</td>
                        <td className="mono tiny">{String(item.meta?.external_id ?? '—')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card title="成交" flush>
            {state.recent_fills.length === 0 ? (
              <div style={{ padding: 16 }}>
                <Empty title="暂无成交" />
              </div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>标的</th>
                      <th>方向</th>
                      <th className="right">数量</th>
                      <th className="right">价格</th>
                      <th className="right">金额</th>
                      <th className="right">费用</th>
                    </tr>
                  </thead>
                  <tbody>
                    {state.recent_fills.map((item) => (
                      <tr key={item.id}>
                        <td className="tiny">{fmtDateTime(item.dt)}</td>
                        <td className="mono">{item.symbol}</td>
                        <td>
                          <Badge tone={item.side === 'buy' ? 'accent' : 'warn'}>
                            {item.side === 'buy' ? '买入' : '卖出'}
                          </Badge>
                        </td>
                        <td className="right num">{fmtNumber(item.quantity, 4)}</td>
                        <td className="right num">{fmtNumber(item.price, 4)}</td>
                        <td className="right num">{fmtMoney(item.gross)}</td>
                        <td className="right num">{fmtMoney(item.fees?.total ?? 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      <SwitchStrategyModal
        open={switchOpen}
        onClose={() => setSwitchOpen(false)}
        kind="live"
        current={state?.strategy_id}
        onDone={() => void loadAll()}
      />
    </div>
  )
}

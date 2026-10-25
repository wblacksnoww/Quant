/** C++ 交易网关：tick 级策略（网格/突破/动量），paper 模式默认。 */

import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { credentialApi, gatewayApi, strategyApi } from '../api/client'
import type { Credential, CustomStrategy, GatewayStatus } from '../api/types'
import { notify } from '../store/toast'
import { Badge, Banner, Button, Card, Empty, Field, Loading, Stat } from '../components/ui'
import { fmtDateTime } from '../utils/format'

const STRATEGY_TYPES = [
  { value: 'grid', label: '网格交易', hint: '价格每跌一格买入、涨一格卖出，震荡市持续吃波动' },
  { value: 'breakout', label: '突破触发器', hint: '上破买入 / 下破清仓，冷却期防反复' },
  { value: 'tick_momentum', label: 'tick 动量', hint: '窗口动量顺势开仓，反向/回撤平仓' },
  {
    value: 'bar_spec',
    label: '拼装策略（蓝图）',
    hint: '策略库里拼好的条件/蓝图：bar 收盘求 entry/exit，全程 C++',
  },
] as const

const DEFAULT_PARAMS: Record<string, Record<string, unknown>> = {
  grid: { step_pct: 0.01, order_qty: 0.001, max_inventory: 0.01 },
  breakout: { upper: 0, lower: 0, order_qty: 0.001, cooldown_s: 60 },
  tick_momentum: { window_s: 30, entry_threshold: 0.003, order_qty: 0.001, cooldown_s: 30 },
  bar_spec: { order_qty: 0.001 },
}

export function GatewayPage() {
  const [status, setStatus] = useState<GatewayStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')

  const [strategyType, setStrategyType] = useState<string>('grid')
  const [paramsText, setParamsText] = useState(JSON.stringify(DEFAULT_PARAMS.grid, null, 2))
  const [symbol, setSymbol] = useState('CRYPTO:BTC/USDT')
  const [barSec, setBarSec] = useState(60)
  const [sandbox, setSandbox] = useState(true)
  const [confirmLive, setConfirmLive] = useState(false)
  const [credentials, setCredentials] = useState<Credential[]>([])
  const [credentialId, setCredentialId] = useState<number | null>(null)
  const [customStrategies, setCustomStrategies] = useState<CustomStrategy[]>([])
  const [customId, setCustomId] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      setStatus(await gatewayApi.status())
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取网关状态失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    void credentialApi
      .list()
      .then(setCredentials)
      .catch(() => undefined)
    void strategyApi
      .custom()
      .then(setCustomStrategies)
      .catch(() => undefined)
  }, [load])

  useEffect(() => {
    if (status?.running) {
      const timer = window.setInterval(() => void load(), 2000)
      return () => window.clearInterval(timer)
    }
  }, [status?.running, load])

  const pickCustom = (id: number) => {
    setCustomId(id)
    const item = customStrategies.find((s) => s.id === id)
    if (!item) return
    let orderQty = 0.001
    try {
      orderQty = (JSON.parse(paramsText) as { order_qty?: number }).order_qty ?? 0.001
    } catch {
      // 参数框里是非法 JSON：保留默认
    }
    setParamsText(JSON.stringify({ order_qty: orderQty, spec: item.spec }, null, 2))
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    let params: Record<string, unknown>
    try {
      params = JSON.parse(paramsText)
    } catch {
      setError('策略参数不是合法 JSON')
      return
    }
    const symbols = symbol
      .split(',')
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean)
    if (symbols.length === 0) {
      setError('请至少填写一个标的（CRYPTO:BTC/USDT）')
      return
    }
    setStarting(true)
    try {
      await gatewayApi.start({
        strategy_type: strategyType as 'grid' | 'breakout' | 'tick_momentum',
        params,
        symbols,
        bar_interval_sec: barSec,
        sandbox,
        confirm_live: confirmLive,
        credential_id: sandbox ? null : credentialId,
      })
      notify('success', '网关已启动（C++ tick 级策略）')
      await load()
    } catch (err) {
      const message = err instanceof Error ? err.message : '启动失败'
      setError(message)
      notify('error', message)
    } finally {
      setStarting(false)
    }
  }

  const stop = async () => {
    try {
      await gatewayApi.stop()
      notify('info', '网关已停止')
      await load()
    } catch (err) {
      notify('error', err instanceof Error ? err.message : '停止失败')
    }
  }

  if (loading) return <Loading text="正在读取网关状态…" />

  const running = status?.running ?? false
  const meta = STRATEGY_TYPES.find((s) => s.value === strategyType)

  return (
    <div className="col" style={{ gap: 18 }}>
      <Card
        title="C++ 交易网关"
        description="独立 quant-gateway 进程：OKX WebSocket tick 行情 → C++ 策略/风控 → 下单。paper 模式订单只写审计日志"
        actions={
          running ? (
            <Button size="sm" variant="danger" onClick={() => void stop()}>
              停止网关
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => void load()}>
              刷新
            </Button>
          )
        }
      >
        {error && <Banner kind="danger">{error}</Banner>}
        {running ? (
          <div className="grid grid-4">
            <Stat
              label="模式"
              value={status?.mode === 'live' ? '真实资金' : 'Paper'}
              sub={status?.strategy ?? ''}
              tone={status?.mode === 'live' ? 'down' : undefined}
            />
            <Stat label="已收 ticks" value={Number(status?.stats?.ticks ?? 0)} sub="累计" />
            <Stat
              label="订单 / 成交"
              value={`${Number(status?.stats?.orders ?? 0)} / ${Number(status?.stats?.fills ?? 0)}`}
              sub="本会话"
            />
            <Stat
              label="策略状态"
              value={status?.strategy_running ? '运行中' : '已暂停'}
              sub={(status?.symbols ?? []).join(', ')}
            />
          </div>
        ) : (
          <Empty title="网关未运行" hint="在下方选择策略与参数启动。默认 paper 模式，不碰真实资金。" />
        )}
      </Card>

      {!running && (
        <Card title="启动网关">
          <form className="col" style={{ gap: 14 }} onSubmit={submit}>
            <div className="form-grid">
              <Field label="策略" hint={meta?.hint}>
                <select
                  className="select"
                  value={strategyType}
                  onChange={(event) => {
                    setStrategyType(event.target.value)
                    setParamsText(JSON.stringify(DEFAULT_PARAMS[event.target.value], null, 2))
                  }}
                >
                  {STRATEGY_TYPES.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="标的（逗号分隔）" hint="目前仅虚拟货币：CRYPTO:BTC/USDT">
                <input
                  className="input mono"
                  value={symbol}
                  onChange={(event) => setSymbol(event.target.value)}
                  placeholder="CRYPTO:BTC/USDT, CRYPTO:ETH/USDT"
                />
              </Field>
            </div>

            {strategyType === 'bar_spec' && (
              <Field label="从策略库选择" hint="选中后把该策略的 spec 填入下方参数">
                <select
                  className="select"
                  value={customId ?? ''}
                  onChange={(event) => pickCustom(Number(event.target.value))}
                >
                  <option value="">— 请选择拼装策略 —</option>
                  {customStrategies.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                      {item.version > 1 ? ` (v${item.version})` : ''}
                    </option>
                  ))}
                </select>
              </Field>
            )}

            <Field label="策略参数（JSON）">
              <textarea
                className="input mono"
                rows={7}
                value={paramsText}
                onChange={(event) => setParamsText(event.target.value)}
                spellCheck={false}
              />
            </Field>

            <div className="form-grid">
              <Field label="bar 聚合周期（秒）" hint="仅用于指标统计；策略本身每个 tick 都触发">
                <input
                  className="input mono"
                  type="number"
                  min={1}
                  max={86400}
                  value={barSec}
                  onChange={(event) => setBarSec(Number(event.target.value))}
                />
              </Field>
              <Field label="环境">
                <label className="row" style={{ gap: 8, paddingTop: 6 }}>
                  <input
                    type="checkbox"
                    checked={sandbox}
                    onChange={(event) => {
                      setSandbox(event.target.checked)
                      if (event.target.checked) setConfirmLive(false)
                    }}
                  />
                  <span>{sandbox ? 'Paper（订单只写审计）' : '真实资金'}</span>
                </label>
              </Field>
            </div>

            {!sandbox && (
              <>
                <Banner kind="warn">
                  真实资金模式：订单将直接发往 OKX。需要服务端开启 QUANT_ENABLE_LIVE_TRADING、选择 broker
                  凭据并勾选确认。
                </Banner>
                <div className="form-grid">
                  <Field label="Broker 凭据">
                    <select
                      className="select"
                      value={credentialId ?? ''}
                      onChange={(event) =>
                        setCredentialId(event.target.value ? Number(event.target.value) : null)
                      }
                    >
                      <option value="">— 请选择 —</option>
                      {credentials
                        .filter((item) => item.kind === 'broker')
                        .map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name}
                          </option>
                        ))}
                    </select>
                  </Field>
                  <Field label="确认">
                    <label className="row" style={{ gap: 8, paddingTop: 6 }}>
                      <input
                        type="checkbox"
                        checked={confirmLive}
                        onChange={(event) => setConfirmLive(event.target.checked)}
                      />
                      <span>我确认使用真实资金</span>
                    </label>
                  </Field>
                </div>
              </>
            )}

            <Button type="submit" variant="primary" loading={starting}>
              启动网关
            </Button>
          </form>
        </Card>
      )}

      {running && (status?.recent_events?.length ?? 0) > 0 && (
        <Card title="事件流" description="最近事件（订单/成交/日志，每 2 秒刷新）" flush>
          <div className="table-wrap" style={{ maxHeight: 420 }}>
            <table>
              <thead>
                <tr>
                  <th>类型</th>
                  <th>内容</th>
                </tr>
              </thead>
              <tbody>
                {[...(status?.recent_events ?? [])]
                  .reverse()
                  .map((event: Record<string, unknown>, index: number) => (
                    <tr key={index}>
                      <td>
                        <Badge
                          tone={
                            event.type === 'fill'
                              ? 'success'
                              : event.type === 'order'
                                ? 'accent'
                                : event.type === 'log'
                                  ? 'warn'
                                  : 'neutral'
                          }
                        >
                          {String(event.type)}
                        </Badge>
                      </td>
                      <td className="tiny mono" style={{ whiteSpace: 'pre-wrap' }}>
                        {String(
                          JSON.stringify(
                            Object.fromEntries(Object.entries(event).filter(([k]) => k !== 'type')),
                          ),
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

/** 标的详情页：K 线 + 交易规则 + 直接选策略启动模拟盘/实盘。
 *
 * 入口：行情页的自选列表、排行榜、热门推荐点击代码/名称。
 * URL：``/market/CN:600519``、``/market/CRYPTO:BTC~USDT``（虚拟货币的 / 用 ~ 转义）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ApiError,
  credentialApi,
  liveApi,
  marketApi,
  newsApi,
  paperApi,
  riskApi,
  strategyApi,
} from '../api/client'
import type {
  BarsResponse,
  Credential,
  CustomStrategy,
  InstrumentInfo,
  LiveAdapter,
  NewsFactor,
  PaperStartRequest,
  RiskPresets,
  Strategy,
} from '../api/types'
import { KLineChart } from '../components/Charts'
import { Badge, Banner, Button, Card, Empty, Field, Loading, Stat } from '../components/ui'
import { IconRefresh } from '../components/icons'
import {
  fmtCompact,
  fmtNumber,
  fmtPct,
  fmtPrice,
  fmtSignedPct,
  marketLabel,
  marketTone,
  priceDigits,
} from '../utils/format'
import { cleanParams, defaultsFor, riskRulesFor } from '../utils/strategy'
import { segmentToSymbol } from '../utils/symbolPath'
import { useWatchlist } from '../store/watchlist'
import { notify } from '../store/toast'

type RangeKey = 'today' | 'week' | 'month' | '1y' | '3y' | '5y'
type KlineInterval = '1m' | '5m' | '15m' | '30m' | '60m' | '1d' | '1w' | '1M'

const RANGES: {
  key: RangeKey
  label: string
  interval: KlineInterval
  limit: number
  minute: boolean
  defaultVisible: number
  upgradeAt: number
}[] = [
  {
    key: 'today',
    label: '今日',
    interval: '5m',
    limit: 500,
    minute: true,
    defaultVisible: 48,
    upgradeAt: 100,
  },
  {
    key: 'week',
    label: '本周',
    interval: '30m',
    limit: 500,
    minute: true,
    defaultVisible: 40,
    upgradeAt: 80,
  },
  {
    key: 'month',
    label: '本月',
    interval: '1d',
    limit: 40,
    minute: false,
    defaultVisible: 30,
    upgradeAt: 90,
  },
  {
    key: '1y',
    label: '1 年',
    interval: '1d',
    limit: 260,
    minute: false,
    defaultVisible: 120,
    upgradeAt: 180,
  },
  { key: '3y', label: '3 年', interval: '1w', limit: 160, minute: false, defaultVisible: 80, upgradeAt: 120 },
  { key: '5y', label: '5 年', interval: '1M', limit: 72, minute: false, defaultVisible: 60, upgradeAt: 9999 },
]

const INTERVAL_LABELS: Record<KlineInterval, string> = {
  '1m': '1 分钟',
  '5m': '5 分钟',
  '15m': '15 分钟',
  '30m': '30 分钟',
  '60m': '60 分钟',
  '1d': '日线',
  '1w': '周线',
  '1M': '月线',
}

export function InstrumentPage() {
  const { code = '' } = useParams()
  const symbol = useMemo(() => segmentToSymbol(code), [code])
  const navigate = useNavigate()
  const addWatchItem = useWatchlist((state) => state.add)
  const loadWatchlist = useWatchlist((state) => state.load)

  const [info, setInfo] = useState<InstrumentInfo | null>(null)
  const [bars, setBars] = useState<BarsResponse | null>(null)
  const [range, setRange] = useState<RangeKey>('today')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [barsLoading, setBarsLoading] = useState(false)
  const [newsFactor, setNewsFactor] = useState<NewsFactor | null>(null)

  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [customs, setCustoms] = useState<CustomStrategy[]>([])
  const [presets, setPresets] = useState<RiskPresets | null>(null)
  const [adapters, setAdapters] = useState<LiveAdapter[]>([])
  const [credentials, setCredentials] = useState<Credential[]>([])

  // 交易面板
  const [panel, setPanel] = useState<'paper' | 'live'>('paper')
  const [strategyId, setStrategyId] = useState('dual_ma')
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [customId, setCustomId] = useState<number | null>(null)
  const [initialCash, setInitialCash] = useState(1_000_000)
  const [paperMode, setPaperMode] = useState<'replay' | 'live'>('replay')
  const [riskPreset, setRiskPreset] = useState('balanced')
  const [adapter, setAdapter] = useState('ccxt')
  const [credentialId, setCredentialId] = useState<number | null>(null)
  const [sandbox, setSandbox] = useState(true)
  const [confirmLive, setConfirmLive] = useState(false)
  const [starting, setStarting] = useState(false)
  const [runningNote, setRunningNote] = useState('')

  useEffect(() => {
    void loadWatchlist()
  }, [loadWatchlist])

  const strategy = useMemo(() => strategies.find((item) => item.id === strategyId), [strategies, strategyId])
  const brokerCredentials = useMemo(() => credentials.filter((item) => item.kind === 'broker'), [credentials])

  useEffect(() => {
    let cancelled = false
    void newsApi
      .factor(symbol, '1h')
      .then((value) => {
        if (!cancelled) setNewsFactor(value)
      })
      .catch(() => {
        if (!cancelled) setNewsFactor(null)
      })
    return () => {
      cancelled = true
    }
  }, [symbol])

  // ---- 基础数据（标的规则 + 策略/风控/实盘配置） --------------------------- //
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const [infoR, strategyR, customR, presetR, adapterR, credR] = await Promise.allSettled([
        marketApi.instrument(symbol),
        strategyApi.list(),
        strategyApi.custom(),
        riskApi.presets(),
        liveApi.adapters(),
        credentialApi.list(),
      ])
      if (cancelled) return
      if (infoR.status === 'fulfilled') setInfo(infoR.value)
      else setError(infoR.reason instanceof ApiError ? infoR.reason.message : '标的加载失败')

      if (strategyR.status === 'fulfilled') {
        setStrategies(strategyR.value)
        const preferred = strategyR.value.find((item) => item.id === 'dual_ma') ?? strategyR.value[0]
        if (preferred) {
          setStrategyId(preferred.id)
          setParams(defaultsFor(preferred))
        }
      }
      if (customR.status === 'fulfilled') {
        const enabled = customR.value.filter((item) => item.enabled)
        setCustoms(enabled)
        if (enabled[0]) setCustomId(enabled[0].id)
      }
      if (presetR.status === 'fulfilled') {
        setPresets(presetR.value)
        setRiskPreset(presetR.value.default || 'balanced')
      }
      if (adapterR.status === 'fulfilled') {
        setAdapters(adapterR.value)
        if (adapterR.value[0]) setAdapter(adapterR.value[0].name)
      }
      if (credR.status === 'fulfilled') {
        setCredentials(credR.value)
        const first = credR.value.find((item) => item.kind === 'broker')
        if (first) setCredentialId(first.id)
      }
      setLoading(false)
    })()
    return () => {
      cancelled = true
    }
  }, [symbol])

  // ---- K 线 ---------------------------------------------------------------- //
  const loadChart = useCallback(
    (_refresh = false) => {
      if (!symbol) return
      setBarsLoading(true)
      const config = RANGES.find((item) => item.key === range) ?? RANGES[0]
      const request = marketApi.klines(symbol, config.interval, config.limit)
      request
        .then((result) => {
          setBars(result)
          setError('')
        })
        .catch((err) => {
          // 刷新失败保留上一份数据，只提示错误
          setError(err instanceof ApiError ? err.message : '行情加载失败')
        })
        .finally(() => setBarsLoading(false))
    },
    [symbol, range],
  )

  useEffect(() => {
    loadChart()
  }, [loadChart])

  // 今日/本周是分钟线：每 15 秒自动刷新（页面可见时）
  useEffect(() => {
    if (range !== 'today' && range !== 'week') return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') loadChart()
    }, 15_000)
    return () => window.clearInterval(timer)
  }, [range, loadChart])

  // 一直缩小到数据最左端时，自动切到更长的周期（今日→本周→本月→1年→3年→5年）
  const zoomLock = useRef(Date.now())
  const upgrade = useCallback(() => {
    const index = RANGES.findIndex((item) => item.key === range)
    const next = RANGES[index + 1]
    if (!next) return
    zoomLock.current = Date.now()
    setRange(next.key)
    notify('info', `已扩展到${next.label}（${INTERVAL_LABELS[next.interval]}）`)
  }, [range])

  // 缩到当前周期的合理上限（可视根数阈值）时，自动切到更长周期
  const onZoomState = useCallback(
    (info: { visibleCount: number; start: number; end: number }) => {
      if (Date.now() - zoomLock.current < 1500) return
      const config = RANGES.find((item) => item.key === range)
      if (!config) return
      // 可视根数超过当前周期上限，或已缩放到接近全部数据（数据量小的周期靠后者）
      const nearAll = info.end - info.start >= 92
      if (info.visibleCount < config.upgradeAt && !nearAll) return
      upgrade()
    },
    [range, upgrade],
  )

  const zoomOut = useCallback(() => {
    if (Date.now() - zoomLock.current < 1500) return
    upgrade()
  }, [upgrade])

  // 港股/美股暂不支持分钟线：加载到市场信息后自动切到本月
  useEffect(() => {
    if (!info) return
    if ((info.market === 'HK' || info.market === 'US') && (range === 'today' || range === 'week')) {
      setRange('month')
    }
  }, [info, range])

  // 实时刷新现价/涨跌（3 秒；页面隐藏时不轮询）
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return
      marketApi
        .instrument(symbol)
        .then((fresh) => setInfo(fresh))
        .catch(() => undefined)
    }, 3000)
    return () => window.clearInterval(timer)
  }, [symbol])

  // ---- 当前会话是否在跑这个标的 -------------------------------------------- //
  useEffect(() => {
    Promise.allSettled([paperApi.state(), liveApi.state()]).then(([paper, live]) => {
      const notes: string[] = []
      if (
        paper.status === 'fulfilled' &&
        paper.value?.status === 'running' &&
        paper.value.symbols.includes(symbol)
      ) {
        notes.push(`模拟盘正在运行（${paper.value.run_id}）`)
      }
      if (
        live.status === 'fulfilled' &&
        live.value?.status === 'running' &&
        live.value.symbols.includes(symbol)
      ) {
        notes.push(`实盘正在运行（${live.value.run_id}）`)
      }
      setRunningNote(notes.join('；'))
    })
  }, [symbol])

  const changeStrategy = (id: string) => {
    setStrategyId(id)
    setParams(defaultsFor(strategies.find((item) => item.id === id)))
  }

  const buildParams = (): Record<string, unknown> => {
    const out = cleanParams(strategy, params)
    if (strategyId === 'custom_rule' && customId) out.custom_strategy_id = customId
    return out
  }

  const startPaper = async () => {
    setError('')
    if (strategyId === 'custom_rule' && !customId) {
      setError('请选择要运行的自定义策略')
      return
    }
    const body: PaperStartRequest = {
      strategy_id: strategyId,
      symbols: [symbol],
      initial_cash: initialCash,
      params: buildParams(),
      risk: riskRulesFor(presets, riskPreset),
      mode: paperMode,
      replay_days: 120,
      replay_interval: 0.35,
      poll_interval: 5,
      respect_market_hours: true,
    }
    setStarting(true)
    try {
      await paperApi.start(body)
      notify('success', `${symbol} 模拟盘已启动`)
      navigate('/paper')
    } catch (err) {
      const message = err instanceof ApiError ? err.message : '模拟盘启动失败'
      setError(message)
      notify('error', message)
    } finally {
      setStarting(false)
    }
  }

  const startLive = async () => {
    setError('')
    if (strategyId === 'custom_rule' && !customId) {
      setError('请选择要运行的自定义策略')
      return
    }
    if (credentialId === null) {
      setError('请先在「设置 → 凭据」创建券商凭据')
      return
    }
    if (!sandbox && !confirmLive) {
      setError('关闭测试网意味着真实资金下单，请勾选确认')
      return
    }
    setStarting(true)
    try {
      await liveApi.start({
        strategy_id: strategyId,
        symbols: [symbol],
        initial_cash: initialCash,
        params: buildParams(),
        adapter,
        credential_id: credentialId,
        sandbox,
        confirm_live: confirmLive,
        poll_interval: 5,
        warmup_days: 120,
      })
      notify('success', `${symbol} 实盘已启动（${sandbox ? '测试网' : '真实资金'}）`)
      navigate('/live')
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.status === 403
            ? '实盘功能未开启：请在服务端设置 QUANT_ENABLE_LIVE_TRADING=true'
            : err.message
          : '实盘启动失败'
      setError(message)
      notify('error', message)
    } finally {
      setStarting(false)
    }
  }

  const addWatch = async () => {
    if (await addWatchItem(symbol)) notify('success', `${symbol} 已加入自选`)
    else notify('info', '该标的已在自选中')
  }

  if (loading) return <Loading text="正在加载标的详情…" />

  if (!symbol) {
    return <Empty title="标的无效" hint="请从行情页选择一个标的。" />
  }

  const digits = info ? priceDigits(info.market, info.price) : 2
  const currency = info?.currency
  const changePct = info?.change_pct ?? null
  const limitText = info && info.limit_pct > 0 ? `±${(info.limit_pct * 100).toFixed(0)}%` : '无限制'
  const settleText = info?.t_plus_1 ? 'T+1（次日可卖）' : 'T+0（当日可卖）'
  const lotText = info && info.lot_size > 1 ? `${info.lot_size} 股/手` : '任意数量（含小数）'

  return (
    <div className="col" style={{ gap: 18 }}>
      <div className="row between" style={{ flexWrap: 'wrap', gap: 10 }}>
        <div className="row" style={{ gap: 10, flexWrap: 'wrap', alignItems: 'baseline' }}>
          <Button size="sm" variant="ghost" onClick={() => navigate('/market')}>
            ← 行情
          </Button>
          <span className="mono strong" style={{ fontSize: 20 }}>
            {info?.code || symbol}
          </span>
          <span style={{ fontSize: 16 }}>{info?.name || ''}</span>
          {info && <Badge tone={marketTone(info.market)}>{marketLabel(info.market)}</Badge>}
          {info && <Badge tone="neutral">{info.board}</Badge>}
        </div>
        <div className="row" style={{ gap: 6 }}>
          <Button size="sm" onClick={() => void addWatch()}>
            加入自选
          </Button>
          <Button size="sm" variant="ghost" loading={barsLoading} onClick={() => loadChart(true)}>
            <IconRefresh />
          </Button>
        </div>
      </div>

      {error && <Banner kind="danger">{error}</Banner>}
      {runningNote && <Banner kind="info">{runningNote}</Banner>}

      <div className="grid grid-4">
        <Stat
          label="现价"
          value={info && info.price > 0 ? fmtPrice(info.price, currency, digits) : '—'}
          sub={info?.source || ''}
          tone={changePct !== null ? (changePct >= 0 ? 'up' : 'down') : undefined}
        />
        <Stat
          label="涨跌幅"
          value={changePct !== null ? fmtSignedPct(changePct / 100) : '—'}
          sub={
            info?.change !== null && info?.change !== undefined
              ? `${info.change >= 0 ? '+' : ''}${fmtNumber(info.change, digits)}`
              : ''
          }
          tone={changePct !== null ? (changePct >= 0 ? 'up' : 'down') : undefined}
        />
        <Stat
          label="昨收 / 今开"
          value={info?.prev_close != null ? fmtPrice(info.prev_close, currency, digits) : '—'}
          sub={
            info?.limit_up
              ? `涨停 ${fmtNumber(info.limit_up, digits)} / 跌停 ${fmtNumber(info.limit_down ?? 0, digits)}`
              : ''
          }
        />
        <Stat
          label="成交额"
          value={fmtCompact(info?.amount ?? null)}
          sub={`成交量 ${fmtCompact(info?.volume ?? null)}`}
        />
      </div>

      <Card
        title="新闻/政策因子"
        description="1h 窗口 · 情绪/政策风险来自「新闻」页；回测按有效时间 Point-in-Time 对齐"
        actions={
          <Button size="sm" variant="ghost" onClick={() => navigate('/news')}>
            管理新闻
          </Button>
        }
      >
        <div className="grid grid-4">
          <Stat
            label="新闻情绪"
            value={fmtSignedPct(newsFactor?.sentiment ?? 0)}
            sub={`${newsFactor?.event_count ?? 0} 条事件`}
            tone={(newsFactor?.sentiment ?? 0) >= 0 ? 'up' : 'down'}
          />
          <Stat
            label="政策风险"
            value={fmtPct(newsFactor?.policy_risk ?? 0)}
            sub="窗口内最大风险"
            tone={(newsFactor?.policy_risk ?? 0) > 0.5 ? 'down' : undefined}
          />
          <Stat label="置信度" value={fmtPct(newsFactor?.confidence ?? 0)} sub="最高条目" />
          <Stat
            label="新闻窗口"
            value={newsFactor?.window ?? '1h'}
            sub={newsFactor?.as_of ? 'PIT 计算' : '暂无新闻'}
          />
        </div>
      </Card>

      <Card
        title="K 线"
        description={
          bars?.symbol
            ? `${bars.symbol} · ${bars.name || ''} · ${
                RANGES.find((item) => item.key === range)?.label ?? ''
              }（${INTERVAL_LABELS[(RANGES.find((item) => item.key === range) ?? RANGES[0]).interval]}${
                range === 'today' || range === 'week' ? '，15 秒刷新' : ''
              }）· 持续缩小会自动扩展到更长的周期`
            : '行情走势'
        }
        actions={
          <div className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
            {RANGES.map((item) => {
              const disabled = item.minute && info != null && info.market !== 'CN' && info.market !== 'CRYPTO'
              return (
                <Button
                  key={item.key}
                  size="sm"
                  variant={range === item.key ? 'primary' : 'ghost'}
                  disabled={disabled}
                  title={disabled ? '该市场暂不支持分钟线' : ''}
                  onClick={() => setRange(item.key)}
                >
                  {item.label}
                </Button>
              )
            })}
          </div>
        }
        flush
      >
        <KLineChart
          bars={bars?.bars ?? []}
          height={400}
          defaultVisible={RANGES.find((item) => item.key === range)?.defaultVisible}
          onZoomOut={zoomOut}
          onZoomState={onZoomState}
        />
      </Card>

      <div className="grid grid-sidebar">
        <Card title="交易规则" description="由 core.markets.BoardRules 统一给出，回测/纸盘/实盘一致">
          <dl className="kv">
            <dt>市场</dt>
            <dd>
              {marketLabel(info?.market)} {info ? <span className="faint tiny">{info.currency}</span> : null}
            </dd>
            <dt>板块</dt>
            <dd>{info?.board || '—'}</dd>
            <dt>涨跌停</dt>
            <dd>{limitText}</dd>
            <dt>涨跌停价</dt>
            <dd className="mono">
              {info?.limit_up != null && info?.limit_down != null
                ? `${fmtNumber(info.limit_up, digits)} / ${fmtNumber(info.limit_down, digits)}`
                : '—'}
            </dd>
            <dt>最小单位</dt>
            <dd>{lotText}</dd>
            <dt>交收</dt>
            <dd>{settleText}</dd>
            <dt>报价精度</dt>
            <dd>{digits} 位小数</dd>
          </dl>
        </Card>

        <Card
          title="启动交易"
          description={`对 ${symbol} 直接运行策略：模拟盘走回放/实时模拟，实盘发往交易所`}
        >
          <div className="col" style={{ gap: 14 }}>
            <div className="row" style={{ gap: 6 }}>
              <Button
                size="sm"
                variant={panel === 'paper' ? 'primary' : 'ghost'}
                onClick={() => setPanel('paper')}
              >
                模拟盘
              </Button>
              <Button
                size="sm"
                variant={panel === 'live' ? 'primary' : 'ghost'}
                onClick={() => setPanel('live')}
              >
                实盘
              </Button>
            </div>

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

            {strategyId === 'custom_rule' && (
              <Field label="自定义策略" hint={customs.length === 0 ? '还没有可用策略，请到策略库创建' : ''}>
                <select
                  className="select"
                  value={customId ?? ''}
                  onChange={(event) => setCustomId(event.target.value ? Number(event.target.value) : null)}
                >
                  <option value="">— 请选择 —</option>
                  {customs.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </Field>
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
                  ) : spec.type === 'choice' ? (
                    <select
                      className="select"
                      value={String(params[spec.name] ?? '')}
                      onChange={(event) =>
                        setParams((prev) => ({ ...prev, [spec.name]: event.target.value }))
                      }
                    >
                      {(spec.choices ?? []).map((choice) => (
                        <option key={String(choice)} value={String(choice)}>
                          {String(choice)}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      className="input mono"
                      type={spec.type === 'int' || spec.type === 'float' ? 'number' : 'text'}
                      step={spec.step ?? (spec.type === 'int' ? 1 : 'any')}
                      min={spec.min ?? undefined}
                      max={spec.max ?? undefined}
                      value={String(params[spec.name] ?? '')}
                      onChange={(event) =>
                        setParams((prev) => ({ ...prev, [spec.name]: event.target.value }))
                      }
                    />
                  )}
                </Field>
              ))}

            <Field label="初始资金（元）">
              <input
                className="input mono"
                type="number"
                min={1000}
                step={1000}
                value={initialCash}
                onChange={(event) => setInitialCash(Number(event.target.value))}
              />
            </Field>

            {panel === 'paper' ? (
              <>
                <div className="form-grid">
                  <Field
                    label="模式"
                    hint={paperMode === 'replay' ? '倍速回放最近 120 个交易日' : '盘中轮询实时价'}
                  >
                    <select
                      className="select"
                      value={paperMode}
                      onChange={(event) => setPaperMode(event.target.value as 'replay' | 'live')}
                    >
                      <option value="replay">回放</option>
                      <option value="live">实时模拟</option>
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
                          {key}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <Button block loading={starting} onClick={() => void startPaper()}>
                  启动模拟盘
                </Button>
              </>
            ) : (
              <>
                <div className="form-grid">
                  <Field
                    label="通道"
                    hint={adapters.find((a) => a.name === adapter)?.markets.join(' / ') || ''}
                  >
                    <select
                      className="select"
                      value={adapter}
                      onChange={(event) => setAdapter(event.target.value)}
                    >
                      {adapters.map((item) => (
                        <option key={item.name} value={item.name}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="券商凭据">
                    <select
                      className="select"
                      value={credentialId ?? ''}
                      onChange={(event) =>
                        setCredentialId(event.target.value ? Number(event.target.value) : null)
                      }
                    >
                      <option value="">— 请选择 —</option>
                      {brokerCredentials.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <label className="row" style={{ gap: 8, alignItems: 'center' }}>
                  <input
                    type="checkbox"
                    checked={sandbox}
                    onChange={(event) => {
                      setSandbox(event.target.checked)
                      if (event.target.checked) setConfirmLive(false)
                    }}
                  />
                  <span className="small">{sandbox ? '测试网 / 模拟账户' : '真实资金'}</span>
                </label>
                {!sandbox && (
                  <label className="row" style={{ gap: 8, alignItems: 'center' }}>
                    <input
                      type="checkbox"
                      checked={confirmLive}
                      onChange={(event) => setConfirmLive(event.target.checked)}
                    />
                    <span className="small">我确认使用真实资金</span>
                  </label>
                )}
                <Button
                  block
                  variant={sandbox ? 'primary' : 'danger'}
                  loading={starting}
                  onClick={() => void startLive()}
                >
                  启动实盘
                </Button>
                <div className="tiny faint">
                  实盘需要服务端开启 QUANT_ENABLE_LIVE_TRADING；未开启时启动会返回 403。
                </div>
              </>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}

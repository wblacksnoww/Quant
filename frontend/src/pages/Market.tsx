/** 行情研究页：市场分类（A股/美股/港股）、实时自选、K 线、均线、成交量与本地缓存管理。 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, marketApi } from '../api/client'
import type { BarsResponse, DataCacheItem, QuoteItem, SymbolInfo } from '../api/types'
import { KLineChart } from '../components/Charts'
import { CardBoard } from '../components/CardBoard'
import { IndexCards } from '../components/IndexCards'
import { HotPicks, RankingBoard, type RankMarket } from '../components/Ranking'
import { Badge, Banner, Button, Card, Empty, Field, Loading, Stat } from '../components/ui'
import { IconRefresh, IconSearch, IconTrash } from '../components/icons'
import { usePolling } from '../hooks/usePolling'
import {
  classForSign,
  displayCode,
  fmtCompact,
  fmtPrice,
  fmtSignedPct,
  marketLabel,
  marketTone,
  normalizeInstrumentId,
  priceDigits,
  todayISO,
  yearsAgoISO,
} from '../utils/format'
import { notify } from '../store/toast'
import { symbolToSegment } from '../utils/symbolPath'
import { useWatchlist } from '../store/watchlist'

const QUOTES_CACHE_KEY = 'quant.quotes_cache'

/** 上次成功的实时快照：刷新页面时先渲染，避免整表短暂变成 "—"。 */
function loadQuotesCache(): Record<string, QuoteItem> {
  try {
    const raw = localStorage.getItem(QUOTES_CACHE_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const out: Record<string, QuoteItem> = {}
      for (const [id, quote] of Object.entries(parsed as Record<string, QuoteItem>)) {
        out[id] = { ...quote, stale: true }
      }
      return out
    }
  } catch {
    // 缓存损坏时忽略
  }
  return {}
}

const QUOTE_INTERVAL_KEY = 'quant.quote_interval'

type QuoteRow = {
  id: string
  quote?: QuoteItem
  market: string
}

export function MarketPage() {
  const navigate = useNavigate()
  const goDetail = (symbol: string) => navigate(`/market/${symbolToSegment(symbol)}`)
  const [symbol, setSymbol] = useState('CN:600519')
  const [input, setInput] = useState('CN:600519')
  const [years, setYears] = useState(3)
  const [data, setData] = useState<BarsResponse | null>(null)
  const [cache, setCache] = useState<DataCacheItem[]>([])
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')

  // ---- 实时自选 ----
  const watchlist = useWatchlist((state) => state.items)
  const setWatchlistItems = useWatchlist((state) => state.setItems)
  const loadWatchlist = useWatchlist((state) => state.load)
  const addWatchItem = useWatchlist((state) => state.add)
  const [quotes, setQuotes] = useState<Record<string, QuoteItem>>(loadQuotesCache)
  const [quoteNote, setQuoteNote] = useState('')
  const [quoteAt, setQuoteAt] = useState('')
  const [quoteLoading, setQuoteLoading] = useState(false)
  const [quoteInterval, setQuoteInterval] = useState(() => {
    const raw = localStorage.getItem(QUOTE_INTERVAL_KEY)
    if (raw === null) return 5000
    const saved = Number(raw)
    return Number.isFinite(saved) && saved >= 0 ? saved : 5000
  })
  const [searchText, setSearchText] = useState('')
  const [searchMarket, setSearchMarket] = useState<'ALL' | RankMarket>('ALL')
  const [suggestions, setSuggestions] = useState<SymbolInfo[]>([])
  const [searching, setSearching] = useState(false)

  const loadCache = useCallback(async () => {
    try {
      setCache(await marketApi.cache())
    } catch {
      // 缓存接口失败不影响主流程
    }
  }, [])

  const load = useCallback(
    async (code: string, refresh = false) => {
      const trimmed = code.trim()
      if (!trimmed) return
      if (refresh) setRefreshing(true)
      else setLoading(true)
      setError('')
      try {
        const result = await marketApi.bars(trimmed, yearsAgoISO(years), todayISO(), refresh)
        setData(result)
        setSymbol(result.symbol)
        setInput(result.symbol)
        await loadCache()
        if (refresh) notify('success', `${result.symbol} 行情已刷新`)
      } catch (err) {
        const message = err instanceof ApiError ? err.message : '行情加载失败'
        setError(message)
        // 刷新失败保留当前图表；只有切换标的失败才清空，避免看到上一个标的的图
        if (!refresh) setData(null)
      } finally {
        setLoading(false)
        setRefreshing(false)
      }
    },
    [years, loadCache],
  )

  useEffect(() => {
    void loadWatchlist()
  }, [loadWatchlist])

  // ---- 实时自选：拉取 / 自动刷新 ----
  const loadQuotes = useCallback(
    async (silent = false) => {
      if (watchlist.length === 0) {
        setQuotes({})
        setQuoteNote('')
        try {
          localStorage.removeItem(QUOTES_CACHE_KEY)
        } catch {
          // 忽略
        }
        return
      }
      if (!silent) setQuoteLoading(true)
      try {
        const result = await marketApi.quote(watchlist)
        setQuotes((prev) => {
          // 合并而不是整体替换：本轮缺失的标的保留上次价格并标记陈旧，
          // 避免行情源抖动时整张自选表变成 "—"
          const merged: Record<string, QuoteItem> = { ...prev, ...result.quotes }
          for (const id of watchlist) {
            if (!result.quotes[id] && merged[id]) {
              merged[id] = { ...merged[id], stale: true }
            }
          }
          try {
            localStorage.setItem(QUOTES_CACHE_KEY, JSON.stringify(merged))
          } catch {
            // 存储不可用时忽略
          }
          return merged
        })
        setQuoteNote(result.note)
        setQuoteAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
      } catch (err) {
        setQuoteNote(err instanceof ApiError ? err.message : '实时行情获取失败')
      } finally {
        if (!silent) setQuoteLoading(false)
      }
    },
    [watchlist],
  )

  useEffect(() => {
    void loadQuotes(false)
  }, [loadQuotes])

  // 自动轮询走静默模式，避免 spinner 每秒闪烁；页面切后台时由 usePolling 暂停
  usePolling(() => loadQuotes(true), {
    interval: quoteInterval,
    enabled: quoteInterval > 0,
    immediate: false,
  })

  // ---- 标的检索（防抖），按市场过滤 ----
  useEffect(() => {
    const keyword = searchText.trim()
    if (!keyword) {
      setSuggestions([])
      return
    }
    const timer = window.setTimeout(() => {
      setSearching(true)
      void marketApi
        .search(keyword, 8, searchMarket === 'ALL' ? null : searchMarket)
        .then((items) => setSuggestions(items.filter((item) => !watchlist.includes(item.symbol))))
        .catch(() => setSuggestions([]))
        .finally(() => setSearching(false))
    }, 300)
    return () => window.clearTimeout(timer)
  }, [searchText, searchMarket, watchlist])

  const updateWatchlist = (next: string[]) => {
    void setWatchlistItems(next)
  }

  const addSymbol = (code: string) => {
    const normalized = normalizeInstrumentId(code) ?? code.trim()
    if (!normalized) return
    void (async () => {
      const added = await addWatchItem(normalized)
      if (added) notify('success', `${normalized} 已加入自选`)
      else notify('info', `${normalized} 已在自选中`)
    })()
    setSearchText('')
    setSuggestions([])
  }

  const removeSymbol = (code: string) => {
    updateWatchlist(watchlist.filter((item) => item !== code))
    setQuotes((prev) => {
      const next = { ...prev }
      delete next[code]
      return next
    })
  }

  const changeQuoteInterval = (value: number) => {
    setQuoteInterval(value)
    localStorage.setItem(QUOTE_INTERVAL_KEY, String(value))
  }

  useEffect(() => {
    // 首次进入优先展示已有缓存，否则用默认标的
    void (async () => {
      let initial = 'CN:600519'
      try {
        const items = await marketApi.cache()
        setCache(items)
        if (items.length > 0) initial = items[0].symbol
      } catch {
        // 缓存不可用时仍尝试默认标的
      }
      setInput(initial)
      await load(initial)
    })()
    // 只跑一次；后续由用户操作触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const stats = useMemo(() => {
    if (!data || data.bars.length === 0) return null
    const bars = data.bars
    const last = bars[bars.length - 1]
    const prevClose = last.prev_close ?? (bars.length > 1 ? bars[bars.length - 2].close : last.open)
    const change = prevClose > 0 ? last.close / prevClose - 1 : 0
    const high = Math.max(...bars.map((bar) => bar.high))
    const low = Math.min(...bars.map((bar) => bar.low))
    const totalAmount = bars.reduce((sum, bar) => sum + (bar.amount || 0), 0)
    return { last, prevClose, change, high, low, totalAmount }
  }, [data])

  /** 展示用：从规范 id 截出代码部分（``CN:600519`` → ``600519``）。 */

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void load(input)
  }

  // 自选行视图模型
  const watchRows = useMemo<QuoteRow[]>(
    () =>
      watchlist.map((id) => {
        const index = id.indexOf(':')
        return {
          id,
          quote: quotes[id],
          market: quotes[id]?.market ?? (index >= 0 ? id.slice(0, index) : ''),
        }
      }),
    [watchlist, quotes],
  )

  return (
    <div className="col" style={{ gap: 0 }}>
      <CardBoard
        storageKey="quant.layout.market"
        items={[
          {
            id: 'indices',
            title: '指数',
            wide: true,
            render: () => <IndexCards />,
          },
          {
            id: 'quotes',
            title: '实时行情',
            wide: true,
            render: () => (
              <Card
                title="实时行情"
                description="自选快照：腾讯 → 东财 → 新浪 多源聚合，支持 A股 / 美股 / 港股"
                actions={
                  <div className="row" style={{ gap: 8, alignItems: 'center' }}>
                    {quoteAt && <span className="tiny faint">更新于 {quoteAt}</span>}
                    <select
                      className="select"
                      value={quoteInterval}
                      onChange={(event) => changeQuoteInterval(Number(event.target.value))}
                      style={{ width: 110 }}
                    >
                      <option value={1000}>每 1 秒</option>
                      <option value={3000}>每 3 秒</option>
                      <option value={5000}>每 5 秒</option>
                      <option value={10000}>每 10 秒</option>
                      <option value={30000}>每 30 秒</option>
                      <option value={0}>暂停刷新</option>
                    </select>
                    <Button size="sm" loading={quoteLoading} onClick={() => void loadQuotes(false)}>
                      <IconRefresh /> 立即刷新
                    </Button>
                  </div>
                }
              >
                <div className="row wrap" style={{ gap: 8, alignItems: 'flex-end' }}>
                  <div className="suggest-anchor" style={{ flex: '1 1 320px', maxWidth: 420 }}>
                    <input
                      className="input"
                      value={searchText}
                      onChange={(event) => setSearchText(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key !== 'Enter') return
                        event.preventDefault()
                        if (suggestions.length > 0) addSymbol(suggestions[0].symbol)
                        else if (normalizeInstrumentId(searchText)) addSymbol(searchText.trim())
                      }}
                      placeholder="搜索代码 / 名称（600519、茅台、AAPL、00700、CRYPTO:BTC/USDT），回车加入自选"
                    />
                    {searchText.trim() !== '' && (
                      <div className="suggest-list">
                        {searching && <div className="suggest-item faint">搜索中…</div>}
                        {!searching && suggestions.length === 0 && (
                          <div className="suggest-item faint">
                            {normalizeInstrumentId(searchText)
                              ? '无匹配结果，回车按代码直接加入'
                              : '无匹配标的'}
                          </div>
                        )}
                        {suggestions.map((item) => (
                          <div
                            key={item.symbol}
                            className="suggest-item"
                            role="button"
                            tabIndex={0}
                            title="查看详情"
                            onClick={() => goDetail(item.symbol)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') goDetail(item.symbol)
                            }}
                          >
                            <span className="mono strong">{item.code ?? displayCode(item.symbol)}</span>
                            <span>{item.name || '—'}</span>
                            {item.kind === 'etf' && <Badge tone="success">ETF</Badge>}
                            <span className="tiny faint" style={{ marginLeft: 'auto' }}>
                              {marketLabel(item.market)} · {item.board}
                            </span>
                            <button
                              type="button"
                              className="btn btn-sm"
                              title="加入自选"
                              onClick={(event) => {
                                event.stopPropagation()
                                addSymbol(item.symbol)
                              }}
                            >
                              +
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="row" style={{ gap: 6 }}>
                    {(['ALL', 'CN', 'US', 'HK', 'CRYPTO'] as const).map((market) => (
                      <button
                        key={market}
                        type="button"
                        className={`btn btn-sm${searchMarket === market ? ' btn-primary' : ''}`}
                        onClick={() => setSearchMarket(market)}
                        title={market === 'ALL' ? '搜索全部市场' : `只搜索${marketLabel(market)}`}
                      >
                        {market === 'ALL' ? '全部' : marketLabel(market)}
                      </button>
                    ))}
                  </div>
                </div>

                <HotPicks
                  market={searchMarket === 'ALL' ? 'CN' : searchMarket}
                  watchlist={watchlist}
                  onSelect={goDetail}
                  onAdd={addSymbol}
                />

                {quoteNote && (
                  <div style={{ marginTop: 10 }}>
                    <Banner kind="warn">{quoteNote}</Banner>
                  </div>
                )}

                {watchlist.length === 0 ? (
                  <div style={{ marginTop: 12 }}>
                    <Empty title="自选为空" hint="用上方搜索框添加标的，支持代码或名称。" />
                  </div>
                ) : (
                  <div className="table-wrap" style={{ marginTop: 12 }}>
                    <table>
                      <thead>
                        <tr>
                          <th>代码</th>
                          <th>市场</th>
                          <th>名称</th>
                          <th className="right">现价</th>
                          <th className="right">涨跌幅</th>
                          <th className="right">涨跌</th>
                          <th className="right">今开 / 昨收</th>
                          <th className="right">最高 / 最低</th>
                          <th className="right">成交量</th>
                          <th className="right">成交额</th>
                          <th>数据时间</th>
                          <th className="right">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {watchRows.map(({ id, quote, market }) => {
                          const tone = classForSign(quote?.change_pct ?? quote?.change ?? null)
                          const currency = quote?.currency
                          const digits = priceDigits(market, quote?.price)
                          const label = marketLabel(market)
                          return (
                            <tr key={id}>
                              <td className="strong mono">
                                <button
                                  type="button"
                                  className="link-btn"
                                  title="查看详情"
                                  onClick={() => goDetail(id)}
                                >
                                  {displayCode(id)}
                                </button>
                              </td>
                              <td>{label ? <Badge tone={marketTone(market)}>{label}</Badge> : '—'}</td>
                              <td>
                                <span
                                  className="row"
                                  style={{
                                    gap: 6,
                                    alignItems: 'center',
                                    flexWrap: 'nowrap',
                                    whiteSpace: 'nowrap',
                                  }}
                                >
                                  <button
                                    type="button"
                                    className="link-btn"
                                    title="查看详情"
                                    onClick={() => goDetail(id)}
                                  >
                                    {quote?.name || '—'}
                                  </button>
                                  {quote?.stale && <Badge tone="warn">陈旧</Badge>}
                                </span>
                              </td>
                              <td className="right num strong">
                                {quote ? fmtPrice(quote.price, currency, digits) : '—'}
                              </td>
                              <td className={`right ${tone}`}>
                                {quote?.change_pct != null ? fmtSignedPct(quote.change_pct / 100) : '—'}
                              </td>
                              <td className={`right ${tone}`}>
                                {quote?.change != null
                                  ? `${quote.change >= 0 ? '+' : ''}${fmtPrice(quote.change, currency, digits)}`
                                  : '—'}
                              </td>
                              <td className="right num tiny">
                                {quote
                                  ? `${fmtPrice(quote.open, currency, digits)} / ${fmtPrice(quote.prev_close, currency, digits)}`
                                  : '—'}
                              </td>
                              <td className="right num tiny">
                                {quote
                                  ? `${fmtPrice(quote.high, currency, digits)} / ${fmtPrice(quote.low, currency, digits)}`
                                  : '—'}
                              </td>
                              <td className="right num tiny">
                                {quote?.volume != null ? fmtCompact(quote.volume) : '—'}
                              </td>
                              <td className="right num tiny">{fmtCompact(quote?.amount)}</td>
                              <td className="tiny faint">
                                {quote?.ts || '—'}
                                {quote?.source ? ` · ${quote.source}` : ''}
                              </td>
                              <td className="right">
                                <div className="row" style={{ justifyContent: 'flex-end', gap: 4 }}>
                                  <Button size="sm" variant="ghost" onClick={() => void load(id)}>
                                    查看
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    title="从自选移除"
                                    onClick={() => removeSymbol(id)}
                                  >
                                    <IconTrash />
                                  </Button>
                                </div>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            ),
          },
          {
            id: 'ranking',
            title: '排行榜',
            wide: true,
            render: () => <RankingBoard watchlist={watchlist} onSelect={goDetail} onAdd={addSymbol} />,
          },
          {
            id: 'search',
            title: 'K 线查询',
            wide: true,
            render: () => (
              <Card>
                <form className="row wrap" onSubmit={submit} style={{ alignItems: 'flex-end', gap: 12 }}>
                  <Field label="标的（兼容旧写法）" style={{ flex: '1 1 240px', maxWidth: 300 }}>
                    <input
                      className="input mono"
                      value={input}
                      onChange={(event) => setInput(event.target.value)}
                      placeholder="CN:600519 / US:AAPL / HK:00700 / CRYPTO:BTC/USDT"
                      style={{ width: 220 }}
                    />
                  </Field>
                  <Field label="时间范围">
                    <select
                      className="select"
                      value={years}
                      onChange={(event) => setYears(Number(event.target.value))}
                      style={{ width: 120 }}
                    >
                      <option value={1}>近 1 年</option>
                      <option value={3}>近 3 年</option>
                      <option value={5}>近 5 年</option>
                      <option value={10}>近 10 年</option>
                    </select>
                  </Field>
                  <Button type="submit" variant="primary" loading={loading}>
                    <IconSearch /> 加载行情
                  </Button>
                  <Button
                    loading={refreshing}
                    disabled={!symbol}
                    onClick={() => void load(symbol, true)}
                    title="跳过缓存，重新向数据源拉取"
                  >
                    <IconRefresh /> 强制刷新
                  </Button>
                  <div className="spacer" />
                  {data && (
                    <div className="row wrap" style={{ gap: 6 }}>
                      <Badge tone={data.market ? marketTone(data.market) : 'accent'}>
                        {marketLabel(data.market) || '—'}
                      </Badge>
                      <Badge tone={data.source === 'synthetic' ? 'warn' : 'accent'}>
                        数据源 {data.source || '—'}
                      </Badge>
                      <Badge tone={data.cached ? 'success' : 'neutral'}>
                        {data.cached ? '已缓存' : '未缓存'}
                      </Badge>
                      <Badge>{data.bars.length} 根</Badge>
                    </div>
                  )}
                </form>
                {cache.length > 0 && (
                  <>
                    <div className="divider" />
                    <div className="row wrap" style={{ gap: 6 }}>
                      <span className="tiny faint" style={{ marginRight: 4 }}>
                        已缓存：
                      </span>
                      {cache.map((item) => (
                        <button
                          key={item.symbol}
                          type="button"
                          className={`btn btn-sm${item.symbol === symbol ? ' btn-primary' : ''}`}
                          onClick={() => void load(item.symbol)}
                        >
                          {displayCode(item.symbol)}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </Card>
            ),
          },
          {
            id: 'chart',
            title: 'K 线与统计',
            wide: true,
            render: () => (
              <>
                {error && <Banner kind="danger">{error}</Banner>}
                {data?.source === 'synthetic' && (
                  <Banner kind="warn">
                    当前展示的是合成行情（synthetic）。它只用于验证系统链路，不能作为投资依据。 配置 akshare
                    或导入真实 CSV 后即可替换。
                  </Banner>
                )}
                {loading && !data ? (
                  <Card>
                    <Loading text="正在加载行情…" />
                  </Card>
                ) : data && stats ? (
                  <>
                    <div className="grid grid-4">
                      <Stat
                        label={`${displayCode(data.symbol)} ${data.name || ''}`}
                        value={fmtPrice(stats.last.close, data.currency)}
                        sub={
                          <span className={stats.change >= 0 ? 'up' : 'down'}>
                            {fmtSignedPct(stats.change)} · 昨收 {fmtPrice(stats.prevClose, data.currency)}
                          </span>
                        }
                        tone={stats.change >= 0 ? 'up' : 'down'}
                      />
                      <Stat
                        label="区间最高 / 最低"
                        value={`${fmtPrice(stats.high, data.currency)} / ${fmtPrice(stats.low, data.currency)}`}
                        sub={`${data.bars[0]?.date ?? ''} → ${stats.last.date}`}
                      />
                      <Stat
                        label="最新成交量（股）"
                        value={fmtCompact(stats.last.volume)}
                        sub={`成交额 ${fmtCompact(stats.last.amount)}`}
                      />
                      <Stat
                        label="区间成交额"
                        value={fmtCompact(stats.totalAmount)}
                        sub={`${data.bars.length} 个交易日`}
                      />
                    </div>

                    <Card
                      title={`${displayCode(data.symbol)} 日线`}
                      description={`红涨绿跌 · 前复权 · ${marketLabel(data.market)} · 货币 ${data.currency ?? ''}`}
                      actions={
                        <span className="tiny faint">
                          价格 {fmtPrice(stats.last.low, data.currency)} —{' '}
                          {fmtPrice(stats.last.high, data.currency)}
                        </span>
                      }
                    >
                      <KLineChart bars={data.bars} height={520} />
                    </Card>
                  </>
                ) : (
                  <Card>
                    <Empty
                      title="选择一只标的开始"
                      hint="输入代码（如 CN:600519 / US:AAPL / HK:00700 / CRYPTO:BTC/USDT），或点击上方已缓存的标的。"
                    />
                  </Card>
                )}
              </>
            ),
          },
          {
            id: 'cache',
            title: '本地缓存',
            wide: true,
            render: () => (
              <Card
                title="本地缓存"
                description="回测与模拟盘优先读取本地数据；删除缓存后会自动重新拉取"
                flush
              >
                {cache.length === 0 ? (
                  <Empty title="暂无缓存" hint="加载任意标的后会写入本地 parquet 缓存。" />
                ) : (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>代码</th>
                          <th>市场</th>
                          <th className="right">行数</th>
                          <th>起始</th>
                          <th>结束</th>
                          <th className="right">大小</th>
                          <th className="right">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {cache.map((item) => (
                          <tr key={item.symbol}>
                            <td className="strong mono">{displayCode(item.symbol)}</td>
                            <td>
                              <Badge tone={marketTone(item.market)}>{marketLabel(item.market) || '—'}</Badge>
                            </td>
                            <td className="right num">{item.rows.toLocaleString('zh-CN')}</td>
                            <td className="tiny faint">{item.start ?? '—'}</td>
                            <td className="tiny faint">{item.end ?? '—'}</td>
                            <td className="right num tiny">{item.size_kb.toFixed(1)} KB</td>
                            <td className="right">
                              <div className="row" style={{ justifyContent: 'flex-end', gap: 4 }}>
                                <Button size="sm" variant="ghost" onClick={() => void load(item.symbol)}>
                                  查看
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={async () => {
                                    try {
                                      await marketApi.refresh([item.symbol])
                                      notify('success', `${displayCode(item.symbol)} 已更新`)
                                      if (item.symbol === symbol) await load(item.symbol)
                                      else await loadCache()
                                    } catch (err) {
                                      notify('error', err instanceof ApiError ? err.message : '刷新失败')
                                    }
                                  }}
                                >
                                  刷新
                                </Button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            ),
          },
        ]}
      />
    </div>
  )
}

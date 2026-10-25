/** 行情页：热门推荐 + 市场排行榜（涨幅 / 跌幅 / 成交额）。
 *
 * 数据来自 `/api/market/ranking`：A股/港股取全市场榜单，美股用热门标的池，
 * 虚拟货币取主流交易对；后端 60 秒缓存，前端同样 60 秒自动刷新。
 */

import { useCallback, useState } from 'react'
import { marketApi } from '../api/client'
import type { QuoteItem, Ranking } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { Card, Empty, Button } from './ui'
import { IconRefresh } from './icons'
import { classForSign, fmtCompact, fmtPrice, fmtSignedPct, marketLabel, priceDigits } from '../utils/format'

export const RANK_MARKETS = [
  { code: 'CN', label: 'A股' },
  { code: 'US', label: '美股' },
  { code: 'HK', label: '港股' },
  { code: 'CRYPTO', label: '虚拟货币' },
] as const

export type RankMarket = (typeof RANK_MARKETS)[number]['code']

type RankSort = 'pct_desc' | 'pct_asc' | 'amount'
type RankPeriod = 'day' | 'week' | 'month'

const SORTS: { key: RankSort; label: string; sort: string; order: 'desc' | 'asc' }[] = [
  { key: 'pct_desc', label: '涨幅榜', sort: 'change_pct', order: 'desc' },
  { key: 'pct_asc', label: '跌幅榜', sort: 'change_pct', order: 'asc' },
  { key: 'amount', label: '成交额', sort: 'amount', order: 'desc' },
]

const PERIODS: { key: RankPeriod; label: string }[] = [
  { key: 'day', label: '今日' },
  { key: 'week', label: '本周' },
  { key: 'month', label: '本月' },
]

const PERIOD_LABELS: Record<RankPeriod, string> = { day: '今日', week: '本周', month: '本月' }

const DAY_REFRESH_MS = 5_000
const WINDOW_REFRESH_MS = 30_000

function pctText(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : fmtSignedPct(value / 100)
}

// --------------------------------------------------------------------------- //
// 热门推荐（搜索框下方）
// --------------------------------------------------------------------------- //

export function HotPicks({
  market,
  watchlist,
  onSelect,
  onAdd,
}: {
  market: RankMarket
  watchlist: string[]
  onSelect: (symbol: string) => void
  onAdd: (symbol: string) => void
}) {
  const [items, setItems] = useState<QuoteItem[]>([])

  const load = useCallback(() => {
    marketApi
      .ranking(market, 'amount', 'desc', 8)
      .then((result) => setItems(result.items))
      .catch(() => setItems([]))
  }, [market])

  usePolling(load, { interval: 30_000 })

  if (items.length === 0) return null

  return (
    <div className="col" style={{ gap: 6, marginTop: 12 }}>
      <div className="tiny faint">
        热门推荐 · {marketLabel(market)} <span className="faint">（按成交额）</span>
      </div>
      <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
        {items.map((item) => {
          const inWatch = watchlist.includes(item.symbol)
          return (
            <div key={item.symbol} className="row" style={{ gap: 2 }}>
              <button
                type="button"
                className="btn btn-sm"
                title={`${item.symbol} ${item.name || ''}`}
                onClick={() => onSelect(item.symbol)}
              >
                <span className="mono">{item.name || item.symbol}</span>
                <span className={`num ${classForSign(item.change_pct)}`}>{pctText(item.change_pct)}</span>
              </button>
              {!inWatch && (
                <button
                  type="button"
                  className="btn btn-sm"
                  title="加入自选"
                  onClick={() => onAdd(item.symbol)}
                >
                  +
                </button>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// 排行榜（独立卡片）
// --------------------------------------------------------------------------- //

export function RankingBoard({
  watchlist,
  onSelect,
  onAdd,
}: {
  watchlist: string[]
  onSelect: (symbol: string) => void
  onAdd: (symbol: string) => void
}) {
  const [market, setMarket] = useState<RankMarket>('CN')
  const [sortKey, setSortKey] = useState<RankSort>('pct_desc')
  const [period, setPeriod] = useState<RankPeriod>('day')
  const [data, setData] = useState<Ranking | null>(null)
  const [loading, setLoading] = useState(false)

  // 周/月榜不按成交额排序（样本口径不同，容易误导）
  const effectiveSortKey: RankSort = period !== 'day' && sortKey === 'amount' ? 'pct_desc' : sortKey
  const sortDef = SORTS.find((item) => item.key === effectiveSortKey) ?? SORTS[0]
  const sortField = sortDef.sort
  const sortOrder = sortDef.order

  const load = useCallback(() => {
    setLoading(true)
    marketApi
      .ranking(market, sortField, sortOrder, 20, period)
      .then((result) => setData(result))
      .catch(() =>
        // 刷新失败保留上次榜单，只更新提示
        setData((prev) =>
          prev
            ? { ...prev, note: '刷新失败，展示上次数据' }
            : {
                market,
                sort: sortField,
                order: sortOrder,
                period,
                items: [],
                note: '榜单加载失败，请稍后重试',
                ts: '',
              },
        ),
      )
      .finally(() => setLoading(false))
  }, [market, sortField, sortOrder, period])

  usePolling(load, { interval: period === 'day' ? DAY_REFRESH_MS : WINDOW_REFRESH_MS })

  const items = data?.items ?? []

  return (
    <Card
      title="排行榜"
      description="按市场与口径取实时榜单；点击代码查看 K 线，点 + 加入自选"
      actions={
        <Button size="sm" variant="ghost" loading={loading} onClick={load}>
          <IconRefresh />
        </Button>
      }
    >
      <div className="col" style={{ gap: 12 }}>
        <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
          {RANK_MARKETS.map((item) => (
            <button
              key={item.code}
              type="button"
              className={`btn btn-sm${market === item.code ? ' btn-primary' : ''}`}
              onClick={() => setMarket(item.code)}
            >
              {item.label}
            </button>
          ))}
          <span className="faint" style={{ width: 8 }} />
          {PERIODS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`btn btn-sm${period === item.key ? ' btn-primary' : ''}`}
              onClick={() => setPeriod(item.key)}
            >
              {item.label}
            </button>
          ))}
          <span className="faint" style={{ width: 8 }} />
          {SORTS.filter((item) => period === 'day' || item.key !== 'amount').map((item) => (
            <button
              key={item.key}
              type="button"
              className={`btn btn-sm${effectiveSortKey === item.key ? ' btn-primary' : ''}`}
              onClick={() => setSortKey(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>

        {data?.note ? <div className="tiny faint">{data.note}</div> : null}

        {items.length === 0 ? (
          <Empty title={loading ? '加载中…' : '暂无榜单数据'} hint="数据源可能限流或当前市场休市" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>代码</th>
                  <th>名称</th>
                  <th className="right">现价</th>
                  <th className="right">{PERIOD_LABELS[period]}涨跌幅</th>
                  <th className="right">成交额</th>
                  <th className="right">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => {
                  const currency = item.currency
                  const digits = priceDigits(market, item.price)
                  const inWatch = watchlist.includes(item.symbol)
                  return (
                    <tr key={item.symbol} style={{ cursor: 'pointer' }} onClick={() => onSelect(item.symbol)}>
                      <td className="mono strong">{item.symbol.split(':')[1] ?? item.symbol}</td>
                      <td>{item.name || '—'}</td>
                      <td className="right num">{fmtPrice(item.price, currency, digits)}</td>
                      <td className={`right num ${classForSign(item.change_pct)}`}>
                        {pctText(item.change_pct)}
                      </td>
                      <td className="right num tiny">{fmtCompact(item.amount)}</td>
                      <td className="right">
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={inWatch}
                          title={inWatch ? '已在自选' : '加入自选'}
                          onClick={(event) => {
                            event.stopPropagation()
                            onAdd(item.symbol)
                          }}
                        >
                          {inWatch ? '已加' : '+'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  )
}

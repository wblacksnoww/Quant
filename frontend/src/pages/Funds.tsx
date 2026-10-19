/** 基金页：场外基金申赎记账、持仓收益、净值曲线。
 *
 * 场外基金按净值确认份额，不走盘中撮合引擎；持仓由申赎流水聚合而来，
 * 因此这里没有"下单/成交"，只有"记一笔申购/赎回"。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, fundApi } from '../api/client'
import type { FundNav, FundPortfolio, FundTrade, SymbolInfo } from '../api/types'
import { NavChart } from '../components/Charts'
import { Badge, Banner, Button, Card, Empty, Field, Loading, Stat } from '../components/ui'
import { IconRefresh, IconSearch, IconTrash } from '../components/icons'
import {
  classForSign,
  fmtMoney,
  fmtNumber,
  fmtSignedPct,
  marketLabel,
  todayISO,
  yearsAgoISO,
} from '../utils/format'
import { notify } from '../store/toast'

type Side = 'subscribe' | 'redeem'

function sideLabel(side: string): string {
  if (side === 'subscribe') return '申购'
  if (side === 'redeem') return '赎回'
  return side
}

export function FundsPage() {
  const [portfolio, setPortfolio] = useState<FundPortfolio | null>(null)
  const [trades, setTrades] = useState<FundTrade[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 搜索与净值
  const [query, setQuery] = useState('')
  const [suggestions, setSuggestions] = useState<SymbolInfo[]>([])
  const [searching, setSearching] = useState(false)
  const [selected, setSelected] = useState('')
  const [nav, setNav] = useState<FundNav | null>(null)
  const [navLoading, setNavLoading] = useState(false)

  // 申赎表单
  const [side, setSide] = useState<Side>('subscribe')
  const [tradeSymbol, setTradeSymbol] = useState('')
  const [tradeDate, setTradeDate] = useState(todayISO())
  const [amount, setAmount] = useState('10000')
  const [shares, setShares] = useState('')
  const [navInput, setNavInput] = useState('')
  const [feeRate, setFeeRate] = useState('0.0015')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const loadPortfolio = useCallback(async () => {
    try {
      setPortfolio(await fundApi.holdings())
      setTrades(await fundApi.trades())
      setError('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '基金数据加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadPortfolio()
  }, [loadPortfolio])

  const loadNav = useCallback(async (symbol: string) => {
    if (!symbol) return
    setNavLoading(true)
    try {
      setNav(await fundApi.nav(symbol, yearsAgoISO(3), todayISO()))
    } catch (err) {
      setNav(null)
      notify('warning', err instanceof ApiError ? err.message : '净值加载失败')
    } finally {
      setNavLoading(false)
    }
  }, [])

  const search = useCallback(async () => {
    const keyword = query.trim()
    if (!keyword) return
    setSearching(true)
    try {
      setSuggestions(await fundApi.search(keyword, 20))
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '基金搜索失败')
    } finally {
      setSearching(false)
    }
  }, [query])

  const pick = useCallback(
    (item: SymbolInfo) => {
      setSelected(item.symbol)
      setTradeSymbol(item.symbol)
      setSuggestions([])
      setQuery(item.name || item.symbol)
      void loadNav(item.symbol)
    },
    [loadNav],
  )

  const submit = useCallback(
    async (event: FormEvent) => {
      event.preventDefault()
      if (!tradeSymbol.trim()) {
        notify('warning', '请填写基金代码（如 FUND:007005）')
        return
      }
      setSubmitting(true)
      try {
        await fundApi.createTrade({
          symbol: tradeSymbol.trim(),
          side,
          trade_date: tradeDate,
          amount: side === 'subscribe' ? Number(amount) : undefined,
          shares: side === 'redeem' ? Number(shares) : undefined,
          nav: navInput ? Number(navInput) : undefined,
          fee_rate: Number(feeRate) || 0,
          note,
        })
        notify('success', `${sideLabel(side)}已记录`)
        setNote('')
        if (!selected) {
          setSelected(tradeSymbol.trim())
          void loadNav(tradeSymbol.trim())
        }
        await loadPortfolio()
      } catch (err) {
        notify('error', err instanceof ApiError ? err.message : `${sideLabel(side)}失败`)
      } finally {
        setSubmitting(false)
      }
    },
    [tradeSymbol, side, tradeDate, amount, shares, navInput, feeRate, note, selected, loadNav, loadPortfolio],
  )

  const remove = useCallback(
    async (id: number) => {
      if (!window.confirm('删除这笔流水？持仓与收益会按剩余流水重算。')) return
      try {
        await fundApi.removeTrade(id)
        notify('success', '已删除')
        await loadPortfolio()
      } catch (err) {
        notify('error', err instanceof ApiError ? err.message : '删除失败')
      }
    },
    [loadPortfolio],
  )

  const totals = portfolio?.totals ?? {}
  const holdings = useMemo(
    () => (portfolio?.holdings ?? []).filter((item) => item.shares > 0),
    [portfolio],
  )

  if (loading) return <Loading text="正在加载基金持仓…" />

  return (
    <div className="col" style={{ gap: 18 }}>
      {error && <Banner kind="danger">{error}</Banner>}

      <div className="grid grid-4">
        <Stat label="持仓成本" value={fmtMoney(totals.cost)} sub={`${totals.count ?? 0} 只基金`} />
        <Stat label="当前市值" value={fmtMoney(totals.market_value)} sub="按最新净值估算" />
        <Stat
          label="累计收益"
          value={fmtMoney(totals.total_pnl)}
          sub={`已实现 ${fmtMoney(totals.realized_pnl)}`}
          tone={(totals.total_pnl ?? 0) >= 0 ? 'up' : 'down'}
        />
        <Stat
          label="持仓收益率"
          value={fmtSignedPct(totals.return_pct)}
          sub={`未实现 ${fmtMoney(totals.unrealized_pnl)}`}
          tone={(totals.return_pct ?? 0) >= 0 ? 'up' : 'down'}
        />
      </div>

      <div className="grid grid-sidebar">
        <Card
          title={side === 'subscribe' ? '记一笔申购' : '记一笔赎回'}
          description="按确认净值折算份额；净值留空则取申请日或之前最近净值"
        >
          <form className="col" style={{ gap: 14 }} onSubmit={submit}>
            <div className="form-grid">
              <Field label="方向">
                <select
                  className="select"
                  value={side}
                  onChange={(event) => setSide(event.target.value as Side)}
                >
                  <option value="subscribe">申购</option>
                  <option value="redeem">赎回</option>
                </select>
              </Field>
              <Field label="申请日">
                <input
                  className="input"
                  type="date"
                  value={tradeDate}
                  onChange={(event) => setTradeDate(event.target.value)}
                />
              </Field>
            </div>

            <Field label="基金代码" hint="支持搜索后点击结果自动填入，如 FUND:007005">
              <input
                className="input mono"
                value={tradeSymbol}
                onChange={(event) => setTradeSymbol(event.target.value)}
                placeholder="FUND:007005"
              />
            </Field>

            <div className="form-grid">
              {side === 'subscribe' ? (
                <Field label="申购金额（元）">
                  <input
                    className="input mono"
                    type="number"
                    min={0}
                    step={100}
                    value={amount}
                    onChange={(event) => setAmount(event.target.value)}
                  />
                </Field>
              ) : (
                <Field label="赎回份额">
                  <input
                    className="input mono"
                    type="number"
                    min={0}
                    step={1}
                    value={shares}
                    onChange={(event) => setShares(event.target.value)}
                  />
                </Field>
              )}
              <Field label="费率" hint={side === 'subscribe' ? '申购费率，如 0.0015 = 0.15%' : '赎回费率，如 0.005 = 0.5%'}>
                <input
                  className="input mono"
                  type="number"
                  min={0}
                  max={0.5}
                  step={0.0001}
                  value={feeRate}
                  onChange={(event) => setFeeRate(event.target.value)}
                />
              </Field>
            </div>

            <div className="form-grid">
              <Field label="成交净值（可空）" hint="留空自动取最近净值">
                <input
                  className="input mono"
                  type="number"
                  min={0}
                  step={0.0001}
                  value={navInput}
                  onChange={(event) => setNavInput(event.target.value)}
                  placeholder="如 1.0234"
                />
              </Field>
              <Field label="备注">
                <input
                  className="input"
                  value={note}
                  maxLength={255}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder="可选"
                />
              </Field>
            </div>

            <Button type="submit" block loading={submitting}>
              {sideLabel(side)}
            </Button>
          </form>
        </Card>

        <Card
          title="基金搜索"
          description="按代码或简称检索场外基金（数据来自 akshare）"
          actions={<Badge tone="neutral">FUND</Badge>}
        >
          <div className="col" style={{ gap: 10 }}>
            <div className="row" style={{ gap: 8 }}>
              <input
                className="input"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    void search()
                  }
                }}
                placeholder="如 华夏 / 007005"
              />
              <Button size="sm" variant="ghost" loading={searching} onClick={() => void search()}>
                <IconSearch />
              </Button>
            </div>
            {suggestions.length > 0 && (
              <div className="suggest-list">
                {suggestions.map((item) => (
                  <button
                    key={item.symbol}
                    type="button"
                    className="suggest-item"
                    onClick={() => pick(item)}
                  >
                    <span className="mono strong">{item.code ?? item.symbol}</span>
                    <span>{item.name || '—'}</span>
                    <span className="tiny faint" style={{ marginLeft: 'auto' }}>
                      {marketLabel(item.market)} · {item.board}
                    </span>
                  </button>
                ))}
              </div>
            )}
            {selected && (
              <div className="col" style={{ gap: 8 }}>
                <div className="row between">
                  <span className="mono strong">{selected}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    loading={navLoading}
                    onClick={() => void loadNav(selected)}
                  >
                    <IconRefresh />
                  </Button>
                </div>
                <div className="tiny faint">{nav?.name || '—'}</div>
                <NavChart points={nav?.points ?? []} height={220} />
              </div>
            )}
            {!selected && <Empty title="选择一只基金" hint="搜索后点击结果，查看历史单位净值。" />}
          </div>
        </Card>
      </div>

      <Card
        title="基金持仓"
        description={portfolio?.nav_note || '市值按最新净值估算；净值源不可用时按成本计'}
        actions={
          <Button size="sm" variant="ghost" onClick={() => void loadPortfolio()}>
            <IconRefresh />
          </Button>
        }
        flush
      >
        {holdings.length === 0 ? (
          <div style={{ padding: 16 }}>
            <Empty title="暂无持仓" hint="在左侧记一笔申购后，这里会显示份额与收益。" />
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>基金</th>
                  <th>名称</th>
                  <th className="right">份额</th>
                  <th className="right">成本净值</th>
                  <th className="right">最新净值</th>
                  <th className="right">成本</th>
                  <th className="right">市值</th>
                  <th className="right">未实现</th>
                  <th className="right">已实现</th>
                  <th className="right">收益率</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((item) => (
                  <tr key={item.symbol}>
                    <td className="mono strong">{item.symbol.replace('FUND:', '')}</td>
                    <td>{item.name || '—'}</td>
                    <td className="right num">{fmtNumber(item.shares, 2)}</td>
                    <td className="right num">{fmtNumber(item.avg_cost, 4)}</td>
                    <td className="right num">{fmtNumber(item.nav, 4)}</td>
                    <td className="right num">{fmtMoney(item.cost)}</td>
                    <td className="right num">{fmtMoney(item.market_value)}</td>
                    <td className={`right num ${classForSign(item.unrealized_pnl)}`}>
                      {fmtMoney(item.unrealized_pnl)}
                    </td>
                    <td className={`right num ${classForSign(item.realized_pnl)}`}>
                      {fmtMoney(item.realized_pnl)}
                    </td>
                    <td className={`right num ${classForSign(item.return_pct)}`}>
                      {fmtSignedPct(item.return_pct)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="申赎流水" description="删除补录错误的记录后，持仓与收益按剩余流水重算" flush>
        {trades.length === 0 ? (
          <div style={{ padding: 16 }}>
            <Empty title="暂无流水" />
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>申请日</th>
                  <th>基金</th>
                  <th>方向</th>
                  <th className="right">净值</th>
                  <th className="right">份额</th>
                  <th className="right">金额</th>
                  <th className="right">费率</th>
                  <th className="right">费用</th>
                  <th>备注</th>
                  <th className="right">操作</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((item) => (
                  <tr key={item.id}>
                    <td className="tiny">{item.trade_date || '—'}</td>
                    <td className="mono">{item.symbol.replace('FUND:', '')}</td>
                    <td>
                      <Badge tone={item.side === 'subscribe' ? 'accent' : 'warn'}>
                        {sideLabel(item.side)}
                      </Badge>
                    </td>
                    <td className="right num">{fmtNumber(item.nav, 4)}</td>
                    <td className="right num">{fmtNumber(item.shares, 2)}</td>
                    <td className="right num">{fmtMoney(item.amount)}</td>
                    <td className="right num tiny">{fmtNumber(item.fee_rate * 100, 3)}%</td>
                    <td className="right num">{fmtMoney(item.fee)}</td>
                    <td className="tiny faint">{item.note || '—'}</td>
                    <td className="right">
                      <Button size="sm" variant="ghost" onClick={() => void remove(item.id)}>
                        <IconTrash />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

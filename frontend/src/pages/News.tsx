/** 新闻/政策：录入原始条目，查看 Point-in-Time 量化因子。 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, newsApi } from '../api/client'
import type { NewsFactor, NewsFetchStatus, NewsItem } from '../api/types'
import { notify } from '../store/toast'
import { useAuth } from '../store/auth'
import { Badge, Banner, Button, Card, Empty, Field, Loading, Stat } from '../components/ui'
import { IconRefresh, IconTrash } from '../components/icons'
import { fmtDateTime, fmtPct, fmtSignedPct } from '../utils/format'

const EVENT_TYPES = ['policy', 'regulation', 'macro', 'etf', 'security', 'exchange', 'other'] as const

function isoFromLocal(value: string): string | undefined {
  if (!value) return undefined
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString()
}

function nowLocal(): string {
  const date = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function NewsPage() {
  const [symbol, setSymbol] = useState('CRYPTO:BTC/USDT')
  const [symbolInput, setSymbolInput] = useState('CRYPTO:BTC/USDT')
  const [factorWindow, setFactorWindow] = useState('1h')
  const [factor, setFactor] = useState<NewsFactor | null>(null)
  const [items, setItems] = useState<NewsItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [fetching, setFetching] = useState(false)
  const [fetchStatus, setFetchStatus] = useState<NewsFetchStatus | null>(null)
  const isAdmin = useAuth((state) => state.user?.role === 'admin')

  const [title, setTitle] = useState('')
  const [summary, setSummary] = useState('')
  const [symbolsText, setSymbolsText] = useState('CRYPTO:BTC/USDT')
  const [eventType, setEventType] = useState<(typeof EVENT_TYPES)[number]>('policy')
  const [sentiment, setSentiment] = useState(0)
  const [policyRisk, setPolicyRisk] = useState(0)
  const [confidence, setConfidence] = useState(0.7)
  const [publishedAt, setPublishedAt] = useState(nowLocal)
  const [effectiveAt, setEffectiveAt] = useState('')
  const [url, setUrl] = useState('')

  const load = useCallback(async () => {
    if (!symbol.trim()) {
      setFactor(null)
      setItems([])
      setLoading(false)
      setError('请输入标的代码')
      return
    }
    setLoading(true)
    setError('')
    try {
      const [nextFactor, nextItems] = await Promise.all([
        newsApi.factor(symbol, factorWindow),
        newsApi.items(symbol, 100),
      ])
      setFactor(nextFactor)
      setItems(nextItems)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '读取新闻因子失败')
    } finally {
      setLoading(false)
    }
  }, [symbol, factorWindow])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    void newsApi
      .status()
      .then(setFetchStatus)
      .catch(() => setFetchStatus(null))
  }, [])

  const triggerFetch = async () => {
    setFetching(true)
    try {
      const before = fetchStatus?.last_run_at
      await newsApi.fetch()
      for (let i = 0; i < 10; i += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000))
        const next = await newsApi.status()
        setFetchStatus(next)
        if (next.last_run_at && next.last_run_at !== before) break
      }
      await load()
      notify('success', '新闻抓取已完成')
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '抓取失败')
    } finally {
      setFetching(false)
    }
  }

  const applySymbol = () => {
    const next = symbolInput.trim()
    if (!next) {
      setError('请输入标的代码')
      setFactor(null)
      setItems([])
      return
    }
    if (next === symbol) {
      void load()
    } else {
      setSymbol(next)
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!title.trim()) {
      notify('warning', '请填写新闻标题')
      return
    }
    setSubmitting(true)
    try {
      await newsApi.create({
        title: title.trim(),
        summary,
        url,
        market: '',
        symbols: symbolsText
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
        event_type: eventType,
        sentiment,
        policy_risk: policyRisk,
        confidence,
        published_at: isoFromLocal(publishedAt),
        effective_at: isoFromLocal(effectiveAt),
      })
      notify('success', '新闻已录入')
      setTitle('')
      setSummary('')
      setUrl('')
      await load()
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '录入失败')
    } finally {
      setSubmitting(false)
    }
  }

  const remove = async (id: number) => {
    if (!window.confirm('确定删除这条新闻？')) return
    try {
      await newsApi.remove(id)
      notify('success', '已删除')
      await load()
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '删除失败')
    }
  }

  const factorItems = useMemo(() => factor?.items ?? [], [factor])

  return (
    <div className="col" style={{ gap: 18 }}>
      <Card
        title="新闻/政策因子"
        description="Point-in-Time 聚合：策略只能看到 effective_at 之前的数据；无新闻时返回中性值"
        actions={
          <div className="row" style={{ gap: 6 }}>
            <Button size="sm" loading={fetching} onClick={() => void triggerFetch()}>
              立即抓取
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void load()}>
              <IconRefresh />
            </Button>
          </div>
        }
      >
        <div className="row wrap" style={{ gap: 10, alignItems: 'flex-end' }}>
          <Field label="标的" style={{ flex: '1 1 260px', maxWidth: 360 }}>
            <input
              className="input mono"
              value={symbolInput}
              onChange={(e) => setSymbolInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  applySymbol()
                }
              }}
            />
          </Field>
          <Field label="聚合窗口">
            <select className="select" value={factorWindow} onChange={(e) => setFactorWindow(e.target.value)}>
              {['15m', '1h', '4h', '1d'].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </Field>
          <Button variant="primary" onClick={applySymbol} loading={loading}>
            查询因子
          </Button>
        </div>

        {error && <Banner kind="danger">{error}</Banner>}
        <Banner kind={fetchStatus?.enabled ? 'info' : 'warn'}>
          {fetchStatus?.enabled
            ? `自动抓取已开启：每 ${Math.round(fetchStatus.interval_seconds)} 秒；上次 ${
                fetchStatus.last_run_at ? fmtDateTime(fetchStatus.last_run_at) : '尚未运行'
              }`
            : '自动抓取未开启（QUANT_NEWS_AUTO_FETCH=false），仍可手动抓取'}
          {fetchStatus?.last_result
            ? ` · 新增 ${(fetchStatus.last_result as { inserted?: number }).inserted ?? 0} 条 / 跳过 ${
                (fetchStatus.last_result as { skipped?: number }).skipped ?? 0
              } 条`
            : ''}
        </Banner>
        {loading && !factor ? (
          <Loading text="正在计算新闻因子…" />
        ) : (
          <div className="grid grid-4" style={{ marginTop: 14 }}>
            <Stat
              label="情绪"
              value={fmtSignedPct(factor?.sentiment ?? 0)}
              sub="[-1, 1] 加权平均"
              tone={(factor?.sentiment ?? 0) >= 0 ? 'up' : 'down'}
            />
            <Stat
              label="政策风险"
              value={fmtPct(factor?.policy_risk ?? 0)}
              sub="窗口内最大风险"
              tone={(factor?.policy_risk ?? 0) > 0.5 ? 'down' : undefined}
            />
            <Stat label="置信度" value={fmtPct(factor?.confidence ?? 0)} sub="最高条目置信度" />
            <Stat label="事件数" value={factor?.event_count ?? 0} sub={`${factorWindow} 窗口`} />
          </div>
        )}
      </Card>

      <div className="grid grid-2">
        <Card title="录入新闻 / 政策">
          <form className="col" style={{ gap: 12 }} onSubmit={submit}>
            <Field label="标题">
              <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
            </Field>
            <Field label="摘要">
              <textarea
                className="input"
                rows={3}
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
              />
            </Field>
            <div className="form-grid">
              <Field label="影响标的" hint="逗号分隔；ALL 表示全市场">
                <input
                  className="input mono"
                  value={symbolsText}
                  onChange={(e) => setSymbolsText(e.target.value)}
                />
              </Field>
              <Field label="事件类型">
                <select
                  className="select"
                  value={eventType}
                  onChange={(e) => setEventType(e.target.value as (typeof EVENT_TYPES)[number])}
                >
                  {EVENT_TYPES.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="form-grid-3">
              <Field label="情绪 (-1~1)">
                <input
                  className="input mono"
                  type="number"
                  min={-1}
                  max={1}
                  step={0.05}
                  value={sentiment}
                  onChange={(e) => setSentiment(Number(e.target.value))}
                />
              </Field>
              <Field label="政策风险 (0~1)">
                <input
                  className="input mono"
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={policyRisk}
                  onChange={(e) => setPolicyRisk(Number(e.target.value))}
                />
              </Field>
              <Field label="置信度 (0~1)">
                <input
                  className="input mono"
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={confidence}
                  onChange={(e) => setConfidence(Number(e.target.value))}
                />
              </Field>
            </div>
            <div className="form-grid">
              <Field label="发布时间">
                <input
                  className="input"
                  type="datetime-local"
                  value={publishedAt}
                  onChange={(e) => setPublishedAt(e.target.value)}
                />
              </Field>
              <Field label="生效时间" hint="留空 = 发布时间；回测请填历史时间">
                <input
                  className="input"
                  type="datetime-local"
                  value={effectiveAt}
                  onChange={(e) => setEffectiveAt(e.target.value)}
                />
              </Field>
            </div>
            <Field label="原文链接">
              <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} />
            </Field>
            <Button type="submit" variant="primary" loading={submitting}>
              录入
            </Button>
          </form>
        </Card>

        <Card title="因子明细" description="按影响强度排序的前 5 条">
          {factorItems.length === 0 ? (
            <Empty title="窗口内没有新闻" hint="录入一条新闻后，策略即可读取因子。" />
          ) : (
            <div className="col" style={{ gap: 10 }}>
              {factorItems.map((item) => (
                <div key={item.id} className="col" style={{ gap: 4 }}>
                  <div className="row between">
                    <strong>{item.title}</strong>
                    <Badge tone={item.policy_risk > 0.5 ? 'warn' : 'neutral'}>{item.event_type}</Badge>
                  </div>
                  <span className="tiny faint">
                    {fmtDateTime(item.effective_at ?? item.published_at)} · 情绪{' '}
                    {fmtSignedPct(item.sentiment)} · 风险 {fmtPct(item.policy_risk)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card title="新闻列表" description="手动录入的条目；删除后策略不再使用" flush>
        {items.length === 0 ? (
          <Empty title="暂无新闻" hint="在上方录入政策、监管或市场新闻。" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>生效时间</th>
                  <th>标题</th>
                  <th>类型</th>
                  <th>标的</th>
                  <th className="right">情绪</th>
                  <th className="right">政策风险</th>
                  <th className="right">置信度</th>
                  <th>来源</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td className="tiny faint nowrap">{fmtDateTime(item.effective_at)}</td>
                    <td>
                      <div className="strong">{item.title}</div>
                      {item.summary && <div className="tiny faint truncate">{item.summary}</div>}
                    </td>
                    <td className="tiny">{item.event_type}</td>
                    <td className="mono tiny">{(item.symbols ?? []).join(', ')}</td>
                    <td className="right num">{fmtSignedPct(item.sentiment)}</td>
                    <td className="right num">{fmtPct(item.policy_risk)}</td>
                    <td className="right num">{fmtPct(item.confidence)}</td>
                    <td className="tiny faint">{item.source}</td>
                    <td className="right">
                      {(item.created_by != null || isAdmin) && (
                        <Button size="sm" variant="ghost" onClick={() => void remove(item.id)}>
                          <IconTrash />
                        </Button>
                      )}
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

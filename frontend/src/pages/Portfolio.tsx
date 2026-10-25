/** 组合：权重优化 + 指数成分股（point-in-time）。
 *
 * 两块内容服务同一个问题 ——"该买哪些、各买多少"。成分股回答"哪些"，
 * 且用的是**当时**的名单（否则回测会只挑活到今天的幸存者）；
 * 权重优化回答"多少"。
 */

import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, portfolioApi } from '../api/client'
import type {
  IndexMember,
  IndexSummary,
  PortfolioMethod,
  PortfolioMethodOption,
  PortfolioOptimizeResult,
} from '../api/types'
import { Banner, Button, Card, Empty, Field, Loading, Stat } from '../components/ui'
import { notify } from '../store/toast'
import { fmtNumber, fmtSignedPct, parseSymbols, todayISO, yearsAgoISO } from '../utils/format'

const METHOD_FALLBACK: PortfolioMethodOption[] = [
  { key: 'risk_parity', label: '风险平价' },
  { key: 'equal_weight', label: '等权' },
]

export function PortfolioPage() {
  const [methods, setMethods] = useState<PortfolioMethodOption[]>(METHOD_FALLBACK)
  const [method, setMethod] = useState<PortfolioMethod>('risk_parity')
  const [symbolsText, setSymbolsText] = useState('CN:600519, CN:000001')
  const [start, setStart] = useState(yearsAgoISO(2))
  const [end, setEnd] = useState(todayISO())
  const [maxWeight, setMaxWeight] = useState(1)
  const [result, setResult] = useState<PortfolioOptimizeResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const [indexes, setIndexes] = useState<IndexSummary[]>([])
  const [indexCode, setIndexCode] = useState('')
  const [asOf, setAsOf] = useState(todayISO())
  const [members, setMembers] = useState<IndexMember[] | null>(null)
  const [importText, setImportText] = useState('')
  const [importAsOf, setImportAsOf] = useState(todayISO())
  const [indexBusy, setIndexBusy] = useState(false)

  const loadIndexes = useCallback(() => {
    return portfolioApi
      .indexes()
      .then(setIndexes)
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    void portfolioApi
      .methods()
      .then(setMethods)
      .catch(() => undefined)
    void loadIndexes()
  }, [loadIndexes])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    const symbols = parseSymbols(symbolsText)
    if (symbols.length < 2) {
      setError('至少需要两个标的才能构成组合')
      return
    }
    setBusy(true)
    try {
      setResult(
        await portfolioApi.optimize({
          symbols,
          method,
          start,
          end,
          max_weight: maxWeight,
        }),
      )
    } catch (err) {
      const message = err instanceof ApiError ? err.message : '计算失败'
      setError(message)
      notify('error', message)
    } finally {
      setBusy(false)
    }
  }

  const queryMembers = async () => {
    if (!indexCode.trim()) {
      notify('error', '请先填写指数代码')
      return
    }
    try {
      setMembers(await portfolioApi.members(indexCode.trim(), asOf))
    } catch (err) {
      setMembers([])
      notify('error', err instanceof ApiError ? err.message : '查询失败')
    }
  }

  const importSnapshot = async (event: FormEvent) => {
    event.preventDefault()
    if (!indexCode.trim()) {
      notify('error', '请先填写指数代码')
      return
    }
    const symbols = parseSymbols(importText)
    if (symbols.length === 0) {
      notify('error', '请粘贴成分股列表')
      return
    }
    setIndexBusy(true)
    try {
      const stats = await portfolioApi.importSnapshot(indexCode.trim(), symbols, importAsOf)
      notify(
        'success',
        `已导入：新增 ${stats.added}、调出 ${stats.removed}、保留 ${stats.kept}，当前 ${stats.active} 只`,
      )
      setImportText('')
      await loadIndexes()
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '导入失败')
    } finally {
      setIndexBusy(false)
    }
  }

  const weights = result ? Object.entries(result.weights).sort((a, b) => b[1] - a[1]) : []
  const heaviest = weights.length > 0 ? weights[0][1] : 1

  return (
    <div className="col" style={{ gap: 18 }}>
      <Card
        title="组合权重"
        description="用历史收益率估计风险结构，给出各标的的资金占比。权重之和恒为 1，且不超过单标的上限。"
      >
        {error && <Banner kind="danger">{error}</Banner>}
        <form className="col" style={{ gap: 14 }} onSubmit={submit}>
          <Field label="标的（逗号分隔，至少两个）">
            <input
              className="input mono"
              value={symbolsText}
              onChange={(event) => setSymbolsText(event.target.value)}
              placeholder="CN:600519, CN:000001, CN:600036"
            />
          </Field>
          <div className="form-grid">
            <Field label="方法" hint={methods.find((m) => m.key === method)?.label}>
              <select
                className="select"
                value={method}
                onChange={(event) => setMethod(event.target.value as PortfolioMethod)}
              >
                {methods.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="单标的上限" hint="0.2 表示任一标的不超过 20%">
              <input
                className="input mono"
                type="number"
                min={0.05}
                max={1}
                step={0.05}
                value={maxWeight}
                onChange={(event) => setMaxWeight(Number(event.target.value))}
              />
            </Field>
          </div>
          <div className="form-grid">
            <Field label="开始日期">
              <input
                className="input mono"
                type="date"
                value={start}
                onChange={(event) => setStart(event.target.value)}
              />
            </Field>
            <Field label="结束日期">
              <input
                className="input mono"
                type="date"
                value={end}
                onChange={(event) => setEnd(event.target.value)}
              />
            </Field>
          </div>
          <Button type="submit" variant="primary" loading={busy}>
            计算权重
          </Button>
        </form>
      </Card>

      {result && (
        <Card
          title={`权重结果 · ${result.method_label}`}
          description={
            result.price_start ? `样本区间 ${result.price_start} ~ ${result.price_end}` : '基于所选区间'
          }
        >
          <div className="grid grid-4">
            <Stat
              label="预期年化收益"
              value={fmtSignedPct(result.expected_return)}
              tone={result.expected_return >= 0 ? 'up' : 'down'}
            />
            <Stat label="组合波动率" value={fmtSignedPct(result.volatility)} sub="年化" />
            <Stat label="夏普" value={fmtNumber(result.sharpe, 2)} />
            <Stat
              label="分散化比率"
              value={fmtNumber(result.diversification, 2)}
              sub={`有效持仓 ${fmtNumber(result.diagnostics.effective_holdings ?? 0, 1)} 只`}
            />
          </div>
          {result.note && (
            <div style={{ marginTop: 12 }}>
              <Banner kind="warn">{result.note}</Banner>
            </div>
          )}
          <div className="col" style={{ gap: 8, marginTop: 14 }}>
            {weights.map(([symbol, weight]) => (
              <div key={symbol} className="row" style={{ gap: 10, alignItems: 'center' }}>
                <span className="mono tiny" style={{ width: 130 }}>
                  {symbol}
                </span>
                <div
                  style={{
                    flex: 1,
                    height: 8,
                    borderRadius: 4,
                    background: 'var(--border)',
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      width: `${(weight / Math.max(heaviest, 0.0001)) * 100}%`,
                      height: '100%',
                      background: 'var(--accent)',
                    }}
                  />
                </div>
                <span className="mono tiny" style={{ width: 56, textAlign: 'right' }}>
                  {(weight * 100).toFixed(1)}%
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card
        title="指数成分股（历史）"
        description="按快照导入成分名单。两次快照的差集会自动拼成「进出区间」，回测才能问出某一天的真实成分。"
      >
        {indexes.length > 0 ? (
          <div className="table-wrap" style={{ maxHeight: 200, marginBottom: 14 }}>
            <table>
              <thead>
                <tr>
                  <th>指数</th>
                  <th>记录数</th>
                  <th>在册</th>
                  <th>最早快照</th>
                  <th>来源</th>
                </tr>
              </thead>
              <tbody>
                {indexes.map((item) => (
                  <tr key={item.index_code}>
                    <td className="mono">{item.index_code}</td>
                    <td>{item.entries}</td>
                    <td>{item.active}</td>
                    <td className="tiny">{item.earliest ?? '—'}</td>
                    <td className="tiny">{item.sources.join(' / ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div style={{ marginBottom: 14 }}>
            <Banner kind="info">还没有导入任何成分股。导入后，用「当时的名单」回测才不会高估收益。</Banner>
          </div>
        )}

        <div className="form-grid">
          <Field label="指数代码" hint="如 MARKET:000300">
            <input
              className="input mono"
              value={indexCode}
              onChange={(event) => setIndexCode(event.target.value)}
              placeholder="MARKET:000300"
            />
          </Field>
          <Field label="查询日期">
            <div className="row" style={{ gap: 8 }}>
              <input
                className="input mono"
                type="date"
                value={asOf}
                onChange={(event) => setAsOf(event.target.value)}
              />
              <Button type="button" size="sm" variant="ghost" onClick={() => void queryMembers()}>
                查询
              </Button>
            </div>
          </Field>
        </div>

        {members && (
          <div style={{ marginTop: 12 }}>
            {members.length === 0 ? (
              <Empty title="该日期没有成分记录" hint="可能是还没导入这个指数的快照" />
            ) : (
              <div className="table-wrap" style={{ maxHeight: 260 }}>
                <table>
                  <thead>
                    <tr>
                      <th>标的</th>
                      <th>纳入</th>
                      <th>调出</th>
                      <th>来源</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((item) => (
                      <tr key={item.symbol}>
                        <td className="mono">{item.symbol}</td>
                        <td className="tiny">{item.start_date ?? '—'}</td>
                        <td className="tiny">{item.end_date ?? '在册'}</td>
                        <td className="tiny">{item.source}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        <form className="col" style={{ gap: 10, marginTop: 16 }} onSubmit={importSnapshot}>
          <Field label="导入快照" hint="粘贴该日全部成分股（逗号或换行分隔）。重复导入同一天是幂等的。">
            <textarea
              className="input mono"
              rows={4}
              value={importText}
              onChange={(event) => setImportText(event.target.value)}
              placeholder="CN:600519, CN:000001, CN:600036"
              spellCheck={false}
            />
          </Field>
          <div className="row" style={{ gap: 10, alignItems: 'flex-end' }}>
            <Field label="快照日期">
              <input
                className="input mono"
                type="date"
                value={importAsOf}
                onChange={(event) => setImportAsOf(event.target.value)}
              />
            </Field>
            <Button type="submit" variant="primary" size="sm" loading={indexBusy}>
              导入
            </Button>
          </div>
        </form>
      </Card>

      {busy && <Loading text="正在估计协方差并求解权重…" />}
      {result && (
        <div className="tiny faint">
          观测样本 {result.diagnostics.observations ?? '—'} 个交易日 · 标的{' '}
          {result.diagnostics.symbols ?? weights.length} 只
        </div>
      )}
    </div>
  )
}

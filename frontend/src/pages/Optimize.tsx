/** 参数寻优：网格/随机搜索 + walk-forward 滚动验证。
 *
 * 这个页面存在的理由：单次回测的最优参数几乎必然是过拟合的。真正能判断
 * 一组参数能不能上实盘，要看它在**没参与选择**的样本外区间表现如何 ——
 * 也就是 walk-forward。所以默认就开 walk-forward，并把 WFE 放在最显眼的位置。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, optimizeApi, strategyApi } from '../api/client'
import type {
  OptimizeJob,
  ParamCandidates,
  OptimizeMode,
  OptimizeObjective,
  OptimizeObjectiveOption,
  OptimizeStartRequest,
  Strategy,
} from '../api/types'
import { Badge, Banner, Button, Card, Empty, Field, Progress, Stat } from '../components/ui'
import { usePolling } from '../hooks/usePolling'
import { notify } from '../store/toast'
import { fmtNumber, fmtSignedPct, parseSymbols, todayISO, yearsAgoISO } from '../utils/format'

const OBJECTIVE_FALLBACK: OptimizeObjectiveOption[] = [
  { key: 'sharpe', label: '夏普比率' },
  { key: 'calmar', label: '卡玛比率' },
  { key: 'annual_return', label: '年化收益' },
]

const DEFAULT_SPACE = `{
  "fast": { "start": 5, "stop": 30, "step": 5 },
  "slow": { "start": 30, "stop": 90, "step": 15 }
}`

function statusTone(status: string): 'success' | 'danger' | 'warn' | 'neutral' {
  if (status === 'finished' || status === 'ok') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'skipped') return 'warn'
  return 'neutral'
}

function pct(value: unknown): string {
  const num = Number(value)
  return Number.isFinite(num) ? fmtSignedPct(num) : '—'
}

function num(value: unknown, digits = 2): string {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? fmtNumber(parsed, digits) : '—'
}

export function OptimizePage() {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [objectives, setObjectives] = useState<OptimizeObjectiveOption[]>(OBJECTIVE_FALLBACK)

  const [strategyId, setStrategyId] = useState('dual_ma')
  const [symbolsText, setSymbolsText] = useState('CN:600519')
  const [start, setStart] = useState(yearsAgoISO(3))
  const [end, setEnd] = useState(todayISO())
  const [spaceText, setSpaceText] = useState(DEFAULT_SPACE)
  const [objective, setObjective] = useState<OptimizeObjective>('sharpe')
  const [minTrades, setMinTrades] = useState(5)
  const [walkForward, setWalkForward] = useState(true)
  const [trainDays, setTrainDays] = useState(365)
  const [testDays, setTestDays] = useState(90)

  const [job, setJob] = useState<OptimizeJob | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    void strategyApi
      .list()
      .then(setStrategies)
      .catch(() => undefined)
    void optimizeApi
      .objectives()
      .then(setObjectives)
      .catch(() => undefined)
  }, [])

  const running = job?.status === 'queued' || job?.status === 'running'

  const refresh = useCallback((jobId: string) => {
    return optimizeApi
      .get(jobId)
      .then(setJob)
      .catch(() => undefined)
  }, [])

  usePolling(() => (job ? refresh(job.job_id) : Promise.resolve()), {
    interval: 1200,
    enabled: running,
    immediate: false,
  })

  const current = strategies.find((item) => item.id === strategyId)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')

    let space: Record<string, ParamCandidates>
    try {
      space = JSON.parse(spaceText) as Record<string, ParamCandidates>
    } catch {
      setError('参数空间不是合法 JSON')
      return
    }
    const symbols = parseSymbols(symbolsText)
    if (symbols.length === 0) {
      setError('请至少填写一个标的')
      return
    }

    const body: OptimizeStartRequest = {
      strategy_id: strategyId,
      symbols,
      start,
      end,
      space,
      objective,
      min_trades: minTrades,
      walk_forward: walkForward,
      walk_forward_options: walkForward ? { train_days: trainDays, test_days: testDays } : undefined,
    }

    setSubmitting(true)
    try {
      const summary = await optimizeApi.submit(body)
      setJob({ ...summary, summary: {}, best: null, trials: [], folds: [], total_trials: 0 })
      notify('info', '寻优已启动')
    } catch (err) {
      const message = err instanceof ApiError ? err.message : '提交失败'
      setError(message)
      notify('error', message)
    } finally {
      setSubmitting(false)
    }
  }

  const cancel = async () => {
    if (!job) return
    try {
      await optimizeApi.cancel(job.job_id)
      notify('info', '已请求取消')
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '取消失败')
    }
  }

  const summary = job?.summary ?? {}
  const trials = job?.trials ?? []
  const folds = job?.folds ?? []
  const wfe = summary.wfe

  const modeLabel: Record<OptimizeMode, string> = useMemo(
    () => ({ search: '全区间搜索', walk_forward: 'Walk-forward 滚动验证' }),
    [],
  )

  return (
    <div className="col" style={{ gap: 18 }}>
      <Card
        title="参数寻优"
        description="在参数空间里自动搜索，并按「样本外」而不是「样本内」给结论。默认走 walk-forward：训练段选参、测试段验证。"
      >
        {error && <Banner kind="danger">{error}</Banner>}
        <form className="col" style={{ gap: 14 }} onSubmit={submit}>
          <div className="form-grid">
            <Field label="策略" hint={current?.description}>
              <select
                className="select"
                value={strategyId}
                onChange={(event) => setStrategyId(event.target.value)}
              >
                {strategies.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="标的（逗号分隔）">
              <input
                className="input mono"
                value={symbolsText}
                onChange={(event) => setSymbolsText(event.target.value)}
                placeholder="CN:600519"
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

          <Field
            label="参数空间（JSON）"
            hint='枚举写 ["a","b"]；等差写 {"start":1,"stop":10,"step":2}；固定值直接写 0.95'
          >
            <textarea
              className="input mono"
              rows={7}
              value={spaceText}
              onChange={(event) => setSpaceText(event.target.value)}
              spellCheck={false}
            />
          </Field>

          <div className="form-grid">
            <Field label="优化目标">
              <select
                className="select"
                value={objective}
                onChange={(event) => setObjective(event.target.value as OptimizeObjective)}
              >
                {objectives.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="最少成交次数"
              hint="低于该次数的组合不参与排名：否则会选出「几乎不交易却侥幸赚钱」的伪最优"
            >
              <input
                className="input mono"
                type="number"
                min={0}
                value={minTrades}
                onChange={(event) => setMinTrades(Number(event.target.value))}
              />
            </Field>
          </div>

          <Field label="验证方式">
            <label className="row" style={{ gap: 8, paddingTop: 6 }}>
              <input
                type="checkbox"
                checked={walkForward}
                onChange={(event) => setWalkForward(event.target.checked)}
              />
              <span>
                Walk-forward（训练段选参 → 紧接着的测试段验证）
                {walkForward ? '' : '　关闭后只做全区间搜索，结果不可直接用于实盘'}
              </span>
            </label>
          </Field>

          {walkForward && (
            <div className="form-grid">
              <Field label="训练段（自然日）">
                <input
                  className="input mono"
                  type="number"
                  min={30}
                  value={trainDays}
                  onChange={(event) => setTrainDays(Number(event.target.value))}
                />
              </Field>
              <Field label="测试段（自然日）">
                <input
                  className="input mono"
                  type="number"
                  min={10}
                  value={testDays}
                  onChange={(event) => setTestDays(Number(event.target.value))}
                />
              </Field>
            </div>
          )}

          <div className="row" style={{ gap: 10 }}>
            <Button type="submit" variant="primary" loading={submitting} disabled={running}>
              开始寻优
            </Button>
            {running && (
              <Button type="button" variant="danger" size="sm" onClick={() => void cancel()}>
                取消
              </Button>
            )}
          </div>
        </form>
      </Card>

      {job && (
        <Card
          title="任务状态"
          description={`${modeLabel[job.mode]} · ${job.job_id}`}
          actions={<Badge tone={statusTone(job.status)}>{job.status}</Badge>}
        >
          <div className="col" style={{ gap: 10 }}>
            <Progress value={job.progress} />
            <div className="tiny faint">
              {job.message} · {Math.round(job.progress * 100)}%
              {job.total_trials > 0 ? ` · 共 ${job.total_trials} 组参数` : ''}
            </div>
            {job.error && <Banner kind="danger">{job.error}</Banner>}
          </div>
        </Card>
      )}

      {job?.status === 'finished' && (
        <Card title="结论" description="先看 WFE 与参数稳定性，再看收益。样本内好看、样本外打折是常态。">
          <div className="grid grid-4">
            <Stat
              label="样本外收益"
              value={pct(summary.oos_return)}
              sub="各折测试段复利拼接"
              tone={Number(summary.oos_return) >= 0 ? 'up' : 'down'}
            />
            <Stat label="样本内收益" value={pct(summary.is_return)} sub="同期训练段" />
            <Stat
              label="WFE"
              value={wfe === null || wfe === undefined ? '—' : num(wfe, 2)}
              sub="样本外年化 / 样本内年化"
              tone={typeof wfe === 'number' && wfe >= 0.5 ? 'up' : 'down'}
            />
            <Stat
              label="参数稳定"
              value={summary.params_stable ? '是' : '否'}
              sub={summary.params_stable ? '各折选出同一组参数' : '各折参数不一致，过拟合风险高'}
              tone={summary.params_stable ? 'up' : 'down'}
            />
          </div>
          {typeof summary.wfe_hint === 'string' && summary.wfe_hint && (
            <div style={{ marginTop: 12 }}>
              <Banner kind="warn">{summary.wfe_hint}</Banner>
            </div>
          )}
          {job.best && (
            <div style={{ marginTop: 14 }}>
              <div className="tiny faint" style={{ marginBottom: 6 }}>
                训练段最优参数
              </div>
              <pre className="mono tiny" style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                {JSON.stringify(job.best.params, null, 2)}
              </pre>
            </div>
          )}
        </Card>
      )}

      {folds.length > 0 && (
        <Card title="各折明细" description="每一折的最优参数与训练 / 测试表现" flush>
          <div className="table-wrap" style={{ maxHeight: 380 }}>
            <table>
              <thead>
                <tr>
                  <th>折</th>
                  <th>训练段</th>
                  <th>测试段</th>
                  <th>训练收益</th>
                  <th>测试收益</th>
                  <th>测试夏普</th>
                  <th>成交</th>
                  <th>选出的参数</th>
                </tr>
              </thead>
              <tbody>
                {folds.map((item) => (
                  <tr key={item.fold.index}>
                    <td>{item.fold.index}</td>
                    <td className="tiny mono">
                      {item.fold.train_start} → {item.fold.train_end}
                    </td>
                    <td className="tiny mono">
                      {item.fold.test_start} → {item.fold.test_end}
                    </td>
                    <td className={Number(item.train_metrics.total_return) >= 0 ? 'up' : 'down'}>
                      {pct(item.train_metrics.total_return)}
                    </td>
                    <td className={Number(item.test_metrics.total_return) >= 0 ? 'up' : 'down'}>
                      {pct(item.test_metrics.total_return)}
                    </td>
                    <td>{num(item.test_metrics.sharpe)}</td>
                    <td>{num(item.test_metrics.trade_count, 0)}</td>
                    <td className="tiny mono">{JSON.stringify(item.best_params)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {trials.length > 0 && (
        <Card
          title="参数排名"
          description={`按 ${
            objectives.find((item) => item.key === objective)?.label ?? objective
          } 排序，最多显示前 ${trials.length} 组`}
          flush
        >
          <div className="table-wrap" style={{ maxHeight: 460 }}>
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>参数</th>
                  <th>目标值</th>
                  <th>总收益</th>
                  <th>年化</th>
                  <th>最大回撤</th>
                  <th>成交</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {trials.map((trial) => (
                  <tr key={`${trial.index}-${trial.run_id}`}>
                    <td>{trial.index}</td>
                    <td className="tiny mono">{JSON.stringify(trial.params)}</td>
                    <td>{num(trial.metrics.objective, 3)}</td>
                    <td className={Number(trial.metrics.total_return) >= 0 ? 'up' : 'down'}>
                      {pct(trial.metrics.total_return)}
                    </td>
                    <td>{pct(trial.metrics.annual_return)}</td>
                    <td className="down">{pct(trial.metrics.max_drawdown)}</td>
                    <td>{num(trial.metrics.trade_count, 0)}</td>
                    <td>
                      <Badge tone={statusTone(trial.status)}>{trial.status}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {!job && (
        <Empty
          title="还没有寻优记录"
          hint="填好策略与参数空间后点「开始寻优」。建议区间至少 3 年，walk-forward 才能切出足够多的折。"
        />
      )}
    </div>
  )
}

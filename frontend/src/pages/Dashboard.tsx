/** 仪表盘：系统健康、最近回测、模拟盘与数据缓存的总览。 */

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { backtestApi, marketApi, paperApi, systemApi } from '../api/client'
import type { DataCacheItem, Health, PaperState, RunSummary, SystemInfo } from '../api/types'
import { Badge, Banner, Button, Card, Empty, Loading, Stat, StatusBadge } from '../components/ui'
import { IconFlask, IconPlay, IconRefresh, IconShield } from '../components/icons'
import { fmtDateTime, fmtMoney, fmtPct, fmtSignedPct } from '../utils/format'

function uptimeText(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days} 天 ${hours} 小时`
  if (hours > 0) return `${hours} 小时 ${minutes} 分`
  return `${minutes} 分`
}

export function DashboardPage() {
  const [health, setHealth] = useState<Health | null>(null)
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [cache, setCache] = useState<DataCacheItem[]>([])
  const [paper, setPaper] = useState<PaperState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setError('')
    const [healthResult, infoResult, runsResult, cacheResult, paperResult] = await Promise.allSettled([
      systemApi.health(),
      systemApi.info(),
      backtestApi.list(8),
      marketApi.cache(),
      paperApi.state(),
    ])
    if (healthResult.status === 'fulfilled') setHealth(healthResult.value)
    if (infoResult.status === 'fulfilled') setInfo(infoResult.value)
    if (runsResult.status === 'fulfilled') setRuns(runsResult.value)
    if (cacheResult.status === 'fulfilled') setCache(cacheResult.value)
    if (paperResult.status === 'fulfilled') setPaper(paperResult.value)
    const failures = [healthResult, infoResult, runsResult].filter((item) => item.status === 'rejected')
    if (failures.length > 0) setError('部分数据加载失败，请检查后端服务')
    setLoading(false)
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => {
      void paperApi.state().then(setPaper).catch(() => undefined)
    }, 8000)
    return () => window.clearInterval(timer)
  }, [load])

  const runningCount = runs.filter((run) => run.status === 'running' || run.status === 'pending').length
  const finished = runs.filter((run) => run.status === 'finished' && run.metrics)
  const best = finished.reduce<RunSummary | null>((acc, run) => {
    const sharpe = Number(run.metrics.sharpe ?? -Infinity)
    const bestSharpe = acc ? Number(acc.metrics.sharpe ?? -Infinity) : -Infinity
    return sharpe > bestSharpe ? run : acc
  }, null)
  const cachedRows = cache.reduce((sum, item) => sum + item.rows, 0)

  if (loading) {
    return <Loading text="正在加载仪表盘…" />
  }

  return (
    <div className="col" style={{ gap: 18 }}>
      {error && <Banner kind="warn">{error}</Banner>}

      <div className="grid grid-4">
        <Stat
          label="系统状态"
          value={
            <span className={`row ${health?.status === 'ok' ? 'down' : 'warn'}`} style={{ gap: 8 }}>
              <span className="dot" style={{ width: 9, height: 9 }} />
              {health?.status === 'ok' ? '正常' : '异常'}
            </span>
          }
          sub={`${health?.env ?? info?.env ?? '—'} · v${health?.version ?? info?.version ?? '—'}`}
        />
        <Stat
          label="运行时长"
          value={health ? uptimeText(health.uptime_seconds) : '—'}
          sub={health?.database ? '数据库连接正常' : '数据库不可用'}
          tone={health?.database ? undefined : 'warn'}
        />
        <Stat
          label="行情缓存"
          value={`${cache.length} 只`}
          sub={`${cachedRows.toLocaleString('zh-CN')} 根 K 线`}
        />
        <Stat
          label="回测任务"
          value={runningCount > 0 ? `${runningCount} 运行中` : '空闲'}
          sub={
            best && best.metrics.total_return !== undefined
              ? `最佳夏普 ${Number(best.metrics.sharpe ?? 0).toFixed(2)} · ${fmtSignedPct(best.metrics.total_return)}`
              : `${runs.length} 条近期记录`
          }
          tone={runningCount > 0 ? 'accent' : undefined}
        />
      </div>

      <div className="grid grid-sidebar">
        <div className="col" style={{ gap: 18 }}>
          <Card
            title="最近回测"
            description="点击任意记录打开完整结果"
            actions={
              <>
                <Button size="sm" variant="ghost" onClick={() => void load()}>
                  <IconRefresh /> 刷新
                </Button>
                <Link to="/backtest" className="btn btn-sm btn-primary">
                  <IconFlask /> 新建回测
                </Link>
              </>
            }
            flush
          >
            {runs.length === 0 ? (
              <Empty
                title="还没有回测记录"
                hint="从策略库选一个策略，提交第一次回测。"
                action={
                  <Link to="/backtest" className="btn btn-primary btn-sm">
                    开始回测
                  </Link>
                }
              />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>策略</th>
                      <th>标的</th>
                      <th>状态</th>
                      <th className="right">收益率</th>
                      <th className="right">夏普</th>
                      <th className="right">最大回撤</th>
                      <th>提交时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((run) => (
                      <tr key={run.run_id}>
                        <td className="strong">
                          <Link to={`/backtest?run=${run.run_id}`} className="truncate" style={{ display: 'block', maxWidth: 170 }}>
                            {run.strategy_id}
                          </Link>
                        </td>
                        <td className="mono tiny">{run.symbols.join(' · ')}</td>
                        <td><StatusBadge status={run.status} /></td>
                        <td className={`right ${Number(run.metrics?.total_return ?? 0) >= 0 ? 'up' : 'down'} num`}>
                          {run.status === 'finished' ? fmtSignedPct(run.metrics?.total_return) : '—'}
                        </td>
                        <td className="right num">
                          {run.status === 'finished' ? Number(run.metrics?.sharpe ?? 0).toFixed(2) : '—'}
                        </td>
                        <td className="right num down">
                          {run.status === 'finished' ? fmtPct(run.metrics?.max_drawdown) : '—'}
                        </td>
                        <td className="tiny faint nowrap">{fmtDateTime(run.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card
            title="行情缓存"
            description="本地 parquet 缓存，回测优先读取，避免重复拉取"
            actions={
              <Link to="/market" className="btn btn-sm btn-ghost">
                打开行情
              </Link>
            }
            flush
          >
            {cache.length === 0 ? (
              <Empty title="缓存为空" hint="在行情页加载并刷新任意标的即可生成缓存。" />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>代码</th>
                      <th className="right">行数</th>
                      <th>起始</th>
                      <th>结束</th>
                      <th className="right">大小</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cache.map((item) => (
                      <tr key={item.symbol}>
                        <td className="strong mono">{item.symbol}</td>
                        <td className="right num">{item.rows.toLocaleString('zh-CN')}</td>
                        <td className="tiny faint">{item.start ?? '—'}</td>
                        <td className="tiny faint">{item.end ?? '—'}</td>
                        <td className="right num tiny">{item.size_kb.toFixed(1)} KB</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>

        <div className="col" style={{ gap: 18 }}>
          <Card
            title="模拟盘"
            actions={<Badge tone={paper?.status === 'running' ? 'success' : 'neutral'}>{paper?.status === 'running' ? '运行中' : '未运行'}</Badge>}
          >
            {paper ? (
              <div className="col" style={{ gap: 12 }}>
                <div>
                  <div className="faint tiny">总权益</div>
                  <div className="stat-value" style={{ fontSize: 28 }}>{fmtMoney(paper.equity)}</div>
                  <div className={`small num ${paper.total_return >= 0 ? 'up' : 'down'}`}>
                    {fmtSignedPct(paper.total_return)} · {paper.steps} 步
                  </div>
                </div>
                <div className="row wrap" style={{ gap: 6 }}>
                  <Badge tone="accent">{paper.strategy_id}</Badge>
                  <Badge>{paper.mode === 'replay' ? '回放' : '实时'}</Badge>
                  <Badge>{paper.symbols.length} 标的</Badge>
                </div>
                <Link to="/paper" className="btn btn-sm btn-block">
                  <IconPlay /> 查看模拟盘
                </Link>
              </div>
            ) : (
              <div className="col" style={{ gap: 12 }}>
                <div className="muted small">当前没有运行中的模拟盘。回放模式可在历史行情上按时间推进，验证策略的实时行为。</div>
                <Link to="/paper" className="btn btn-sm btn-block">
                  <IconPlay /> 启动模拟盘
                </Link>
              </div>
            )}
          </Card>

          <Card title="系统与安全" description="加密与认证状态">
            {info ? (
              <div className="col" style={{ gap: 12 }}>
                <dl className="kv">
                  <dt>运行环境</dt>
                  <dd>{info.env}</dd>
                  <dt>数据库</dt>
                  <dd>{info.database}</dd>
                  <dt>主数据源</dt>
                  <dd>{info.data_provider}</dd>
                  <dt>字段加密</dt>
                  <dd>{String(info.encryption.field_cipher ?? '—')}</dd>
                  <dt>密钥来源</dt>
                  <dd>{String(info.encryption.key_source ?? '—')}</dd>
                  <dt>密码哈希</dt>
                  <dd>{String(info.encryption.password_hash ?? '—')}</dd>
                  <dt>审计链</dt>
                  <dd>{String(info.encryption.audit_chain ?? '—')}</dd>
                </dl>
                {info.warnings.length > 0 && (
                  <Banner kind="warn">
                    {info.warnings.map((warning) => (
                      <div key={warning}>{warning}</div>
                    ))}
                  </Banner>
                )}
                <Link to="/settings" className="btn btn-sm btn-ghost">
                  <IconShield /> 安全设置
                </Link>
              </div>
            ) : (
              <Empty title="系统信息不可用" hint="后端可能未就绪。" />
            )}
          </Card>

          <Card title="快捷操作">
            <div className="col" style={{ gap: 8 }}>
              <Link to="/backtest" className="btn btn-sm btn-block">
                <IconFlask /> 新建回测
              </Link>
              <Link to="/market" className="btn btn-sm btn-block">
                查看行情
              </Link>
              <Link to="/strategies" className="btn btn-sm btn-block">
                浏览策略库
              </Link>
            </div>
            <div className="divider" />
            <div className="tiny faint">
              最近数据：{cache[0] ? `${cache[0].symbol} ${fmtDateTime(cache[0].end)}` : '无'}
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}

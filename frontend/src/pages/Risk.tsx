/** 风控页：规则目录 + 三套预设的阈值对比。 */

import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { riskApi } from '../api/client'
import type { RiskPresetRule, RiskPresets, RiskRule } from '../api/types'
import { Badge, Button, Card, Empty, Loading } from '../components/ui'
import { IconFlask, IconShield } from '../components/icons'
import { fmtPct } from '../utils/format'

const PRESET_LABEL: Record<string, string> = {
  conservative: '保守',
  balanced: '均衡',
  aggressive: '激进',
}

function formatValue(ruleId: string, key: string, value: unknown): string {
  if (typeof value === 'boolean') return value ? '启用' : '关闭'
  if (Array.isArray(value)) return value.length === 0 ? '空' : value.join(', ')
  if (typeof value === 'number') {
    if (
      key === 'limit' &&
      (ruleId.includes('pct') || ruleId.includes('loss') || ruleId.includes('drawdown'))
    ) {
      return fmtPct(value, 1)
    }
    return Number.isInteger(value) ? String(value) : value.toFixed(2)
  }
  return String(value)
}

function presetSummary(rule: RiskPresetRule): string {
  const parts: string[] = []
  if (rule.limit !== undefined) parts.push(`阈值 ${formatValue(rule.id, 'limit', rule.limit)}`)
  if (rule.max_orders !== undefined) parts.push(`${rule.max_orders} 笔 / ${rule.window_seconds ?? 60}s`)
  if (rule.symbols !== undefined) parts.push(rule.symbols.length ? rule.symbols.join(', ') : '无黑名单')
  if (rule.enabled === false) return '已关闭'
  return parts.length ? parts.join(' · ') : '启用'
}

export function RiskPage() {
  const [rules, setRules] = useState<RiskRule[]>([])
  const [presets, setPresets] = useState<RiskPresets | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([riskApi.rules(), riskApi.presets()])
      .then(([ruleList, presetData]) => {
        setRules(ruleList)
        setPresets(presetData)
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  const ruleIds = useMemo(() => new Set(rules.map((rule) => rule.id)), [rules])
  const presetNames = presets ? Object.keys(presets.presets) : []

  if (loading) return <Loading text="正在加载风控规则…" />

  return (
    <div className="col" style={{ gap: 18 }}>
      {error && (
        <Card>
          <Empty title="加载失败" hint={error} />
        </Card>
      )}

      <Card
        title="风控执行链路"
        description="风控在引擎侧执行，前端选择的预设只是一组阈值"
        actions={
          <Link to="/backtest" className="btn btn-sm btn-primary">
            <IconFlask /> 带着风控回测
          </Link>
        }
      >
        <div className="grid grid-4">
          {[
            ['1 · 策略下单', '策略调用 buy / sell / order_target_percent，只表达意图。'],
            ['2 · 逐条校验', '仓位、单笔金额、持仓数、日内亏损、回撤、频率、黑名单依次检查。'],
            ['3 · 拒绝留痕', '被拒订单同样记录，reason 字段标明触发的规则。'],
            ['4 · 撮合执行', '通过校验的订单进入撮合器，按下一根 K 线成交。'],
          ].map(([title, text]) => (
            <div key={title} className="stat">
              <div className="stat-label">{title}</div>
              <div className="small muted" style={{ marginTop: 4 }}>
                {text}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid grid-3">
        {presetNames.map((name) => {
          const items = presets?.presets[name] ?? []
          return (
            <Card
              key={name}
              title={
                <span className="row" style={{ gap: 8 }}>
                  {PRESET_LABEL[name] ?? name}
                  {presets?.default === name && <Badge tone="accent">默认</Badge>}
                </span>
              }
              description={
                name === 'conservative'
                  ? '低仓位、低回撤容忍，适合实盘前的保守验证'
                  : name === 'aggressive'
                    ? '接近不限制，适合观察策略的原始行为'
                    : '内置默认值，允许单票接近满仓，兼顾信号释放'
              }
              flush
            >
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>规则</th>
                      <th>阈值</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((rule) => (
                      <tr key={rule.id}>
                        <td className="strong tiny">
                          {rules.find((item) => item.id === rule.id)?.name ?? rule.id}
                          {!ruleIds.has(rule.id) && <span className="faint">（未知）</span>}
                        </td>
                        <td className="tiny faint">{presetSummary(rule)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )
        })}
      </div>

      <Card title="规则目录" description="每条规则的参数、严重级别与本次进程内的触发次数" flush>
        {rules.length === 0 ? (
          <Empty title="没有注册风控规则" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>规则</th>
                  <th>ID</th>
                  <th>说明</th>
                  <th>默认参数</th>
                  <th>级别</th>
                  <th className="right">触发</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <tr key={rule.id}>
                    <td className="strong">
                      <span className="row" style={{ gap: 6 }}>
                        <IconShield style={{ width: 14, height: 14, color: 'var(--accent)' }} />
                        {rule.name}
                      </span>
                    </td>
                    <td className="mono tiny">{rule.id}</td>
                    <td className="tiny">{rule.description}</td>
                    <td className="tiny mono">
                      {Object.entries(rule.params)
                        .map(([key, value]) => `${key}=${formatValue(rule.id, key, value)}`)
                        .join(', ') || '—'}
                    </td>
                    <td>
                      <Badge
                        tone={
                          rule.severity === 'critical'
                            ? 'danger'
                            : rule.severity === 'warning'
                              ? 'warn'
                              : 'neutral'
                        }
                      >
                        {rule.severity}
                      </Badge>
                    </td>
                    <td className="right num">{rule.trigger_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="自定义风控">
        <div className="small muted">
          回测与模拟盘 API 都接受 <span className="mono">risk</span> 数组，可逐条覆盖阈值：
          <pre className="code-block" style={{ marginTop: 10 }}>{`"risk": [
  { "id": "max_position_pct", "enabled": true, "limit": 0.2 },
  { "id": "order_rate_limit", "max_orders": 20, "window_seconds": 60 },
  { "id": "blacklist", "symbols": ["600519"] }
]`}</pre>
          传入 <span className="mono">null</span> 使用内置默认；前端预设只是这些数组的快捷方式。
        </div>
        <div className="divider" />
        <Button size="sm" onClick={() => window.history.back()}>
          返回
        </Button>
      </Card>
    </div>
  )
}

/** 策略库：展示后端注册表中的策略与参数 schema，并提供免代码的条件拼装器。 */

import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ApiError, strategyApi } from '../api/client'
import type { CustomGraphSpec, CustomSpec, CustomStrategy, Strategy, StrategyParam } from '../api/types'
import { Badge, Button, Card, Empty, Input, Loading } from '../components/ui'
import { AdminStrategiesCard, SharedStrategiesCard, ShareStrategyModal } from '../components/StrategySharing'
import { StrategyBuilder, conditionLabel } from '../components/StrategyBuilder'
import { IconFlask, IconSearch, IconTrash } from '../components/icons'
import { notify } from '../store/toast'
import { useAuth } from '../store/auth'
import { cx } from '../utils/format'

function paramRange(param: StrategyParam): string {
  if (param.choices && param.choices.length > 0) return param.choices.join(' / ')
  if (param.min !== null && param.min !== undefined && param.max !== null && param.max !== undefined) {
    return `${param.min} — ${param.max}`
  }
  if (param.min !== null && param.min !== undefined) return `≥ ${param.min}`
  if (param.max !== null && param.max !== undefined) return `≤ ${param.max}`
  return '—'
}

function defaultText(param: StrategyParam): string {
  if (typeof param.default === 'boolean') return param.default ? '是' : '否'
  if (param.type === 'json') return '（网页生成）'
  if (param.default === null || param.default === undefined) return '—'
  if (typeof param.default === 'object') return '（结构化）'
  return String(param.default)
}

export function StrategiesPage() {
  const navigate = useNavigate()
  const [items, setItems] = useState<Strategy[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState('all')

  // ---- 我的拼装策略 ----
  const [custom, setCustom] = useState<CustomStrategy[]>([])
  const [customLoading, setCustomLoading] = useState(true)
  const [customError, setCustomError] = useState('')
  const [shareTarget, setShareTarget] = useState<CustomStrategy | null>(null)
  const isAdmin = useAuth((state) => state.user?.role === 'admin')

  const loadCustom = () => {
    setCustomLoading(true)
    strategyApi
      .custom()
      .then((list) => {
        setCustom(list)
        setCustomError('')
      })
      .catch((err: Error) => setCustomError(err instanceof ApiError ? err.message : '加载失败'))
      .finally(() => setCustomLoading(false))
  }

  useEffect(() => {
    loadCustom()
  }, [])

  const removeCustom = async (item: CustomStrategy) => {
    if (!window.confirm(`删除拼装策略「${item.name}」？使用它的已保存回测记录不受影响。`)) return
    try {
      await strategyApi.removeCustom(item.id)
      notify('success', `已删除「${item.name}」`)
      loadCustom()
    } catch (err) {
      notify('error', err instanceof Error ? err.message : '删除失败')
    }
  }

  const syncCustom = async (item: CustomStrategy) => {
    try {
      const updated = await strategyApi.syncCustom(item.id)
      notify('success', `「${updated.name}」已同步到 v${updated.source_version ?? updated.version}`)
      loadCustom()
    } catch (err) {
      notify('error', err instanceof Error ? err.message : '同步失败')
    }
  }

  const toggleCustom = async (item: CustomStrategy) => {
    try {
      await strategyApi.updateCustom(item.id, { enabled: !item.enabled })
      loadCustom()
    } catch (err) {
      notify('error', err instanceof Error ? err.message : '操作失败')
    }
  }

  useEffect(() => {
    strategyApi
      .list()
      .then(setItems)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  const categories = useMemo(() => {
    const set = new Set(items.map((item) => item.category || '未分类'))
    return ['all', ...Array.from(set)]
  }, [items])

  const filtered = useMemo(() => {
    const text = keyword.trim().toLowerCase()
    return items.filter((item) => {
      if (category !== 'all' && (item.category || '未分类') !== category) return false
      if (!text) return true
      return (
        item.id.toLowerCase().includes(text) ||
        item.name.toLowerCase().includes(text) ||
        item.description.toLowerCase().includes(text)
      )
    })
  }, [items, keyword, category])

  if (loading) return <Loading text="正在加载策略…" />

  return (
    <div className="col" style={{ gap: 16 }}>
      <Card>
        <div className="row wrap" style={{ gap: 12 }}>
          <div className="row" style={{ flex: 1, minWidth: 220, position: 'relative' }}>
            <IconSearch
              style={{
                width: 15,
                height: 15,
                position: 'absolute',
                left: 11,
                color: 'var(--text-faint)',
              }}
            />
            <Input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="搜索策略名称、ID 或描述…"
              style={{ paddingLeft: 34 }}
            />
          </div>
          <div className="row wrap" style={{ gap: 6 }}>
            {categories.map((item) => (
              <button
                key={item}
                type="button"
                className={cx('btn btn-sm', category === item && 'btn-primary')}
                onClick={() => setCategory(item)}
              >
                {item === 'all' ? '全部' : item}
              </button>
            ))}
          </div>
          <Badge tone="accent">{filtered.length} 个策略</Badge>
        </div>
      </Card>

      {error && (
        <Card>
          <Empty title="加载失败" hint={error} />
        </Card>
      )}

      {filtered.length === 0 ? (
        <Card>
          <Empty title="没有匹配的策略" hint="换个关键词或分类试试。" />
        </Card>
      ) : (
        <div className="grid grid-2">
          {filtered.map((strategy) => (
            <Card
              key={strategy.id}
              title={
                <span className="row" style={{ gap: 8 }}>
                  {strategy.name}
                  <Badge>{strategy.id}</Badge>
                </span>
              }
              description={strategy.description || '暂无描述'}
              actions={
                <Link to={`/backtest?strategy=${strategy.id}`} className="btn btn-sm btn-primary">
                  <IconFlask /> 回测
                </Link>
              }
            >
              {strategy.params.length === 0 ? (
                <div className="faint small">无可调参数。</div>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>参数</th>
                        <th>类型</th>
                        <th>默认</th>
                        <th>范围 / 选项</th>
                        <th>说明</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategy.params.map((param) => (
                        <tr key={param.name}>
                          <td className="strong mono">{param.name}</td>
                          <td className="tiny faint">{param.type}</td>
                          <td className="num">{defaultText(param)}</td>
                          <td className="tiny faint">{paramRange(param)}</td>
                          <td className="tiny">{param.description || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="divider" />
              <div className="row wrap" style={{ gap: 6 }}>
                <Badge tone="accent">{strategy.category || '未分类'}</Badge>
                <span className="tiny faint">参数由后端 ParamSpec 自动生成，新增策略无需改前端。</span>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* ---- 我的拼装策略（免代码） ---- */}
      <Card
        title="我的拼装策略"
        description="用条件拼装买卖规则，无需写 Python；spec 经服务端白名单校验后由通用解释器执行"
        flush
      >
        <div style={{ padding: '14px 16px 0' }}>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <StrategyBuilder onSaved={loadCustom} />
            <Button variant="ghost" onClick={() => navigate('/strategies/blueprint')}>
              ✦ 蓝图编辑器
            </Button>
          </div>
        </div>
        <div className="divider" />
        {customLoading ? (
          <Loading text="加载拼装策略…" />
        ) : customError ? (
          <Empty title="加载失败" hint={customError} />
        ) : custom.length === 0 ? (
          <Empty title="还没有拼装策略" hint="点上方「拼装新策略」，用指标条件拼出你的第一套规则。" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>名称</th>
                  <th>入场条件（全部满足）</th>
                  <th>离场条件（任一满足）</th>
                  <th>仓位 / 止损 / 止盈</th>
                  <th className="right">操作</th>
                </tr>
              </thead>
              <tbody>
                {custom.map((item) => {
                  const spec = item.spec
                  const isGraph = spec != null && (spec as CustomGraphSpec).version === 2 && 'nodes' in spec
                  const nodeCount = isGraph ? (spec as CustomGraphSpec).nodes.length : 0
                  const position =
                    spec?.position_pct != null ? `${Math.round(spec.position_pct * 100)}%` : '—'
                  const stop =
                    spec?.stop_loss != null && spec.stop_loss > 0
                      ? `${Math.round(spec.stop_loss * 100)}%`
                      : '无'
                  const take =
                    spec?.take_profit != null && spec.take_profit > 0
                      ? `${Math.round(spec.take_profit * 100)}%`
                      : '无'
                  return (
                    <tr key={item.id}>
                      <td>
                        <div className="strong">{item.name}</div>
                        <div className="row tiny faint" style={{ gap: 4, flexWrap: 'wrap' }}>
                          {item.enabled ? (
                            <Badge tone="success">启用</Badge>
                          ) : (
                            <Badge tone="warn">已停用</Badge>
                          )}
                          <Badge tone="neutral">v{item.version ?? 1}</Badge>
                          {item.source_strategy_id && (
                            <Badge tone={item.update_available ? 'warn' : 'neutral'}>
                              {item.update_available ? '副本 · 有更新' : '共享副本'}
                            </Badge>
                          )}
                          {isGraph && <Badge tone="accent">蓝图 · {nodeCount} 节点</Badge>}
                        </div>
                      </td>
                      <td className="small">
                        {isGraph ? (
                          <span className="tiny faint">（节点图，点「编辑蓝图」查看连线）</span>
                        ) : (
                          ((spec as CustomSpec | null)?.entry ?? []).map((cond, i) => (
                            <div key={i} className="mono tiny">
                              {conditionLabel(cond)}
                            </div>
                          ))
                        )}
                      </td>
                      <td className="small">
                        {isGraph ? (
                          <span className="tiny faint">—</span>
                        ) : (
                          ((spec as CustomSpec | null)?.exit ?? []).map((cond, i) => (
                            <div key={i} className="mono tiny">
                              {conditionLabel(cond)}
                            </div>
                          ))
                        )}
                      </td>
                      <td className="num tiny">
                        {position} / {stop} / {take}
                      </td>
                      <td className="right">
                        <div className="row" style={{ justifyContent: 'flex-end', gap: 4 }}>
                          <Link
                            to={`/backtest?strategy=custom_rule&custom=${item.id}`}
                            className="btn btn-sm btn-primary"
                          >
                            <IconFlask /> 回测
                          </Link>
                          {isGraph && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => navigate(`/strategies/blueprint?edit=${item.id}`)}
                            >
                              编辑蓝图
                            </Button>
                          )}
                          {item.update_available && (
                            <Button size="sm" variant="primary" onClick={() => void syncCustom(item)}>
                              同步更新
                            </Button>
                          )}
                          <Button size="sm" variant="ghost" onClick={() => setShareTarget(item)}>
                            共享
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => void toggleCustom(item)}>
                            {item.enabled ? '停用' : '启用'}
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => void removeCustom(item)}>
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

      <SharedStrategiesCard onChanged={loadCustom} />

      {isAdmin && <AdminStrategiesCard />}

      <ShareStrategyModal
        open={shareTarget !== null}
        strategy={shareTarget}
        onClose={() => setShareTarget(null)}
        onChanged={loadCustom}
      />
    </div>
  )
}

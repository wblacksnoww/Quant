/** 拼装策略共享：完全共享、点对点、fork、版本同步与管理员管理。 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { adminApi, ApiError, strategyApi } from '../api/client'
import type { AdminStrategy, CustomStrategy, SharedStrategy, StrategyShare } from '../api/types'
import { notify } from '../store/toast'
import { Badge, Banner, Button, Card, Empty, Field, Loading, Modal, Tabs } from './ui'
import { fmtDateTime } from '../utils/format'

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

export function ShareStrategyModal({
  open,
  strategy,
  onClose,
  onChanged,
}: {
  open: boolean
  strategy: CustomStrategy | null
  onClose: () => void
  onChanged: () => void
}) {
  const [shares, setShares] = useState<StrategyShare[]>([])
  const [scope, setScope] = useState<'public' | 'user'>('public')
  const [target, setTarget] = useState('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (!strategy) return
    setLoading(true)
    setError('')
    try {
      setShares(await strategyApi.shares(strategy.id))
    } catch (err) {
      setError(errorText(err, '读取共享记录失败'))
    } finally {
      setLoading(false)
    }
  }, [strategy])

  useEffect(() => {
    if (open && strategy) void load()
  }, [open, strategy, load])

  const submit = async () => {
    if (!strategy) return
    if (scope === 'user' && !target.trim()) {
      setError('请填写目标用户名')
      return
    }
    setBusy(true)
    setError('')
    try {
      await strategyApi.shareCustom(strategy.id, {
        scope,
        target_username: scope === 'user' ? target.trim() : undefined,
      })
      notify('success', scope === 'public' ? '已加入完全共享库' : `已共享给 ${target.trim()}`)
      setTarget('')
      await load()
      onChanged()
    } catch (err) {
      setError(errorText(err, '共享失败'))
    } finally {
      setBusy(false)
    }
  }

  const revoke = async (shareId: number) => {
    if (!strategy) return
    try {
      await strategyApi.revokeShare(strategy.id, shareId)
      notify('success', '已撤销共享')
      await load()
      onChanged()
    } catch (err) {
      notify('error', errorText(err, '撤销失败'))
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={strategy ? `共享策略 · ${strategy.name}` : '共享策略'}
      width={620}
    >
      <div className="col" style={{ gap: 14 }}>
        {error && <Banner kind="danger">{error}</Banner>}
        <Banner kind="info">
          完全共享会出现在所有人的共享库；点对点共享只出现在目标用户的「共享给我」。
          对方加入后是独立副本，源策略更新时可以手动同步，不更新则保留旧版本。
        </Banner>

        <div className="form-grid">
          <Field label="共享方式">
            <select
              className="select"
              value={scope}
              onChange={(event) => setScope(event.target.value as 'public' | 'user')}
            >
              <option value="public">完全共享（所有人可见）</option>
              <option value="user">点对点（指定用户）</option>
            </select>
          </Field>
          {scope === 'user' && (
            <Field label="目标用户名">
              <input
                className="input"
                value={target}
                onChange={(event) => setTarget(event.target.value)}
                placeholder="对方登录用户名"
              />
            </Field>
          )}
        </div>

        <Button variant="primary" loading={busy} onClick={() => void submit()}>
          添加共享
        </Button>

        <div className="divider" />
        <div className="row between">
          <strong>已有共享</strong>
          <Badge>{shares.length}</Badge>
        </div>
        {loading ? (
          <Loading text="读取共享记录…" />
        ) : shares.length === 0 ? (
          <Empty title="还没有共享" hint="添加完全共享或点对点共享后，对方才能在共享库看到。" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>方式</th>
                  <th>目标</th>
                  <th>时间</th>
                  <th className="right">操作</th>
                </tr>
              </thead>
              <tbody>
                {shares.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Badge tone={item.scope === 'public' ? 'accent' : 'neutral'}>
                        {item.scope === 'public' ? '完全共享' : '点对点'}
                      </Badge>
                    </td>
                    <td>{item.target_username || '所有用户'}</td>
                    <td className="tiny faint">{fmtDateTime(item.created_at)}</td>
                    <td className="right">
                      <Button size="sm" variant="ghost" onClick={() => void revoke(item.id)}>
                        撤销
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Modal>
  )
}

export function SharedStrategiesCard({ onChanged }: { onChanged: () => void }) {
  const [items, setItems] = useState<SharedStrategy[]>([])
  const [scope, setScope] = useState<'public' | 'user'>('public')
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setItems(await strategyApi.shared())
    } catch (err) {
      setError(errorText(err, '加载共享库失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const visible = useMemo(() => items.filter((item) => item.share_type === scope), [items, scope])

  const fork = async (item: SharedStrategy) => {
    setBusyId(item.strategy_id)
    try {
      const created = await strategyApi.forkShared(item.strategy_id)
      notify('success', `「${created.name}」已加入我的策略（v${created.version}）`)
      await load()
      onChanged()
    } catch (err) {
      notify('error', errorText(err, '加入失败'))
    } finally {
      setBusyId(null)
    }
  }

  const sync = async (item: SharedStrategy) => {
    if (!item.fork_id) return
    setBusyId(item.strategy_id)
    try {
      const updated = await strategyApi.syncCustom(item.fork_id)
      notify('success', `已同步到 v${updated.source_version ?? updated.version}`)
      await load()
      onChanged()
    } catch (err) {
      notify('error', errorText(err, '同步失败'))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card
      title="共享策略库"
      description="完全共享对所有人可见；点对点只显示共享给你的策略。加入后是独立副本，可手动同步版本"
      actions={
        <Button size="sm" variant="ghost" onClick={() => void load()}>
          刷新
        </Button>
      }
      flush
    >
      <div style={{ padding: '12px 16px 0' }}>
        <Tabs<'public' | 'user'>
          value={scope}
          onChange={setScope}
          options={[
            { value: 'public', label: `完全共享 ${items.filter((i) => i.share_type === 'public').length}` },
            { value: 'user', label: `共享给我 ${items.filter((i) => i.share_type === 'user').length}` },
          ]}
        />
      </div>
      {error && (
        <div className="card-body">
          <Banner kind="danger">{error}</Banner>
        </div>
      )}
      {loading ? (
        <Loading text="加载共享库…" />
      ) : visible.length === 0 ? (
        <Empty
          title={scope === 'public' ? '完全共享库为空' : '暂时没有人单独共享给你'}
          hint="用户需要先在「我的拼装策略」里点「共享」。"
        />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>策略</th>
                <th>来源</th>
                <th>版本</th>
                <th>状态</th>
                <th className="right">操作</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((item) => (
                <tr key={`${item.share_id}-${item.strategy_id}`}>
                  <td>
                    <div className="strong">{item.name}</div>
                    <div className="tiny faint">
                      {item.description || item.category} · v{item.version}
                    </div>
                  </td>
                  <td className="tiny">
                    {item.owner_username}
                    <div className="faint">{item.share_type === 'public' ? '完全共享' : '点对点'}</div>
                  </td>
                  <td className="tiny">v{item.version}</td>
                  <td>
                    {item.fork_id ? (
                      item.update_available ? (
                        <Badge tone="warn">有更新</Badge>
                      ) : (
                        <Badge tone="success">已同步</Badge>
                      )
                    ) : (
                      <Badge tone="neutral">未加入</Badge>
                    )}
                  </td>
                  <td className="right">
                    {item.fork_id ? (
                      item.update_available ? (
                        <Button
                          size="sm"
                          variant="primary"
                          loading={busyId === item.strategy_id}
                          onClick={() => void sync(item)}
                        >
                          同步到 v{item.version}
                        </Button>
                      ) : (
                        <span className="tiny faint">已是最新</span>
                      )
                    ) : (
                      <Button
                        size="sm"
                        variant="primary"
                        loading={busyId === item.strategy_id}
                        onClick={() => void fork(item)}
                      >
                        加入我的策略
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
  )
}

export function AdminStrategiesCard() {
  const [items, setItems] = useState<AdminStrategy[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setItems(await adminApi.strategies())
    } catch (err) {
      setError(errorText(err, '加载策略库失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const toggle = async (item: AdminStrategy) => {
    setBusyId(item.id)
    try {
      await adminApi.updateStrategy(item.id, !item.enabled)
      await load()
    } catch (err) {
      notify('error', errorText(err, '更新失败'))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (item: AdminStrategy) => {
    if (
      !window.confirm(`删除策略「${item.name}」（用户 ${item.owner_username}）？副本会保留但不再跟随源更新。`)
    )
      return
    setBusyId(item.id)
    try {
      await adminApi.deleteStrategy(item.id)
      notify('success', '已删除策略')
      await load()
    } catch (err) {
      notify('error', errorText(err, '删除失败'))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card
      title="策略库管理"
      description="管理员可查看、停用或删除所有用户的拼装策略；删除源策略不影响已加入的副本"
      actions={
        <Button size="sm" variant="ghost" onClick={() => void load()}>
          刷新
        </Button>
      }
      flush
    >
      {error && (
        <div className="card-body">
          <Banner kind="danger">{error}</Banner>
        </div>
      )}
      {loading ? (
        <Loading text="加载全部策略…" />
      ) : items.length === 0 ? (
        <Empty title="暂无用户拼装策略" />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>策略</th>
                <th>所有者</th>
                <th>版本</th>
                <th>共享 / 副本</th>
                <th>状态</th>
                <th className="right">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <div className="strong">{item.name}</div>
                    <div className="tiny faint">{item.description || item.category}</div>
                  </td>
                  <td>{item.owner_username}</td>
                  <td className="tiny">v{item.version}</td>
                  <td className="tiny">
                    {item.share_count} / {item.fork_count}
                  </td>
                  <td>
                    <Badge tone={item.enabled ? 'success' : 'warn'}>{item.enabled ? '启用' : '已停用'}</Badge>
                  </td>
                  <td className="right">
                    <div className="row" style={{ justifyContent: 'flex-end', gap: 4 }}>
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={busyId === item.id}
                        onClick={() => void toggle(item)}
                      >
                        {item.enabled ? '停用' : '启用'}
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        loading={busyId === item.id}
                        onClick={() => void remove(item)}
                      >
                        删除
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
  )
}

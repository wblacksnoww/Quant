/** 拼装策略选择下拉：回测页与纸盘页共用。加载当前用户的自建策略，选中后随表单提交。 */

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { strategyApi } from '../api/client'
import type { CustomGraphSpec, CustomSpec, CustomStrategy } from '../api/types'
import { Button, Loading } from './ui'
import { IconRefresh } from './icons'

export function CustomStrategySelect({
  value,
  onChange,
}: {
  /** 当前选中的拼装策略 id（null = 未选） */
  value: number | null
  onChange: (id: number | null) => void
}) {
  const [items, setItems] = useState<CustomStrategy[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    strategyApi
      .custom()
      .then(setItems)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  if (loading) return <Loading text="加载拼装策略…" />

  const selected = items.find((item) => item.id === value)

  return (
    <div className="field">
      <span className="label">选择拼装策略</span>
      <div className="row" style={{ gap: 8, alignItems: 'center' }}>
        <select
          className="select"
          value={value ?? ''}
          onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
          style={{ flex: 1 }}
        >
          <option value="">{items.length === 0 ? '（还没有拼装策略）' : '请选择…'}</option>
          {items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
              {item.enabled ? '' : '（已停用）'}
            </option>
          ))}
        </select>
        <Button size="sm" variant="ghost" onClick={load} title="刷新列表">
          <IconRefresh />
        </Button>
      </div>
      <span className="hint" style={{ marginTop: 4 }}>
        {error ? (
          <span style={{ color: 'var(--danger)' }}>{error}</span>
        ) : items.length === 0 ? (
          <>
            还没有自建策略——去
            <Link to="/strategies" style={{ marginLeft: 4 }}>
              策略库
            </Link>
            用条件拼装器创建一个，无需写代码。
          </>
        ) : selected ? (
          <>
            {selected.spec && 'nodes' in selected.spec
              ? `蓝图 · ${(selected.spec as CustomGraphSpec).nodes.length} 个节点 · 由服务端白名单校验执行`
              : `入场 ${(selected.spec as CustomSpec | undefined)?.entry?.length ?? 0} 条 / 离场 ${
                  (selected.spec as CustomSpec | undefined)?.exit?.length ?? 0
                } 条 · 由服务端白名单校验执行`}
          </>
        ) : (
          '在下方选择要运行的拼装策略'
        )}
      </span>
    </div>
  )
}

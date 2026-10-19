/** 可拖拽的卡片网格：用于行情页与仪表盘。
 *
 * - 编辑模式下拖动 ⠿ 手柄排序、切换宽/窄、隐藏/显示卡片；
 * - 布局（顺序、宽窄、可见性）存 localStorage，刷新后保留；
 * - 默认两列网格，窄屏自动单列。
 */

import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Button } from './ui'
import { notify } from '../store/toast'

export interface BoardItem {
  id: string
  title: string
  wide?: boolean
  render: () => ReactNode
}

interface CellConfig {
  id: string
  visible: boolean
  wide: boolean
}

function defaultsOf(items: BoardItem[]): CellConfig[] {
  return items.map((item) => ({ id: item.id, visible: true, wide: Boolean(item.wide) }))
}

function mergeConfig(items: BoardItem[], stored: unknown): CellConfig[] {
  const defaults = defaultsOf(items)
  if (!Array.isArray(stored)) return defaults
  const byId = new Map(defaults.map((cell) => [cell.id, cell]))
  const out: CellConfig[] = []
  for (const raw of stored) {
    if (!raw || typeof raw !== 'object') continue
    const record = raw as Partial<CellConfig>
    const base = byId.get(String(record.id))
    if (!base) continue
    out.push({ id: base.id, visible: record.visible !== false, wide: Boolean(record.wide) })
    byId.delete(base.id)
  }
  for (const rest of byId.values()) out.push(rest) // 新增卡片追加到末尾
  return out
}

function loadConfig(storageKey: string, items: BoardItem[]): CellConfig[] {
  try {
    const raw = localStorage.getItem(storageKey)
    if (!raw) return defaultsOf(items)
    return mergeConfig(items, JSON.parse(raw))
  } catch {
    return defaultsOf(items)
  }
}

export function CardBoard({
  storageKey,
  items,
}: {
  storageKey: string
  items: BoardItem[]
}) {
  const [config, setConfig] = useState<CellConfig[]>(() => loadConfig(storageKey, items))
  const [editing, setEditing] = useState(false)
  const [dragging, setDragging] = useState<string | null>(null)
  const [over, setOver] = useState<string | null>(null)

  // 卡片的 id 集合变化时（新增/删除卡片）合并配置
  const itemKey = items.map((item) => item.id).join(',')
  useEffect(() => {
    setConfig((prev) => {
      const known = new Set(prev.map((cell) => cell.id))
      const missing = items.filter((item) => !known.has(item.id))
      if (missing.length === 0) return prev
      return [...prev, ...defaultsOf(missing)]
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemKey])

  const itemMap = useMemo(() => new Map(items.map((item) => [item.id, item])), [items])

  const persist = (next: CellConfig[]) => {
    setConfig(next)
    try {
      localStorage.setItem(storageKey, JSON.stringify(next))
    } catch {
      // 存储不可用时仅当前会话生效
    }
  }

  const move = (fromId: string, toId: string) => {
    if (fromId === toId) return
    const next = [...config]
    const from = next.findIndex((cell) => cell.id === fromId)
    const to = next.findIndex((cell) => cell.id === toId)
    if (from < 0 || to < 0) return
    const [moved] = next.splice(from, 1)
    next.splice(to, 0, moved)
    persist(next)
  }

  const toggleWide = (id: string) => {
    persist(config.map((cell) => (cell.id === id ? { ...cell, wide: !cell.wide } : cell)))
  }

  const toggleVisible = (id: string) => {
    persist(config.map((cell) => (cell.id === id ? { ...cell, visible: !cell.visible } : cell)))
  }

  const reset = () => {
    persist(defaultsOf(items))
    notify('success', '已恢复默认布局')
  }

  return (
    <div className="board">
      <div className="board-toolbar">
        <span className="tiny faint">
          {editing ? '拖动 ⠿ 排序 · 「宽/窄」切换占宽 · ✕ 隐藏卡片' : ''}
        </span>
        <div className="row" style={{ gap: 8 }}>
          {editing && (
            <Button size="sm" variant="ghost" onClick={reset}>
              恢复默认
            </Button>
          )}
          <Button
            size="sm"
            variant={editing ? 'primary' : 'default'}
            onClick={() => {
              setEditing((value) => !value)
              setDragging(null)
              setOver(null)
            }}
          >
            {editing ? '完成' : '编辑布局'}
          </Button>
        </div>
      </div>

      <div className={`board-grid${editing ? ' editing' : ''}`}>
        {config.map((cell) => {
          const item = itemMap.get(cell.id)
          if (!item) return null
          if (!cell.visible && !editing) return null
          return (
            <section
              key={cell.id}
              className={[
                'board-cell',
                cell.wide ? 'wide' : '',
                editing && !cell.visible ? 'is-hidden' : '',
                over === cell.id && dragging ? 'drag-over' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              onDragOver={
                editing
                  ? (event) => {
                      event.preventDefault()
                      setOver(cell.id)
                    }
                  : undefined
              }
              onDrop={
                editing
                  ? () => {
                      if (dragging) move(dragging, cell.id)
                      setDragging(null)
                      setOver(null)
                    }
                  : undefined
              }
            >
              {editing && (
                <div className="board-cell-bar">
                  <span
                    className="board-drag"
                    draggable
                    onDragStart={(event) => {
                      setDragging(cell.id)
                      event.dataTransfer.effectAllowed = 'move'
                    }}
                    onDragEnd={() => {
                      setDragging(null)
                      setOver(null)
                    }}
                    title="拖动排序"
                  >
                    ⠿ {item.title}
                  </span>
                  <div className="row" style={{ gap: 4 }}>
                    <Button size="sm" variant="ghost" onClick={() => toggleWide(cell.id)}>
                      {cell.wide ? '窄' : '宽'}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => toggleVisible(cell.id)}>
                      {cell.visible ? '✕' : '显示'}
                    </Button>
                  </div>
                </div>
              )}
              {cell.visible ? item.render() : null}
            </section>
          )
        })}
      </div>
    </div>
  )
}

/** 行情页顶部指数卡片：实时点数 + 涨跌，可编辑显示哪些指数及顺序。
 *
 * 指数不可交易，独立于标的体系；配置存 localStorage（quant.index_cards）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { marketApi } from '../api/client'
import type { IndexQuote } from '../api/types'
import { Badge, Button, Modal } from './ui'
import { IconSettings } from './icons'
import { classForSign, fmtNumber, fmtSignedPct, marketLabel, marketTone } from '../utils/format'
import { notify } from '../store/toast'

const STORAGE_KEY = 'quant.index_cards'
const DEFAULT_IDS = ['sh000001', 'sz399001', 'sz399006', 'sh000300', 'hkHSI', 'usIXIC']
const REFRESH_MS = 5_000

function loadIds(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_IDS
    const parsed: unknown = JSON.parse(raw)
    if (Array.isArray(parsed) && parsed.length > 0 && parsed.every((item) => typeof item === 'string')) {
      return parsed as string[]
    }
  } catch {
    // 本地配置损坏时回退默认
  }
  return DEFAULT_IDS
}

function changeText(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—'
  return `${value >= 0 ? '+' : ''}${fmtNumber(value, digits)}`
}

export function IndexCards() {
  const [all, setAll] = useState<IndexQuote[]>([])
  const [ids, setIds] = useState<string[]>(loadIds)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<string[]>(ids)

  const load = useCallback(() => {
    marketApi
      .indices()
      .then((result) => setAll(result))
      .catch(() => undefined) // 刷新失败保留上一次数据，不清空卡片
  }, [])

  useEffect(() => {
    load()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') load()
    }, REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [load])

  const byId = useMemo(() => new Map(all.map((item) => [item.id, item])), [all])
  const cards = useMemo(
    () => ids.map((id) => byId.get(id)).filter((item): item is IndexQuote => Boolean(item)),
    [ids, byId],
  )

  const openEdit = () => {
    setDraft(ids)
    setOpen(true)
  }

  const move = (index: number, delta: number) => {
    setDraft((prev) => {
      const target = index + delta
      if (target < 0 || target >= prev.length) return prev
      const next = [...prev]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  }

  const toggle = (id: string) => {
    setDraft((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]))
  }

  const save = () => {
    setIds(draft)
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(draft))
    } catch {
      notify('warning', '浏览器禁止本地存储，本次配置只在当前页面生效')
    }
    setOpen(false)
    notify('success', '指数卡片已保存')
  }

  const reset = () => setDraft(DEFAULT_IDS)

  const others = all.filter((item) => !draft.includes(item.id))
  const nameOf = (id: string) => byId.get(id)?.name ?? id

  return (
    <section className="card">
      <header className="card-head">
        <div>
          <div className="card-title">指数</div>
          <div className="card-desc">常用指数实时快照（5 秒刷新，可在右侧编辑）</div>
        </div>
        <div className="row">
          <Button size="sm" variant="ghost" title="编辑指数卡片" onClick={openEdit}>
            <IconSettings />
          </Button>
        </div>
      </header>
      <div className="card-body">
        {cards.length === 0 ? (
          <div className="empty">暂无指数数据，点击右上角编辑添加</div>
        ) : (
          <div className="index-grid">
            {cards.map((item) => {
              const tone = classForSign(item.change_pct)
              return (
                <div key={item.id} className="index-card">
                  <div className="row between" style={{ gap: 6 }}>
                    <span className="small strong">{item.name}</span>
                    <Badge tone={marketTone(item.market)}>{marketLabel(item.market)}</Badge>
                  </div>
                  <div className={`num ${tone}`} style={{ fontSize: 22, fontWeight: 600, marginTop: 2 }}>
                    {fmtNumber(item.price, 2)}
                  </div>
                  <div className="row" style={{ gap: 10 }}>
                    <span className={`num tiny ${tone}`}>{changeText(item.change)}</span>
                    <span className={`num tiny ${tone}`}>
                      {item.change_pct === null ? '—' : fmtSignedPct(item.change_pct / 100)}
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <Modal
        open={open}
        title="编辑指数卡片"
        onClose={() => setOpen(false)}
        width={520}
        footer={
          <div className="row" style={{ justifyContent: 'space-between', gap: 8 }}>
            <Button variant="ghost" size="sm" onClick={reset}>
              恢复默认
            </Button>
            <div className="row" style={{ gap: 8 }}>
              <Button variant="ghost" onClick={() => setOpen(false)}>
                取消
              </Button>
              <Button onClick={save}>保存</Button>
            </div>
          </div>
        }
      >
        <div className="col" style={{ gap: 14 }}>
          <div>
            <div className="tiny faint" style={{ marginBottom: 6 }}>
              已显示（按顺序，可上下调整）
            </div>
            {draft.length === 0 && <div className="tiny faint">还没有选择指数</div>}
            {draft.map((id, index) => (
              <div key={id} className="index-edit-row">
                <span className="mono small" style={{ minWidth: 74 }}>
                  {id}
                </span>
                <span className="strong" style={{ flex: 1 }}>
                  {nameOf(id)}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                  title="上移"
                >
                  ↑
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={index === draft.length - 1}
                  onClick={() => move(index, 1)}
                  title="下移"
                >
                  ↓
                </Button>
                <Button size="sm" variant="ghost" onClick={() => toggle(id)} title="移除">
                  ✕
                </Button>
              </div>
            ))}
          </div>

          {others.length > 0 && (
            <div>
              <div className="tiny faint" style={{ marginBottom: 6 }}>
                添加其他指数
              </div>
              {others.map((item) => (
                <div key={item.id} className="index-edit-row">
                  <span className="mono small" style={{ minWidth: 74 }}>
                    {item.id}
                  </span>
                  <span style={{ flex: 1 }}>{item.name}</span>
                  <Badge tone={marketTone(item.market)}>{marketLabel(item.market)}</Badge>
                  <Button size="sm" variant="ghost" onClick={() => toggle(item.id)} title="添加">
                    +
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </Modal>
    </section>
  )
}

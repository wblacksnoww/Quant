/** 标的选择器：优先从账号自选勾选，支持补搜（搜索仅兜底）。 */

import { useMemo, useState } from 'react'
import { marketApi } from '../api/client'
import type { SymbolInfo } from '../api/types'
import { Badge, Button } from './ui'
import { displayCode, marketLabel, normalizeInstrumentId } from '../utils/format'

export function SymbolPicker({
  watchlist,
  selected,
  onChange,
  max = 30,
}: {
  watchlist: string[]
  selected: string[]
  onChange: (next: string[]) => void
  max?: number
}) {
  const [mode, setMode] = useState<'watch' | 'search'>('watch')
  const [keyword, setKeyword] = useState('')
  const [results, setResults] = useState<SymbolInfo[]>([])
  const [searching, setSearching] = useState(false)

  const toggle = (symbol: string) => {
    if (selected.includes(symbol)) {
      onChange(selected.filter((item) => item !== symbol))
    } else if (selected.length < max) {
      onChange([...selected, symbol])
    }
  }

  const search = async () => {
    const text = keyword.trim()
    if (!text) return
    setSearching(true)
    try {
      const items = await marketApi.search(text, 12, null)
      setResults(items.filter((item) => !selected.includes(item.symbol)))
    } catch {
      setResults([])
    } finally {
      setSearching(false)
    }
  }

  const addManual = () => {
    const normalized = normalizeInstrumentId(keyword.trim()) ?? keyword.trim().toUpperCase()
    if (normalized && !selected.includes(normalized) && selected.length < max) {
      onChange([...selected, normalized])
      setKeyword('')
    }
  }

  const watchRemaining = useMemo(
    () => watchlist.filter((item) => !selected.includes(item)),
    [watchlist, selected],
  )

  return (
    <div className="col" style={{ gap: 8 }}>
      <div className="row" style={{ gap: 6 }}>
        <button
          type="button"
          className={`btn btn-sm${mode === 'watch' ? ' btn-primary' : ''}`}
          onClick={() => setMode('watch')}
        >
          从自选选择
        </button>
        <button
          type="button"
          className={`btn btn-sm${mode === 'search' ? ' btn-primary' : ''}`}
          onClick={() => setMode('search')}
        >
          搜索添加
        </button>
        <Badge tone={selected.length >= max ? 'warn' : 'neutral'}>
          {selected.length} / {max}
        </Badge>
      </div>

      {mode === 'watch' ? (
        watchlist.length === 0 ? (
          <span className="tiny faint">自选为空：先到「行情」页添加自选，或切换到「搜索添加」。</span>
        ) : (
          <div className="row wrap" style={{ gap: 6 }}>
            {watchlist.map((symbol) => {
              const on = selected.includes(symbol)
              return (
                <button
                  key={symbol}
                  type="button"
                  className={`btn btn-sm${on ? ' btn-primary' : ''}`}
                  onClick={() => toggle(symbol)}
                >
                  {displayCode(symbol)}
                </button>
              )
            })}
            {watchRemaining.length === 0 && <span className="tiny faint">自选已全部加入</span>}
          </div>
        )
      ) : (
        <div className="col" style={{ gap: 6 }}>
          <div className="row" style={{ gap: 6 }}>
            <input
              className="input mono"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  void search()
                }
              }}
              placeholder="代码 / 名称 / CRYPTO:BTC/USDT"
              style={{ flex: 1, minWidth: 180 }}
            />
            <Button size="sm" onClick={() => void search()} loading={searching}>
              搜索
            </Button>
            <Button size="sm" variant="ghost" onClick={addManual} title="按代码直接加入">
              直接加入
            </Button>
          </div>
          {results.length > 0 && (
            <div className="col" style={{ gap: 4 }}>
              {results.map((item) => (
                <button
                  key={item.symbol}
                  type="button"
                  className="btn btn-sm btn-ghost"
                  style={{ justifyContent: 'flex-start' }}
                  onClick={() => {
                    toggle(item.symbol)
                    setResults((prev) => prev.filter((r) => r.symbol !== item.symbol))
                  }}
                >
                  <span className="mono">{displayCode(item.symbol)}</span>
                  <span className="tiny faint">{item.name}</span>
                  <span className="tiny faint" style={{ marginLeft: 'auto' }}>
                    {marketLabel(item.market)}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {selected.length > 0 && (
        <div className="row wrap" style={{ gap: 6 }}>
          {selected.map((symbol) => (
            <span key={symbol} className="badge badge-accent" style={{ gap: 6 }}>
              <span className="mono">{displayCode(symbol)}</span>
              <button
                type="button"
                onClick={() => toggle(symbol)}
                style={{ cursor: 'pointer', lineHeight: 1 }}
                title="移除"
              >
                ✕
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * 条件拼装策略构建器：用下拉框拼「指标/字段 vs 指标/常数」的买卖规则，
 * 提交 JSON spec 给后端白名单校验，全程不需要写代码。
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { strategyApi } from '../api/client'
import type { ConditionSpec, CustomSpec, OperandSpec } from '../api/types'
import { Banner, Button, Field, Input } from './ui'
import { notify } from '../store/toast'

// --------------------------------------------------------------------------- //
// UI 数据模型
// --------------------------------------------------------------------------- //

type UiOperand = {
  key: string // 'field:close' | 'ind:sma' | 'const'
  period: number
  width: number
  fast: number
  slow: number
  signal: number
  value: number
}

type UiCondition = {
  type: string
  left: UiOperand
  right: UiOperand
}

const FIELD_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'close', label: '收盘价' },
  { value: 'open', label: '开盘价' },
  { value: 'high', label: '最高价' },
  { value: 'low', label: '最低价' },
  { value: 'volume', label: '成交量' },
  { value: 'amount', label: '成交额' },
]

const INDICATOR_META: Record<string, { label: string; params: string[] }> = {
  sma: { label: 'SMA 均线', params: ['period'] },
  ema: { label: 'EMA 均线', params: ['period'] },
  rsi: { label: 'RSI 强弱', params: ['period'] },
  std: { label: 'N日标准差', params: ['period'] },
  rolling_high: { label: 'N日最高', params: ['period'] },
  rolling_low: { label: 'N日最低', params: ['period'] },
  momentum: { label: 'N日涨幅(小数)', params: ['period'] },
  atr: { label: 'ATR 波动', params: ['period'] },
  boll_lower: { label: '布林下轨', params: ['period', 'width'] },
  boll_upper: { label: '布林上轨', params: ['period', 'width'] },
  macd_dif: { label: 'MACD DIF', params: ['fast', 'slow', 'signal'] },
  macd_dea: { label: 'MACD DEA', params: ['fast', 'slow', 'signal'] },
}

const CONDITION_TYPES: Array<{ value: string; label: string; title: string }> = [
  { value: 'cross_above', label: '上穿(金叉)', title: '左操作数从下方穿越到右操作数上方' },
  { value: 'cross_below', label: '下穿(死叉)', title: '左操作数从上方穿越到右操作数下方' },
  { value: 'gt', label: '大于', title: '左 > 右' },
  { value: 'gte', label: '大于等于', title: '左 ≥ 右' },
  { value: 'lt', label: '小于', title: '左 < 右' },
  { value: 'lte', label: '小于等于', title: '左 ≤ 右' },
]

const newOperand = (): UiOperand => ({ key: 'field:close', period: 20, width: 2, fast: 12, slow: 26, signal: 9, value: 0 })
const newEntryCondition = (): UiCondition => ({ type: 'cross_above', left: newOperand(), right: { ...newOperand(), key: 'ind:sma', period: 30 } })
const newExitCondition = (): UiCondition => ({ type: 'cross_below', left: newOperand(), right: { ...newOperand(), key: 'ind:sma', period: 30 } })

// --------------------------------------------------------------------------- //
// 序列化 UI → spec
// --------------------------------------------------------------------------- //

function toOperandSpec(op: UiOperand): OperandSpec | null {
  if (op.key.startsWith('field:')) return { kind: 'field', field: op.key.slice('field:'.length) }
  if (op.key === 'const') return { kind: 'const', value: op.value }
  if (op.key.startsWith('ind:')) {
    const name = op.key.slice('ind:'.length)
    const meta = INDICATOR_META[name]
    if (!meta) return null
    const params: Record<string, number> = {}
    for (const p of meta.params) {
      const num = op[p as keyof UiOperand] as number
      if (!Number.isInteger(num) || num < 1) return null
      params[p] = num
    }
    if ('period' in params && (params.period < 2 || params.period > 500)) return null
    if ('width' in params && (params.width < 0.1 || params.width > 10)) return null
    return { kind: 'indicator', name, params }
  }
  return null
}

function toSpec(name: string, conditions: { entry: UiCondition[]; exit: UiCondition[]; positionPct: number; stopLoss: number; takeProfit: number }): CustomSpec | null {
  const serialize = (list: UiCondition[]): ConditionSpec[] | null => {
    const out: ConditionSpec[] = []
    for (const cond of list) {
      const left = toOperandSpec(cond.left)
      const right = toOperandSpec(cond.right)
      if (!left || !right) return null
      out.push({ type: cond.type, left, right })
    }
    return out
  }
  const entry = serialize(conditions.entry)
  const exit = serialize(conditions.exit)
  if (!entry || !exit) return null
  return {
    version: 1,
    description: name,
    category: 'trend',
    entry,
    exit,
    position_pct: conditions.positionPct / 100,
    stop_loss: conditions.stopLoss / 100,
    take_profit: conditions.takeProfit / 100,
  }
}

// --------------------------------------------------------------------------- //
// 展示用摘要（保存后的列表）
// --------------------------------------------------------------------------- //

function operandLabel(op: OperandSpec): string {
  if (op.kind === 'const') return String(op.value)
  if (op.kind === 'field') return FIELD_OPTIONS.find((f) => f.value === op.field)?.label ?? op.field
  const meta = INDICATOR_META[op.name]
  if (!meta) return op.name
  const parts = Object.entries(op.params)
    .map(([k, v]) => `${v}`)
    .join(',')
  return `${meta.label}(${parts})`
}

export function conditionLabel(cond: ConditionSpec): string {
  const typeLabel = CONDITION_TYPES.find((t) => t.value === cond.type)?.label ?? cond.type
  return `${operandLabel(cond.left)} ${typeLabel} ${operandLabel(cond.right)}`
}

// --------------------------------------------------------------------------- //
// 操作数编辑器
// --------------------------------------------------------------------------- //

function OperandEditor({ op, onChange }: { op: UiOperand; onChange: (next: UiOperand) => void }) {
  const isField = op.key.startsWith('field:')
  const isInd = op.key.startsWith('ind:')
  const meta = isInd ? INDICATOR_META[op.key.slice('ind:'.length)] : null

  return (
    <div className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
      <select
        className="select"
        value={op.key}
        onChange={(event) => onChange({ ...op, key: event.target.value })}
        style={{ width: 150 }}
      >
        <optgroup label="价格">
          {FIELD_OPTIONS.map((field) => (
            <option key={field.value} value={`field:${field.value}`}>
              {field.label}
            </option>
          ))}
        </optgroup>
        <optgroup label="指标">
          {Object.entries(INDICATOR_META).map(([key, item]) => (
            <option key={key} value={`ind:${key}`}>
              {item.label}
            </option>
          ))}
        </optgroup>
        <optgroup label="其他">
          <option value="const">常数</option>
        </optgroup>
      </select>
      {isField && (
        <span className="tiny faint">{FIELD_OPTIONS.find((f) => `field:${f.value}` === op.key)?.label}</span>
      )}
      {meta && meta.params.includes('period') && (
        <input
          className="input mono"
          type="number"
          min={2}
          max={500}
          title="周期（2~500）"
          value={op.period}
          onChange={(event) => onChange({ ...op, period: Number(event.target.value) })}
          style={{ width: 74 }}
        />
      )}
      {meta && meta.params.includes('width') && (
        <input
          className="input mono"
          type="number"
          min={0.1}
          max={10}
          step={0.1}
          title="布林宽度"
          value={op.width}
          onChange={(event) => onChange({ ...op, width: Number(event.target.value) })}
          style={{ width: 64 }}
        />
      )}
      {meta?.params.includes('fast') && (
        <>
          <input
            className="input mono"
            type="number"
            title="快线"
            value={op.fast}
            onChange={(event) => onChange({ ...op, fast: Number(event.target.value) })}
            style={{ width: 56 }}
          />
          <input
            className="input mono"
            type="number"
            title="慢线"
            value={op.slow}
            onChange={(event) => onChange({ ...op, slow: Number(event.target.value) })}
            style={{ width: 56 }}
          />
          <input
            className="input mono"
            type="number"
            title="信号线"
            value={op.signal}
            onChange={(event) => onChange({ ...op, signal: Number(event.target.value) })}
            style={{ width: 56 }}
          />
        </>
      )}
      {op.key === 'const' && (
        <input
          className="input mono"
          type="number"
          step="any"
          title="常数（涨幅类指标用小数：0.05 = 5%）"
          value={op.value}
          onChange={(event) => onChange({ ...op, value: Number(event.target.value) })}
          style={{ width: 90 }}
        />
      )}
    </div>
  )
}

function ConditionRow({ cond, onChange, onRemove }: { cond: UiCondition; onChange: (next: UiCondition) => void; onRemove: () => void }) {
  const typeDef = CONDITION_TYPES.find((t) => t.value === cond.type)
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row" style={{ gap: 6, alignItems: 'center' }}>
        <select
          className="select"
          value={cond.type}
          onChange={(event) => onChange({ ...cond, type: event.target.value })}
          title={typeDef?.title}
          style={{ width: 120 }}
        >
          {CONDITION_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onRemove} title="删除这条条件">
          ✕
        </button>
      </div>
      <OperandEditor op={cond.left} onChange={(left) => onChange({ ...cond, left })} />
      <OperandEditor op={cond.right} onChange={(right) => onChange({ ...cond, right })} />
    </div>
  )
}

// --------------------------------------------------------------------------- //
// 构建器主体
// --------------------------------------------------------------------------- //

export function StrategyBuilder({ onSaved }: { onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [entry, setEntry] = useState<UiCondition[]>([newEntryCondition()])
  const [exit, setExit] = useState<UiCondition[]>([newExitCondition()])
  const [positionPct, setPositionPct] = useState(90)
  const [stopLoss, setStopLoss] = useState(8)
  const [takeProfit, setTakeProfit] = useState(0)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!name.trim()) {
      setError('请给策略起个名字')
      return
    }
    const spec = toSpec(name.trim(), { entry, exit, positionPct, stopLoss, takeProfit })
    if (!spec) {
      setError('条件不完整：请检查周期/常数的取值')
      return
    }
    setSaving(true)
    setError('')
    try {
      await strategyApi.createCustom({ name: name.trim(), spec })
      notify('success', `拼装策略「${name.trim()}」已创建，可在回测/纸盘中使用`)
      setName('')
      setOpen(false)
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (!open) {
    return (
      <Button variant="primary" onClick={() => setOpen(true)}>
        ＋ 拼装新策略
      </Button>
    )
  }

  return (
    <form className="col" style={{ gap: 12 }} onSubmit={save}>
      <div className="row" style={{ gap: 12, alignItems: 'flex-end' }}>
        <Field label="策略名称" style={{ flex: '0 0 220px' }}>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="如：双均线金叉" />
        </Field>
        <span className="tiny faint" style={{ flex: 1 }}>
          入场条件需<b>全部满足</b>且空仓才买入；离场条件<b>任一满足</b>即清仓。订单统一在次日开盘成交。
        </span>
      </div>

      {(['entry', 'exit'] as const).map((side) => (
        <div key={side}>
          <div className="label" style={{ marginBottom: 6 }}>
            {side === 'entry' ? '入场条件（全部满足）' : '离场条件（任一满足）'}
          </div>
          <div className="col" style={{ gap: 10 }}>
            {side === 'entry'
              ? entry.map((cond, index) => (
                  <ConditionRow
                    key={index}
                    cond={cond}
                    onChange={(next) => setEntry(entry.map((item, i) => (i === index ? next : item)))}
                    onRemove={() => setEntry(entry.length > 1 ? entry.filter((_, i) => i !== index) : entry)}
                  />
                ))
              : exit.map((cond, index) => (
                  <ConditionRow
                    key={index}
                    cond={cond}
                    onChange={(next) => setExit(exit.map((item, i) => (i === index ? next : item)))}
                    onRemove={() => setExit(exit.length > 1 ? exit.filter((_, i) => i !== index) : exit)}
                  />
                ))}
            <div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => (side === 'entry' ? setEntry([...entry, newEntryCondition()]) : setExit([...exit, newExitCondition()]))}
              >
                ＋ 添加条件
              </Button>
            </div>
          </div>
        </div>
      ))}

      <div className="form-grid-3">
        <Field label="仓位 %" hint="买入动用权益的比例">
          <Input type="number" min={1} max={100} value={positionPct} onChange={(e) => setPositionPct(Number(e.target.value))} />
        </Field>
        <Field label="止损 %" hint="0 = 不启用">
          <Input type="number" min={0} max={95} value={stopLoss} onChange={(e) => setStopLoss(Number(e.target.value))} />
        </Field>
        <Field label="止盈 %" hint="0 = 不启用">
          <Input type="number" min={0} max={500} value={takeProfit} onChange={(e) => setTakeProfit(Number(e.target.value))} />
        </Field>
      </div>

      {error && <Banner kind="danger">{error}</Banner>}

      <div className="row" style={{ gap: 8 }}>
        <Button type="submit" variant="primary" loading={saving}>
          保存策略
        </Button>
        <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
          收起
        </Button>
      </div>
    </form>
  )
}

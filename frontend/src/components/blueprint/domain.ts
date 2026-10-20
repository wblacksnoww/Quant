/** 蓝图节点类型注册表：与后端 quant/strategy/custom_graph.py 的白名单一一对应。
 *
 * 画布上分六组：常数 / 数据 / 指标 / 运算 / 比较 / 逻辑。
 * 端口分两种颜色线：数值（青）与布尔（橙）；常数节点输出灰线（视作数值）。
 */

export type PortKind = 'value' | 'bool'

export interface BpParamDef {
  key: string
  label: string
  def: number
  min: number
  max: number
  step: number
}

export interface BpPortDef {
  key: 'in' | 'left' | 'right'
  label: string
  kind: PortKind
  /** true = 只能接数据源/指标/运算产出的序列（不能接常数） */
  seriesOnly?: boolean
}

export interface BpDef {
  btype: string
  label: string
  group: 'const' | 'data' | 'indicator' | 'arith' | 'compare' | 'logic'
  /** 画布节点提示（palette 与节点 title） */
  tip: string
  inputs: BpPortDef[]
  emit: PortKind
  /** const 节点的默认值 */
  valueDefault?: number
  params?: BpParamDef[]
}

const GROUPS: { key: BpDef['group']; label: string; color: string }[] = [
  { key: 'const', label: '常数', color: '#94a3b8' },
  { key: 'data', label: '数据', color: '#a78bfa' },
  { key: 'indicator', label: '指标', color: '#3b82f6' },
  { key: 'arith', label: '运算', color: '#64748b' },
  { key: 'compare', label: '比较 / 交叉', color: '#f59e0b' },
  { key: 'logic', label: '逻辑', color: '#10b981' },
]

const PERIOD_PARAM: BpParamDef = { key: 'period', label: '周期', def: 20, min: 2, max: 500, step: 1 }

const INDICATOR_DEFS: [string, string, BpParamDef[], string][] = [
  ['sma', '均线 SMA', [PERIOD_PARAM], '收盘价的简单移动平均'],
  ['ema', '均线 EMA', [PERIOD_PARAM], '收盘价的指数移动平均'],
  ['rsi', 'RSI', [PERIOD_PARAM], '强弱指数，0~100，越低越超卖'],
  ['std', '波动 STD', [PERIOD_PARAM], '滚动标准差'],
  ['rolling_high', '区间最高', [PERIOD_PARAM], '过去 N 根的最高价'],
  ['rolling_low', '区间最低', [PERIOD_PARAM], '过去 N 根的最低价'],
  ['momentum', '动量', [PERIOD_PARAM], 'N 根涨跌幅（0.05 = 5%）'],
  ['atr', '真实波幅 ATR', [PERIOD_PARAM], '平均真实波幅'],
  [
    'boll_upper',
    '布林上轨',
    [PERIOD_PARAM, { key: 'width', label: '宽度', def: 2, min: 0.1, max: 10, step: 0.1 }],
    '腰带 = 均线 + N 倍标准差',
  ],
  [
    'boll_mid',
    '布林中轨',
    [PERIOD_PARAM, { key: 'width', label: '宽度', def: 2, min: 0.1, max: 10, step: 0.1 }],
    '腰带均线本身',
  ],
  [
    'boll_lower',
    '布林下轨',
    [PERIOD_PARAM, { key: 'width', label: '宽度', def: 2, min: 0.1, max: 10, step: 0.1 }],
    '腰带 = 均线 - N 倍标准差',
  ],
  [
    'macd_dif',
    'MACD DIF',
    [
      { key: 'fast', label: '快线', def: 12, min: 2, max: 500, step: 1 },
      { key: 'slow', label: '慢线', def: 26, min: 2, max: 500, step: 1 },
      { key: 'signal', label: '信号', def: 9, min: 2, max: 500, step: 1 },
    ],
    '快慢 EMA 之差',
  ],
  [
    'macd_dea',
    'MACD DEA',
    [
      { key: 'fast', label: '快线', def: 12, min: 2, max: 500, step: 1 },
      { key: 'slow', label: '慢线', def: 26, min: 2, max: 500, step: 1 },
      { key: 'signal', label: '信号', def: 9, min: 2, max: 500, step: 1 },
    ],
    'DIF 的信号线',
  ],
  [
    'macd_hist',
    'MACD 柱',
    [
      { key: 'fast', label: '快线', def: 12, min: 2, max: 500, step: 1 },
      { key: 'slow', label: '慢线', def: 26, min: 2, max: 500, step: 1 },
      { key: 'signal', label: '信号', def: 9, min: 2, max: 500, step: 1 },
    ],
    '(DIF - DEA) × 2',
  ],
]

const DATA_DEFS: [string, string, string][] = [
  ['open', '开盘价', '当日开盘价'],
  ['high', '最高价', '当日最高价'],
  ['low', '最低价', '当日最低价'],
  ['close', '收盘价', '当日收盘价（最常用）'],
  ['volume', '成交量', '当日成交量（股）'],
]

function buildDefs(): Record<string, BpDef> {
  const defs: Record<string, BpDef> = {
    const: {
      btype: 'const',
      label: '常数',
      group: 'const',
      tip: '数值常数，可接到任何数值输入',
      inputs: [],
      emit: 'value',
      valueDefault: 0,
    },
  }
  for (const [btype, label, tip] of DATA_DEFS) {
    defs[btype] = { btype, label, group: 'data', tip, inputs: [], emit: 'value' }
  }
  for (const [btype, label, params, tip] of INDICATOR_DEFS) {
    defs[btype] = {
      btype,
      label,
      group: 'indicator',
      tip,
      inputs: [{ key: 'in', label: '价格', kind: 'value', seriesOnly: true }],
      emit: 'value',
      params,
    }
  }
  const two = (kind: PortKind, seriesOnly = false): BpPortDef[] => [
    { key: 'left', label: 'A', kind, seriesOnly },
    { key: 'right', label: 'B', kind, seriesOnly },
  ]
  for (const [btype, label, tip] of [
    ['add', '相加 A+B', '两条数值线相加'],
    ['sub', '相减 A-B', 'A 减 B（如乖离）'],
    ['mul', '相乘 A×B', '两条数值线相乘'],
    ['div', '相除 A÷B', 'A 除以 B（除 0 得空）'],
  ] as [string, string, string][]) {
    defs[btype] = { btype, label, group: 'arith', tip, inputs: two('value'), emit: 'value' }
  }
  for (const [btype, label, tip] of [
    ['gt', '大于 >', 'A 严格大于 B 时为真'],
    ['gte', '大于等于 ≥', 'A 不小于 B 时为真'],
    ['lt', '小于 <', 'A 严格小于 B 时为真'],
    ['lte', '小于等于 ≤', 'A 不大于 B 时为真'],
  ] as [string, string, string][]) {
    defs[btype] = { btype, label, group: 'compare', tip, inputs: two('value'), emit: 'bool' }
  }
  defs['cross_above'] = {
    btype: 'cross_above',
    label: '上穿 ⤴',
    group: 'compare',
    tip: 'A 从下方穿越到 B 上方的当根为真',
    inputs: two('value', true),
    emit: 'bool',
  }
  defs['cross_below'] = {
    btype: 'cross_below',
    label: '下穿 ⤵',
    group: 'compare',
    tip: 'A 从上方跌穿 B 的当根为真',
    inputs: two('value', true),
    emit: 'bool',
  }
  defs['not'] = {
    btype: 'not',
    label: '取反 NOT',
    group: 'logic',
    tip: '布尔取反',
    inputs: [{ key: 'in', label: '信号', kind: 'bool' }],
    emit: 'bool',
  }
  for (const [btype, label, tip] of [
    ['and', '全部满足 AND', '两个布尔都真才真'],
    ['or', '任一满足 OR', '至少一个布尔为真'],
  ] as [string, string, string][]) {
    defs[btype] = { btype, label, group: 'logic', tip, inputs: two('bool'), emit: 'bool' }
  }
  return defs
}

export const BP_DEFS: Record<string, BpDef> = buildDefs()

export const GROUP_META = Object.fromEntries(GROUPS.map((g) => [g.key, g]))

export const EDGE_COLORS: Record<PortKind, string> = { value: '#22d3ee', bool: '#f59e0b' }

/** 画布节点数据（React Flow v12 泛型要求 type alias，便于满足 Record<string, unknown>） */
export type BpNodeData = {
  btype: string
  params: Record<string, number>
  /** 由编辑器注入：在节点内直接改参数 */
  onParams?: (next: Record<string, number>) => void
}

/** 输出哨兵节点数据（买入 / 卖出），只收布尔线 */
export type BpOutData = { btype: 'out_entry' | 'out_exit' }

/** 输出哨兵固定 id（仅画布内使用，不会进 spec） */
export const ENTRY_OUT_ID = 'entry_output_marker'
export const EXIT_OUT_ID = 'exit_output_marker'

export const MAX_CANVAS_NODES = 48

export function defaultParams(def: BpDef): Record<string, number> {
  if (def.btype === 'const') return { value: def.valueDefault ?? 0 }
  const params: Record<string, number> = {}
  for (const p of def.params ?? []) params[p.key] = p.def
  return params
}

/** 生成小写 id：n 开头 + 随机片段（符合后端 ^[a-z0-9_]{1,24}$） */
export function genNodeId(): string {
  const rand = Math.random().toString(36).slice(2, 8)
  return `n${Date.now().toString(36)}${rand}`.slice(0, 20)
}

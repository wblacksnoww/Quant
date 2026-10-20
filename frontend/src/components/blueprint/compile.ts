/** 画布 ↔ spec v2 双向编译（与后端白名单的唯一契约是 JSON，见 custom_graph.py）。 */

import type { Edge, Node } from '@xyflow/react'
import type { CustomGraphSpec } from '../../api/types'
import {
  BP_DEFS,
  EDGE_COLORS,
  ENTRY_OUT_ID,
  EXIT_OUT_ID,
  defaultParams,
  type BpNodeData,
  type BpOutData,
  type PortKind,
} from './domain'

export const ENTRY_OUT_TYPE = 'out_entry'
export const EXIT_OUT_TYPE = 'out_exit'

export function isGraphSpec(spec: unknown): spec is CustomGraphSpec {
  return (
    typeof spec === 'object' &&
    spec !== null &&
    (spec as CustomGraphSpec).version === 2 &&
    Array.isArray((spec as CustomGraphSpec).nodes)
  )
}

export type CompileResult = { spec: CustomGraphSpec; nodes: number } | { errors: string[]; spec: null }

/** 输出哨兵在画布上的默认位置 */
const ENTRY_OUT_POS = { x: 720, y: 90 }
const EXIT_OUT_POS = { x: 720, y: 250 }

export function markerNodes(): Node[] {
  return [
    {
      id: ENTRY_OUT_ID,
      type: 'out',
      position: ENTRY_OUT_POS,
      deletable: false,
      data: { btype: ENTRY_OUT_TYPE } as BpOutData,
    },
    {
      id: EXIT_OUT_ID,
      type: 'out',
      position: EXIT_OUT_POS,
      deletable: false,
      data: { btype: EXIT_OUT_TYPE } as BpOutData,
    },
  ]
}

function nodeParamRecord(data: BpNodeData): Record<string, number> {
  const def = BP_DEFS[data.btype]
  const base = defaultParams(def)
  return {
    ...base,
    ...Object.fromEntries(
      Object.entries(data.params ?? {}).filter(([, v]) => typeof v === 'number' && Number.isFinite(v)),
    ),
  }
}

/** 画布 → spec。结构错误在本地报（与服务端语义一致），服务端仍是权威。 */
export function compileToSpec(
  nodes: Node[],
  edges: Edge[],
  meta: { name: string; positionPct: number; stopLoss: number; takeProfit: number },
): CompileResult {
  const errors: string[] = []
  const bpNodes = nodes.filter((n) => n.type === 'bp')
  const outNodes = nodes.filter((n) => n.type === 'out')

  if (bpNodes.length === 0) errors.push('画布上还没有节点：从左侧把指标拖进来')

  // --- 节点 --- //
  const kindOf = new Map<string, PortKind>()
  const specNodes: CustomGraphSpec['nodes'] = []
  for (const node of bpNodes) {
    const data = node.data as BpNodeData
    const def = BP_DEFS[data.btype]
    if (!def) {
      errors.push(`节点 ${node.id} 类型未知（${data.btype}）`)
      continue
    }
    kindOf.set(node.id, def.emit)
    const item: CustomGraphSpec['nodes'][number] = {
      id: node.id,
      type: data.btype,
      pos: { x: Math.round(node.position.x), y: Math.round(node.position.y) },
    }
    if (def.btype === 'const') {
      const value = Number(data.params?.['value'] ?? def.valueDefault ?? 0)
      if (!Number.isFinite(value)) errors.push(`常数节点 ${node.id} 的值必须是数字`)
      item.value = Number.isFinite(value) ? value : 0
    } else if (def.params && def.params.length > 0) {
      item.params = nodeParamRecord(data)
    }
    specNodes.push(item)
  }

  // --- 连线 --- //
  const specEdges: CustomGraphSpec['edges'] = []
  const filledTarget = new Map<string, string>() // `${target}:${to_port}` -> source
  for (const edge of edges) {
    const { source, target, targetHandle } = edge
    if (!source || !target) continue
    if (target === ENTRY_OUT_ID || target === EXIT_OUT_ID) continue // 哨兵连线不进 spec
    if (source === ENTRY_OUT_ID || source === EXIT_OUT_ID) {
      errors.push('买入/卖出输出节点只能接信号，不能作为连线起点')
      continue
    }
    const targetNode = nodes.find((n) => n.id === target)
    const sourceNode = nodes.find((n) => n.id === source)
    if (!targetNode || !sourceNode) {
      errors.push('连到了已删除的节点，请撤销重连')
      continue
    }
    const tData = targetNode.data as BpNodeData
    const sData = sourceNode.data as BpNodeData
    const tDef = BP_DEFS[tData.btype]
    const sDef = BP_DEFS[sData.btype]
    if (!tDef || !sDef) {
      errors.push(`画布上有未知类型的节点（${tData?.btype ?? '?'} / ${sData?.btype ?? '?'}），请删除重画`)
      continue
    }
    const port = tDef.inputs.find((p) => p.key === (targetHandle ?? 'in'))
    if (!port) {
      errors.push(`${tDef.label} 节点没有输入接口 ${String(targetHandle)}`)
      continue
    }
    const srcKind = sData.btype === 'const' ? 'const' : sDef.emit
    const slotKey = `${target}:${port.key}`
    const previous = filledTarget.get(slotKey)
    if (previous && previous !== source) {
      errors.push(`${tDef.label} 的 ${port.label} 输入被连接了多次，只保留一条`)
      continue
    }
    filledTarget.set(slotKey, source)
    if (port.kind === 'bool' && srcKind !== 'bool') {
      errors.push(`${port.label} 输入需要布尔信号线（比较/逻辑的输出），当前接的是数值线`)
      continue
    }
    if (port.kind === 'value' && port.seriesOnly && srcKind === 'const') {
      errors.push(`${tDef.label} 的价格输入不能接常数，请用收盘价等数据节点`)
      continue
    }
    specEdges.push({ from: source, to: target, to_port: port.key })
  }

  // --- 输入完备性 --- //
  for (const node of bpNodes) {
    const data = node.data as BpNodeData
    const def = BP_DEFS[data.btype]
    if (!def) continue
    for (const port of def.inputs) {
      if (!filledTarget.has(`${node.id}:${port.key}`)) {
        errors.push(`${def.label} 节点的 ${port.label} 输入还没有连线`)
      }
    }
  }

  // --- 输出哨兵 → outputs --- //
  const outSources: Record<'entry' | 'exit', string | null> = { entry: null, exit: null }
  for (const outNode of outNodes) {
    const data = outNode.data as BpOutData
    const incoming = edges.filter((e) => e.target === outNode.id && e.source)
    if (incoming.length === 0) {
      errors.push(
        data.btype === ENTRY_OUT_TYPE
          ? '买入输出还没有连线：把布尔信号接进它'
          : '卖出输出还没有连线：把布尔信号接进它',
      )
      continue
    }
    if (incoming.length > 1) {
      errors.push('买入/卖出输出只能接收一条信号线')
      continue
    }
    const sourceId = incoming[0].source as string
    if (kindOf.get(sourceId) !== 'bool') {
      errors.push(
        data.btype === ENTRY_OUT_TYPE
          ? '买入输出必须接布尔信号（比较/交叉/逻辑）'
          : '卖出输出必须接布尔信号（比较/交叉/逻辑）',
      )
      continue
    }
    outSources[data.btype === ENTRY_OUT_TYPE ? 'entry' : 'exit'] = sourceId
  }
  if (!outSources.entry) errors.push('缺少买入信号：画布右上「买入输出」需要一条布尔连线')
  if (!outSources.exit) errors.push('缺少卖出信号：画布右上「卖出输出」需要一条布尔连线')

  // 布尔 sanity：间接喂给输出的节点里若有未接输入已在上一步报过

  if (errors.length > 0) return { errors, spec: null }

  const positionPct = clamp(meta.positionPct, 0.01, 1, 0.9)
  const stopLoss = clamp(meta.stopLoss, 0, 0.95, 0.08)
  const takeProfit = clamp(meta.takeProfit, 0, 5, 0)

  return {
    spec: {
      version: 2,
      description: meta.name,
      category: 'trend',
      position_pct: positionPct,
      stop_loss: stopLoss,
      take_profit: takeProfit,
      nodes: specNodes,
      edges: specEdges,
      outputs: { entry: outSources.entry as string, exit: outSources.exit as string },
    },
    nodes: specNodes.length,
  }
}

function clamp(v: number, lo: number, hi: number, fallback: number): number {
  if (!Number.isFinite(v)) return fallback
  return Math.min(hi, Math.max(lo, v))
}

/** spec → 画布（编辑已有蓝图 / 套用模板）。 */
/** 起点输出 kind 决定连线颜色：常数=灰、数值=青、布尔=橙。 */
function sourceEdgeColor(btype: string | undefined): string {
  if (btype === 'const') return '#94a3b8'
  return EDGE_COLORS[BP_DEFS[btype ?? '']?.emit ?? 'value']
}

export function graphToCanvas(
  spec: CustomGraphSpec,
  onParams: (nodeId: string) => (next: Record<string, number>) => void,
): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = spec.nodes.map((n, i) => {
    const def = BP_DEFS[n.type]
    const baseParams = def ? defaultParams(def) : {}
    const params: Record<string, number> =
      n.type === 'const'
        ? { value: n.value ?? baseParams['value'] ?? 0 }
        : { ...baseParams, ...(n.params ?? {}) }
    return {
      id: n.id,
      type: 'bp',
      position: n.pos
        ? { x: n.pos.x, y: n.pos.y }
        : { x: 40 + (i % 4) * 170, y: 40 + Math.floor(i / 4) * 130 },
      data: { btype: n.type, params, onParams: onParams(n.id) } as BpNodeData,
    }
  })
  const byType = new Map(spec.nodes.map((n) => [n.id, n.type]))
  const edges: Edge[] = spec.edges.map((e, i) => ({
    id: `se_${i}_${e.from}_${e.to}`,
    source: e.from,
    target: e.to,
    targetHandle: e.to_port ?? 'in',
    style: { stroke: sourceEdgeColor(byType.get(e.from)), strokeWidth: 2 },
  }))
  const markerNodesBuilt = markerNodes()
  const entrySrc = spec.outputs?.entry
  const exitSrc = spec.outputs?.exit
  if (entrySrc)
    edges.push({
      id: 'marker_entry',
      source: entrySrc,
      target: ENTRY_OUT_ID,
      targetHandle: 'in',
      style: { stroke: EDGE_COLORS.bool, strokeWidth: 2 },
    })
  if (exitSrc)
    edges.push({
      id: 'marker_exit',
      source: exitSrc,
      target: EXIT_OUT_ID,
      targetHandle: 'in',
      style: { stroke: EDGE_COLORS.bool, strokeWidth: 2 },
    })
  return { nodes: [...nodes, ...markerNodesBuilt], edges }
}

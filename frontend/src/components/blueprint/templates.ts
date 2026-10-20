/** 蓝图模板：套用即铺好画布节点与连线。坐标为编辑器视觉布局，后端只存不评估。 */

import type { Node, Edge } from '@xyflow/react'
import type { CustomGraphSpec } from '../../api/types'
import type { BpNodeData } from './domain'
import { graphToCanvas } from './compile'

export interface BpTemplate {
  key: string
  name: string
  hint: string
  spec: CustomGraphSpec
}

const MA_SPEC: CustomGraphSpec = {
  version: 2,
  description: '双均线金叉（蓝图）',
  category: 'trend',
  position_pct: 0.9,
  stop_loss: 0.08,
  take_profit: 0,
  nodes: [
    { id: 's1', type: 'close', pos: { x: 20, y: 40 } },
    { id: 's2', type: 'close', pos: { x: 20, y: 210 } },
    { id: 'f1', type: 'sma', params: { period: 5 }, pos: { x: 220, y: 30 } },
    { id: 'f2', type: 'sma', params: { period: 20 }, pos: { x: 220, y: 200 } },
    { id: 'x1', type: 'cross_above', pos: { x: 440, y: 90 } },
    { id: 's3', type: 'close', pos: { x: 20, y: 420 } },
    { id: 's4', type: 'close', pos: { x: 20, y: 590 } },
    { id: 'f3', type: 'sma', params: { period: 5 }, pos: { x: 220, y: 410 } },
    { id: 'f4', type: 'sma', params: { period: 20 }, pos: { x: 220, y: 580 } },
    { id: 'x2', type: 'cross_below', pos: { x: 440, y: 470 } },
  ],
  edges: [
    { from: 's1', to: 'f1', to_port: 'in' },
    { from: 's2', to: 'f2', to_port: 'in' },
    { from: 'f1', to: 'x1', to_port: 'left' },
    { from: 'f2', to: 'x1', to_port: 'right' },
    { from: 's3', to: 'f3', to_port: 'in' },
    { from: 's4', to: 'f4', to_port: 'in' },
    { from: 'f3', to: 'x2', to_port: 'left' },
    { from: 'f4', to: 'x2', to_port: 'right' },
  ],
  outputs: { entry: 'x1', exit: 'x2' },
}

const RSI_SPEC: CustomGraphSpec = {
  version: 2,
  description: 'RSI 超卖抄底（蓝图）',
  category: 'mean_reversion',
  position_pct: 0.5,
  stop_loss: 0.05,
  take_profit: 0.1,
  nodes: [
    { id: 's1', type: 'close', pos: { x: 20, y: 40 } },
    { id: 'r1', type: 'rsi', params: { period: 14 }, pos: { x: 220, y: 30 } },
    { id: 'k1', type: 'const', value: 35, pos: { x: 220, y: 170 } },
    { id: 'g1', type: 'lt', pos: { x: 430, y: 70 } },
    { id: 's2', type: 'close', pos: { x: 20, y: 420 } },
    { id: 'r2', type: 'rsi', params: { period: 14 }, pos: { x: 220, y: 400 } },
    { id: 'k2', type: 'const', value: 70, pos: { x: 220, y: 540 } },
    { id: 'g2', type: 'gt', pos: { x: 430, y: 450 } },
  ],
  edges: [
    { from: 's1', to: 'r1', to_port: 'in' },
    { from: 'r1', to: 'g1', to_port: 'left' },
    { from: 'k1', to: 'g1', to_port: 'right' },
    { from: 's2', to: 'r2', to_port: 'in' },
    { from: 'r2', to: 'g2', to_port: 'left' },
    { from: 'k2', to: 'g2', to_port: 'right' },
  ],
  outputs: { entry: 'g1', exit: 'g2' },
}

export const TEMPLATES: BpTemplate[] = [
  { key: 'ma_cross', name: '双均线金叉', hint: '短均上穿长均买入，下穿卖出', spec: MA_SPEC },
  { key: 'rsi_reversion', name: 'RSI 超卖抄底', hint: 'RSI<35 抄底，RSI>70 落袋', spec: RSI_SPEC },
  {
    key: 'blank',
    name: '空白画布',
    hint: '从数据源开始自己连线',
    spec: {
      version: 2,
      description: '',
      category: 'trend',
      position_pct: 0.9,
      stop_loss: 0.08,
      take_profit: 0,
      nodes: [],
      edges: [],
      outputs: { entry: '', exit: '' },
    },
  },
]

export function templateToCanvas(
  tpl: BpTemplate,
  onParams: (nodeId: string) => (next: Record<string, number>) => void,
) {
  if (tpl.spec.nodes.length === 0) {
    const nodes: Node[] = [
      {
        id: 'seed_close',
        type: 'bp',
        position: { x: 60, y: 140 },
        data: { btype: 'close', params: {} } as BpNodeData,
      },
    ]
    return { nodes, edges: [] as Edge[], spec: tpl.spec }
  }
  const canvas = graphToCanvas(tpl.spec, onParams)
  return { nodes: canvas.nodes, edges: canvas.edges, spec: tpl.spec }
}

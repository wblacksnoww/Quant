/** 蓝图画布节点组件：普通节点（含内联参数输入）与买入/卖出输出哨兵。 */

import { Handle, Position, type NodeProps } from '@xyflow/react'
import { BP_DEFS, EDGE_COLORS, type BpNodeData, type BpOutData } from './domain'

function portColor(kind: 'value' | 'bool'): string {
  return EDGE_COLORS[kind]
}

/** 普通节点：单输入端口在左侧 50%，双输入在 32%/68%；输出端口在右侧 50%。 */
export function BpNode(props: NodeProps) {
  const data = props.data as BpNodeData
  const def = BP_DEFS[data.btype] ?? BP_DEFS['const']
  const groupColor = { const: '#94a3b8', data: '#a78bfa', indicator: '#3b82f6', arith: '#64748b', compare: '#f59e0b', logic: '#10b981' }[def.group]
  const params = def.params ?? []
  const isConst = def.btype === 'const'
  const portTop = (index: number, total: number): string => (total === 1 ? '50%' : `${32 + (index * 36)}%`)
  const rightLabel = isConst ? `值 ${data.params?.['value'] ?? 0}` : def.emit === 'bool' ? '信号' : '数值'

  const handleTop = def.inputs.length === 2 ? '38%' : '50%'
  const handleTop2 = def.inputs.length === 2 ? '62%' : '50%'

  return (
    <div className={`bp-node ${props.selected ? 'bp-node-selected' : ''}`} title={def.tip}>
      <div className="bp-node-head" style={{ background: groupColor }}>
        <span className="bp-node-title">{def.label}</span>
      </div>
      <div className="bp-node-body">
        {isConst && (
          <input
            className="bp-node-input nodrag"
            type="number"
            step="any"
            value={Number.isFinite(Number(data.params?.['value'])) ? Number(data.params['value']) : 0}
            onChange={(ev) => {
              const v = Number(ev.target.value)
              data.onParams?.({ ...data.params, value: Number.isFinite(v) ? v : 0 })
            }}
          />
        )}
        {params.map((p) => (
          <label key={p.key} className="bp-node-field">
            <span>{p.label}</span>
            <input
              className="bp-node-input nodrag"
              type="number"
              min={p.min}
              max={p.max}
              step={p.step}
              value={Number.isFinite(Number(data.params?.[p.key])) ? Number(data.params[p.key]) : p.def}
              onChange={(ev) => {
                const v = Number(ev.target.value)
                data.onParams?.({ ...data.params, [p.key]: Number.isFinite(v) ? v : p.def })
              }}
            />
          </label>
        ))}
        {params.length === 0 && !isConst && <div className="bp-node-hint">{def.tip}</div>}
        <div className="bp-node-out-label tiny faint">{rightLabel}</div>
      </div>
      {def.inputs.map((port, idx) => (
        <Handle
          key={port.key}
          type="target"
          position={Position.Left}
          id={port.key}
          style={{
            top: idx === 0 ? handleTop : handleTop2,
            background: portColor(port.kind),
            borderColor: '#0f172a',
          }}
        />
      ))}
      <Handle
        type="source"
        position={Position.Right}
        id="out"
        style={{ background: portColor(def.emit), borderColor: '#0f172a' }}
      />
    </div>
  )
}

/** 买入/卖出输出哨兵：只接收一条布尔信号，作为编译目标 outputs.entry/exit。 */
export function BpOutNode(props: NodeProps) {
  const data = props.data as BpOutData
  const isEntry = data.btype === 'out_entry'
  return (
    <div className={`bp-out ${isEntry ? 'bp-out-entry' : 'bp-out-exit'} ${props.selected ? 'bp-node-selected' : ''}`}>
      <span className="bp-out-title">{isEntry ? '▶ 买入输出' : '■ 卖出输出'}</span>
      <span className="bp-out-sub tiny">接一条布尔信号线</span>
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        style={{ top: '50%', background: portColor('bool'), borderColor: '#0f172a' }}
      />
    </div>
  )
}

export const NODE_TYPES = { bp: BpNode, out: BpOutNode }

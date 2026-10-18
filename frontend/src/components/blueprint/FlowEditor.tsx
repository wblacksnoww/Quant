/** 蓝图编辑器：React Flow 画布，拖节点、连色线、存成 spec v2。
 *
 *  - 左侧 palette 分组拖拽；画布只有两类哨兵（买入/卖出输出）不可删除。
 *  - 连线即数据流：青色=数值、橙色=布尔（比较/交叉/逻辑的输出）。
 *  - 保存即本地编译为 spec v2 → 服务端白名单复检（422 会回显给用户）。
 */

import { useCallback, useEffect, useRef, useState, type DragEvent as ReactDragEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { ApiError, strategyApi } from '../../api/client'
import type { CustomGraphSpec } from '../../api/types'
import { notify } from '../../store/toast'
import { Button, Input } from '../ui'
import { IconFlask } from '../icons'
import {
  BP_DEFS,
  EDGE_COLORS,
  ENTRY_OUT_ID,
  EXIT_OUT_ID,
  MAX_CANVAS_NODES,
  defaultParams,
  genNodeId,
  type BpNodeData,
  GROUP_META,
} from './domain'
import { NODE_TYPES } from './nodes'
import { compileToSpec, graphToCanvas, isGraphSpec, markerNodes } from './compile'
import { TEMPLATES } from './templates'

const PALETTE_ORDER = ['const', 'data', 'indicator', 'arith', 'compare', 'logic'] as const

// 拖拽 mime：palette → 画布
const BP_MIME = 'application/x-quant-bp'
// 需求上限：画布节点数与后端 MAX_NODES(48) 一致（含 2 个输出哨兵）

export function BlueprintEditor() {
  return (
    <ReactFlowProvider>
      <Editor />
    </ReactFlowProvider>
  )
}

function Editor() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const reactFlow = useReactFlow()
  const editIdParam = searchParams.get('edit')
  const editId = editIdParam ? Number(editIdParam) : null

  const [nodes, setNodes] = useState<Node[]>(() => markerNodes())
  const [edges, setEdges] = useState<Edge[]>([])
  const [name, setName] = useState('')
  const [positionPct, setPositionPct] = useState(0.9)
  const [stopLoss, setStopLoss] = useState(0.08)
  const [takeProfit, setTakeProfit] = useState(0)
  const [busy, setBusy] = useState(false)
  const flowRef = useRef<{ fitView: (opts?: { padding?: number }) => void } | null>(null)

  const paramSetter = useCallback((nodeId: string, next: Record<string, number>) => {
    setNodes((ns) => ns.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, params: next } } : n)))
  }, [])
  const setterFactory = useCallback((nodeId: string) => (next: Record<string, number>) => paramSetter(nodeId, next), [paramSetter])

  // ---- 载入（编辑已有蓝图） ------------------------------------------ //
  useEffect(() => {
    if (!editId) return
    let alive = true
    strategyApi
      .custom()
      .then((list) => {
        if (!alive) return
        const item = list.find((i) => i.id === editId)
        if (!item) {
          notify('error', '找不到该拼装策略')
          navigate('/strategies', { replace: true })
          return
        }
        if (!item.spec || !isGraphSpec(item.spec)) {
          notify('error', '该策略是表单拼装，请在策略库用表单编辑')
          navigate('/strategies', { replace: true })
          return
        }
        const spec: CustomGraphSpec = item.spec
        setName(item.name)
        setPositionPct(spec.position_pct ?? 0.9)
        setStopLoss(spec.stop_loss ?? 0.08)
        setTakeProfit(spec.take_profit ?? 0)
        const canvas = graphToCanvas(spec, setterFactory)
        setNodes(canvas.nodes)
        setEdges(canvas.edges)
        window.setTimeout(() => flowRef.current?.fitView({ padding: 0.25 }), 50)
      })
      .catch((err: Error) => notify('error', err instanceof ApiError ? `载入蓝图失败：${err.message}` : '载入蓝图失败'))
    return () => {
      alive = false
    }
  }, [editId, navigate, setterFactory])

  // ---- 拖放与连线 ------------------------------------------------------ //
  const onDrop = useCallback(
    (ev: ReactDragEvent<HTMLDivElement>) => {
      ev.preventDefault()
      const btype = ev.dataTransfer.getData(BP_MIME)
      const def = BP_DEFS[btype]
      if (!def) return
      setNodes((currentNodes) => {
        if (currentNodes.length >= MAX_CANVAS_NODES) {
          notify('error', `节点已达上限（${MAX_CANVAS_NODES} 个），先删减再添加`)
          return currentNodes
        }
        const id = genNodeId()
        const position = reactFlow.screenToFlowPosition({ x: ev.clientX, y: ev.clientY })
        const next: Node = {
          id,
          type: 'bp',
          position,
          data: { btype, params: defaultParams(def), onParams: (nextParams) => paramSetter(id, nextParams) } as BpNodeData,
        }
        return [...currentNodes, next]
      })
    },
    [reactFlow, paramSetter],
  )

  const onDragOver = useCallback((ev: ReactDragEvent<HTMLDivElement>) => {
    ev.preventDefault()
    ev.dataTransfer.dropEffect = 'move'
  }, [])

  const onConnect = useCallback(
    (connection: Connection) => {
      const { source, target, targetHandle } = connection
      if (!source || !target) return
      const sourceNode = nodes.find((n) => n.id === source)
      const targetNode = nodes.find((n) => n.id === target)
      if (!sourceNode || !targetNode) return
      const sData = sourceNode.data as BpNodeData
      const sDef = BP_DEFS[sData.btype]
      if (!sDef) return

      // 输出哨兵：只收布尔
      if (target === ENTRY_OUT_ID || target === EXIT_OUT_ID) {
        if (sDef.emit !== 'bool') {
          notify('error', '买入/卖出输出只能接布尔信号线（比较/交叉/逻辑的输出）')
          return
        }
        setEdges((eds) => {
          const cleaned = eds.filter((e) => e.target !== target)
          return [...cleaned, { id: `${target}_in`, source, target, targetHandle: 'in', style: { stroke: EDGE_COLORS.bool, strokeWidth: 2 } }]
        })
        return
      }
      const tData = targetNode.data as BpNodeData
      const tDef = BP_DEFS[tData.btype]
      if (!tDef) return
      const port = tDef.inputs.find((p) => p.key === targetHandle)
      if (!port) {
        notify('error', `${tDef.label} 没有输入端口 ${String(targetHandle)}`)
        return
      }
      if (sDef.emit === 'bool' && port.kind !== 'bool') {
        notify('error', `${sDef.label} 输出的是布尔信号，不能接到数值输入`)
        return
      }
      if (port.kind === 'bool' && sDef.emit !== 'bool') {
        notify('error', `${port.label} 输入需要布尔信号线，先经过比较/逻辑节点`)
        return
      }
      if (port.kind === 'value' && port.seriesOnly && sData.btype === 'const') {
        notify('error', `${tDef.label} 的 ${port.label} 输入不能接常数，请用收盘价等数据节点`)
        return
      }
      setEdges((eds) => {
        // 同一端口只保留一条线（后端亦如此要求）
        const rest = eds.filter((e) => !(e.target === target && e.targetHandle === port.key))
        return [
          ...rest,
          {
            id: `c_${source}_${target}_${port.key}`,
            source,
            target,
            targetHandle: port.key,
            style: { stroke: port.kind === 'bool' ? EDGE_COLORS.bool : EDGE_COLORS.value, strokeWidth: 2 },
          },
        ]
      })
    },
    [nodes],
  )

  const onNodesChange = useCallback((changes: NodeChange<Node>[]) => {
    setNodes((ns) => applyNodeChanges(changes, ns))
  }, [])
  const onEdgesChange = useCallback((changes: EdgeChange<Edge>[]) => {
    setEdges((es) => applyEdgeChanges(changes, es))
  }, [])

  // ---- 模板与持久化 ---------------------------------------------------- //
  const applyTemplate = useCallback(
    (key: string) => {
      const tpl = TEMPLATES.find((t) => t.key === key)
      if (!tpl) return
      if (nodes.some((n) => n.type === 'bp') && !window.confirm('用模板替换当前画布？未保存的修改会丢失。')) return
      const canvas = tpl.key === 'blank' ? { nodes: [] as Node[], edges: [] as Edge[] } : graphToCanvas(tpl.spec, setterFactory)
      setName(tpl.spec.description && tpl.spec.description.length > 0 ? tpl.spec.description : '')
      setPositionPct(tpl.spec.position_pct ?? 0.9)
      setStopLoss(tpl.spec.stop_loss ?? 0.08)
      setTakeProfit(tpl.spec.take_profit ?? 0)
      setNodes([...canvas.nodes, ...markerNodes()])
      setEdges(canvas.edges)
      window.setTimeout(() => flowRef.current?.fitView({ padding: 0.25 }), 50)
      notify('success', `已套用模板「${tpl.name}」`)
    },
    [nodes, setterFactory],
  )

  const save = useCallback(async () => {
    const trimmed = name.trim()
    if (!trimmed) {
      notify('error', '先给策略起个名字')
      return
    }
    const result = compileToSpec(nodes, edges, { name: trimmed, positionPct, stopLoss, takeProfit })
    if (!result.spec) {
      notify('error', `画布还不能保存：${result.errors.join('；')}`)
      return
    }
    setBusy(true)
    try {
      if (editId) {
        await strategyApi.updateCustom(editId, { name: trimmed, spec: result.spec })
      } else {
        await strategyApi.createCustom({ name: trimmed, spec: result.spec })
      }
      notify('success', editId ? '蓝图已更新' : '蓝图已保存，可去回测验证')
      navigate('/strategies')
    } catch (err) {
      const message = err instanceof ApiError ? err.message : '保存失败'
      notify('error', `保存失败：${message}`)
    } finally {
      setBusy(false)
    }
  }, [editId, edges, name, navigate, nodes, positionPct, stopLoss, takeProfit])

  const bpCount = nodes.filter((n) => n.type === 'bp').length

  return (
    <div className="bp-page">
      <div className="bp-header">
        <div className="row" style={{ gap: 10, flex: 1, minWidth: 0 }}>
          <Link to="/strategies" className="btn btn-sm btn-ghost">
            ‹ 返回
          </Link>
          <div className="bp-name-wrap">
            <Input value={name} onChange={(ev) => setName(ev.target.value)} placeholder="策略名称，如：双均线金叉" maxLength={32} />
          </div>
          <label className="bp-meta">
            仓位
            <input
              type="number"
              min={1}
              max={100}
              step={5}
              value={Math.round(positionPct * 100)}
              onChange={(ev) => setPositionPct(clampParam(Number(ev.target.value) / 100, 0.01, 1, 0.9))}
            />
            %
          </label>
          <label className="bp-meta">
            止损
            <input
              type="number"
              min={0}
              max={95}
              step={1}
              value={Math.round(stopLoss * 100)}
              onChange={(ev) => setStopLoss(clampParam(Number(ev.target.value) / 100, 0, 0.95, 0.08))}
            />
            %
          </label>
          <label className="bp-meta">
            止盈
            <input
              type="number"
              min={0}
              max={500}
              step={5}
              value={Math.round(takeProfit * 100)}
              onChange={(ev) => setTakeProfit(clampParam(Number(ev.target.value) / 100, 0, 5, 0))}
            />
            %
          </label>
          <span className="tiny faint bp-count">{bpCount}/{MAX_CANVAS_NODES} 节点</span>
        </div>
        <div className="row" style={{ gap: 6 }}>
          {TEMPLATES.map((tpl) => (
            <Button key={tpl.key} size="sm" variant="ghost" title={tpl.hint} onClick={() => applyTemplate(tpl.key)}>
              {tpl.name}
            </Button>
          ))}
          <Button size="sm" variant="primary" onClick={() => void save()} disabled={busy}>
            <IconFlask /> {editId ? '保存蓝图' : '保存到策略库'}
          </Button>
        </div>
      </div>
      <div className="bp-body">
        <aside className="bp-palette">
          <div className="bp-palette-title tiny faint">拖到画布开始搭建</div>
          {PALETTE_ORDER.map((group) => (
            <div key={group} className="bp-palette-group">
              <div className="bp-palette-group-title tiny" style={{ color: GROUP_META[group].color }}>
                {GROUP_META[group].label}
              </div>
              {Object.values(BP_DEFS)
                .filter((def) => def.group === group)
                .map((def) => (
                  <div
                    key={def.btype}
                    className="bp-palette-item"
                    title={def.tip}
                    draggable
                    onDragStart={(ev) => {
                      ev.dataTransfer.setData(BP_MIME, def.btype)
                      ev.dataTransfer.effectAllowed = 'move'
                    }}
                  >
                    <span className="bp-dot" style={{ background: GROUP_META[group].color }} />
                    {def.label}
                  </div>
                ))}
            </div>
          ))}
          <div className="bp-palette-legend tiny faint">
            <span>
              <i className="bp-dot" style={{ background: EDGE_COLORS.value }} /> 数值线
            </span>
            <span>
              <i className="bp-dot" style={{ background: EDGE_COLORS.bool }} /> 布尔线
            </span>
            <span>Delete 删除选中的节点/连线</span>
          </div>
        </aside>
        <div className="bp-canvas" onDrop={onDrop} onDragOver={onDragOver}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            snapToGrid
            snapGrid={[8, 8]}
            defaultEdgeOptions={{ type: 'smoothstep', style: { strokeWidth: 2 } }}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            minZoom={0.25}
            maxZoom={2}
            deleteKeyCode={['Backspace', 'Delete']}
            onInit={(instance) => {
              flowRef.current = instance as unknown as { fitView: (o?: { padding?: number }) => void }
            }}
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
          {nodes.filter((n) => n.type === 'bp').length === 0 && (
            <div className="bp-empty small faint">
              把左侧「收盘价」「均线」「上穿」拖进来连线，最后把布尔信号接到右上「买入输出」。
              <br />
              想快些就从顶部模板开始。
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function clampParam(v: number, lo: number, hi: number, fallback: number): number {
  if (!Number.isFinite(v)) return fallback
  return Math.min(hi, Math.max(lo, v))
}

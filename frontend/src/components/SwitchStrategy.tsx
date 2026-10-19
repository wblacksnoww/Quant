/** 运行中切换策略：选择新策略与参数，可选撤销旧策略挂单。 */

import { useEffect, useMemo, useState } from 'react'
import { ApiError, liveApi, paperApi, strategyApi } from '../api/client'
import type { CustomStrategy, Strategy } from '../api/types'
import { Button, Field, Modal } from './ui'
import { cleanParams, defaultsFor } from '../utils/strategy'
import { notify } from '../store/toast'

export function SwitchStrategyModal({
  open,
  onClose,
  kind,
  current,
  onDone,
}: {
  open: boolean
  onClose: () => void
  kind: 'paper' | 'live'
  current?: string
  onDone?: () => void
}) {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [customs, setCustoms] = useState<CustomStrategy[]>([])
  const [strategyId, setStrategyId] = useState('')
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [customId, setCustomId] = useState<number | null>(null)
  const [cancelOrders, setCancelOrders] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setError('')
    void (async () => {
      const [listR, customR] = await Promise.allSettled([
        strategyApi.list(),
        strategyApi.custom(),
      ])
      if (listR.status === 'fulfilled') {
        setStrategies(listR.value)
        const first = listR.value.find((item) => item.id === 'dual_ma') ?? listR.value[0]
        if (first) {
          setStrategyId(first.id)
          setParams(defaultsFor(first))
        }
      }
      if (customR.status === 'fulfilled') {
        const enabled = customR.value.filter((item) => item.enabled)
        setCustoms(enabled)
        setCustomId(enabled[0]?.id ?? null)
      }
    })()
  }, [open])

  const strategy = useMemo(
    () => strategies.find((item) => item.id === strategyId),
    [strategies, strategyId],
  )

  const changeStrategy = (id: string) => {
    setStrategyId(id)
    setParams(defaultsFor(strategies.find((item) => item.id === id)))
  }

  const submit = async () => {
    setError('')
    if (strategyId === 'custom_rule' && !customId) {
      setError('请选择要运行的自定义策略')
      return
    }
    const payload = cleanParams(strategy, params)
    if (strategyId === 'custom_rule' && customId) payload.custom_strategy_id = customId
    const body = {
      strategy_id: strategyId,
      params: payload,
      cancel_open_orders: cancelOrders,
    }
    setBusy(true)
    try {
      const result =
        kind === 'paper'
          ? await paperApi.switchStrategy(body)
          : await liveApi.switchStrategy(body)
      notify('success', result.message)
      onClose()
      onDone?.()
    } catch (err) {
      const message = err instanceof ApiError ? err.message : '策略切换失败'
      setError(message)
      notify('error', message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      title={`切换${kind === 'paper' ? '模拟盘' : '实盘'}策略`}
      onClose={onClose}
      width={520}
      footer={
        <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button loading={busy} onClick={() => void submit()}>
            确认切换
          </Button>
        </div>
      }
    >
      <div className="col" style={{ gap: 12 }}>
        {current && (
          <div className="tiny faint">
            当前策略：<span className="mono">{current}</span> · 切换在下一轮行情推进时生效
          </div>
        )}
        {error && <div className="banner banner-danger">{error}</div>}

        <Field label="新策略">
          <select
            className="select"
            value={strategyId}
            onChange={(event) => changeStrategy(event.target.value)}
          >
            {strategies.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}（{item.id}）
              </option>
            ))}
          </select>
        </Field>

        {strategyId === 'custom_rule' && (
          <Field label="自定义策略" hint={customs.length === 0 ? '还没有可用策略' : ''}>
            <select
              className="select"
              value={customId ?? ''}
              onChange={(event) => setCustomId(event.target.value ? Number(event.target.value) : null)}
            >
              <option value="">— 请选择 —</option>
              {customs.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
        )}

        {(strategy?.params ?? [])
          .filter((spec) => spec.type !== 'json')
          .map((spec) => (
            <Field key={spec.name} label={spec.description || spec.name}>
              {spec.type === 'bool' ? (
                <input
                  type="checkbox"
                  checked={Boolean(params[spec.name])}
                  onChange={(event) =>
                    setParams((prev) => ({ ...prev, [spec.name]: event.target.checked }))
                  }
                />
              ) : spec.type === 'choice' ? (
                <select
                  className="select"
                  value={String(params[spec.name] ?? '')}
                  onChange={(event) =>
                    setParams((prev) => ({ ...prev, [spec.name]: event.target.value }))
                  }
                >
                  {(spec.choices ?? []).map((choice) => (
                    <option key={String(choice)} value={String(choice)}>
                      {String(choice)}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  className="input mono"
                  type={spec.type === 'int' || spec.type === 'float' ? 'number' : 'text'}
                  step={spec.step ?? (spec.type === 'int' ? 1 : 'any')}
                  min={spec.min ?? undefined}
                  max={spec.max ?? undefined}
                  value={String(params[spec.name] ?? '')}
                  onChange={(event) =>
                    setParams((prev) => ({ ...prev, [spec.name]: event.target.value }))
                  }
                />
              )}
            </Field>
          ))}

        <label className="row" style={{ gap: 8, alignItems: 'center' }}>
          <input
            type="checkbox"
            checked={cancelOrders}
            onChange={(event) => setCancelOrders(event.target.checked)}
          />
          <span className="small">撤销旧策略的未成交挂单（推荐）</span>
        </label>
        <div className="tiny faint">
          持仓与资金保持不变，新策略接管后从当前账户状态继续；旧策略的内部状态不会继承。
        </div>
      </div>
    </Modal>
  )
}

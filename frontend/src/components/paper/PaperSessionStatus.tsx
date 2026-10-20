/** 纸盘会话状态卡片。 */

import type { PaperState } from '../../api/types'
import { Badge, Banner, Button, Card, Empty, StatusBadge } from '../ui'
import { IconRefresh, IconStop } from '../icons'
import { fmtDateTime, fmtMoney } from '../../utils/format'

export function PaperSessionStatus({
  state,
  stopping,
  togglingPause,
  onStop,
  onTogglePause,
  onSwitch,
  onRefresh,
}: {
  state: PaperState | null
  stopping: boolean
  togglingPause: boolean
  onStop: (runId?: string) => void
  onTogglePause: (runId: string, paused: boolean) => void
  onSwitch: () => void
  onRefresh: () => void
}) {
  return (
    <Card
      title="会话状态"
      actions={
        <Button size="sm" variant="ghost" onClick={onRefresh}>
          <IconRefresh />
        </Button>
      }
    >
      {state ? (
        <div className="col" style={{ gap: 12 }}>
          <div className="row between">
            <StatusBadge status={state.status} />
            <Badge tone="accent">{state.mode === 'replay' ? '回放' : '实时'}</Badge>
          </div>
          <dl className="kv">
            <dt>运行 ID</dt>
            <dd className="mono tiny">{state.run_id}</dd>
            <dt>策略</dt>
            <dd>{state.strategy_id}</dd>
            <dt>标的</dt>
            <dd className="mono">{state.symbols.join(', ')}</dd>
            <dt>启动时间</dt>
            <dd>{fmtDateTime(state.started_at)}</dd>
            <dt>已推进</dt>
            <dd>{state.steps} 步</dd>
            <dt>最后价格</dt>
            <dd className="mono tiny">
              {Object.entries(state.last_prices)
                .map(([symbol, price]) => `${symbol} ${fmtMoney(price)}`)
                .join(' · ') || '—'}
            </dd>
          </dl>
          {state.error && <Banner kind="danger">{state.error}</Banner>}
          {(state.status === 'running' || state.status === 'paused') && (
            <>
              {state.status === 'running' && (
                <Button block onClick={onSwitch}>
                  切换策略（下一轮生效）
                </Button>
              )}
              <Button
                block
                loading={togglingPause}
                onClick={() => onTogglePause(state.run_id, state.status === 'paused')}
              >
                {state.status === 'paused' ? '继续运行' : '暂停（保留进度）'}
              </Button>
              <Button variant="danger" block loading={stopping} onClick={() => onStop(state?.run_id)}>
                <IconStop /> 停止当前会话（结束）
              </Button>
            </>
          )}
        </div>
      ) : (
        <Empty title="尚未启动" hint="左侧配置策略与标的，启动后这里显示实时状态。" />
      )}
    </Card>
  )
}

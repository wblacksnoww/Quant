/** 纸盘实时结果：权益、持仓、挂单、最近成交。 */

import type { PaperState } from '../../api/types'
import { EquityChart } from '../Charts'
import { Card, Empty, Stat, StatusBadge } from '../ui'
import { cx, fmtDateTime, fmtMoney, fmtSignedPct } from '../../utils/format'

export function PaperSessionResults({ state }: { state: PaperState }) {
  return (
    <>
      <div className="grid grid-4">
        <Stat
          label="总权益"
          value={fmtMoney(state.equity)}
          sub={`现金 ${fmtMoney(state.cash)}`}
          tone={state.total_return >= 0 ? 'up' : 'down'}
        />
        <Stat
          label="总收益率"
          value={fmtSignedPct(state.total_return)}
          sub={`冻结 ${fmtMoney(state.frozen_cash)}`}
          tone={state.total_return >= 0 ? 'up' : 'down'}
        />
        <Stat
          label="持仓数量"
          value={`${state.positions.length} 只`}
          sub={`挂单 ${state.open_orders.length} 笔`}
        />
        <Stat label="成交笔数" value={`${state.recent_fills.length} 笔`} sub={`已推进 ${state.steps} 步`} />
      </div>

      <Card title="实时权益曲线" description="最近 500 个快照">
        <EquityChart points={state.equity_curve} height={320} showDrawdown={false} />
      </Card>

      <div className="grid grid-2">
        <Card title="持仓" flush>
          {state.positions.length === 0 ? (
            <Empty title="空仓" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>代码</th>
                    <th className="right">数量</th>
                    <th className="right">可用</th>
                    <th className="right">成本</th>
                    <th className="right">现价</th>
                    <th className="right">浮动盈亏</th>
                  </tr>
                </thead>
                <tbody>
                  {state.positions.map((position) => (
                    <tr key={position.symbol}>
                      <td className="strong mono">{position.symbol}</td>
                      <td className="right num">{position.quantity.toLocaleString('zh-CN')}</td>
                      <td className="right num">{position.available.toLocaleString('zh-CN')}</td>
                      <td className="right num">{fmtMoney(position.avg_cost)}</td>
                      <td className="right num">{fmtMoney(position.last_price)}</td>
                      <td className={cx('right num', position.unrealized_pnl >= 0 ? 'up' : 'down')}>
                        {fmtMoney(position.unrealized_pnl)}（{fmtSignedPct(position.unrealized_pct)}）
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="挂单" flush>
          {state.open_orders.length === 0 ? (
            <Empty title="没有未完成委托" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>代码</th>
                    <th>方向</th>
                    <th className="right">数量</th>
                    <th className="right">限价</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {state.open_orders.map((order) => (
                    <tr key={order.id}>
                      <td className="tiny faint">{fmtDateTime(order.created_at)}</td>
                      <td className="strong mono">{order.symbol}</td>
                      <td className={order.side === 'buy' ? 'up' : 'down'}>
                        {order.side === 'buy' ? '买入' : '卖出'}
                      </td>
                      <td className="right num">{order.quantity.toLocaleString('zh-CN')}</td>
                      <td className="right num">
                        {order.limit_price ? fmtMoney(order.limit_price) : '市价'}
                      </td>
                      <td>
                        <StatusBadge status={order.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <Card title="最近成交" description="最多展示 50 笔" flush>
        {state.recent_fills.length === 0 ? (
          <Empty title="还没有成交" hint="策略触发信号后，成交会实时出现在这里。" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>代码</th>
                  <th>方向</th>
                  <th className="right">数量</th>
                  <th className="right">价格</th>
                  <th className="right">金额</th>
                  <th className="right">费用</th>
                  <th className="right">已实现盈亏</th>
                </tr>
              </thead>
              <tbody>
                {[...state.recent_fills].reverse().map((fill) => (
                  <tr key={fill.id}>
                    <td className="tiny faint nowrap">{fmtDateTime(fill.dt)}</td>
                    <td className="strong mono">{fill.symbol}</td>
                    <td className={fill.side === 'buy' ? 'up' : 'down'}>
                      {fill.side === 'buy' ? '买入' : '卖出'}
                    </td>
                    <td className="right num">{fill.quantity.toLocaleString('zh-CN')}</td>
                    <td className="right num">{fmtMoney(fill.price)}</td>
                    <td className="right num">{fmtMoney(fill.gross)}</td>
                    <td className="right num tiny">{fmtMoney(fill.fees.total)}</td>
                    <td
                      className={cx(
                        'right num',
                        fill.realized_pnl > 0 ? 'up' : fill.realized_pnl < 0 ? 'down' : '',
                      )}
                    >
                      {fill.realized_pnl ? fmtMoney(fill.realized_pnl) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}

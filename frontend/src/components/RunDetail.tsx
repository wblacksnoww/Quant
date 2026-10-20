/** 运行结果详情（回测 / 模拟盘共用）：指标卡、权益曲线与成交/委托/持仓/日志。 */

import { useState } from 'react'
import type { RunDetail } from '../api/types'
import { EquityChart, MonthlyReturnChart } from './Charts'
import { Card, Empty, Stat, StatusBadge, Tabs } from './ui'
import {
  cx,
  fmtDateTime,
  fmtMoney,
  fmtNumber,
  fmtPct,
  fmtSignedPct,
} from '../utils/format'

type ResultTab = 'trades' | 'orders' | 'positions' | 'logs' | 'config'

export function RunMetrics({ detail }: { detail: RunDetail }) {
  const metrics = detail.metrics ?? {}
  const totalRealized = detail.trades.reduce((sum, trade) => sum + trade.realized_pnl, 0)
  return (
    <div className="grid grid-4">
      <Stat
        label="总收益率"
        value={fmtSignedPct(metrics.total_return)}
        sub={`年化 ${fmtSignedPct(metrics.annual_return)}`}
        tone={Number(metrics.total_return ?? 0) >= 0 ? 'up' : 'down'}
      />
      <Stat
        label="夏普 / 索提诺"
        value={`${fmtNumber(metrics.sharpe, 2)} / ${fmtNumber(metrics.sortino, 2)}`}
        sub={`波动率 ${fmtPct(metrics.volatility)}`}
      />
      <Stat
        label="最大回撤"
        value={fmtPct(metrics.max_drawdown)}
        sub={`${metrics.max_drawdown_days ?? 0} 个交易日`}
        tone="down"
      />
      <Stat
        label="最终权益"
        value={fmtMoney(metrics.final_equity)}
        sub={`初始 ${fmtMoney(metrics.initial_equity)}`}
      />
      <Stat
        label="胜率"
        value={fmtPct(metrics.win_rate)}
        sub={`${metrics.win_count ?? 0} 胜 / ${metrics.loss_count ?? 0} 负`}
      />
      <Stat
        label="盈亏比"
        value={fmtNumber(metrics.profit_factor, 2)}
        sub={`期望 ${fmtMoney(metrics.expectancy)} / 笔`}
      />
      <Stat
        label="成交笔数"
        value={fmtNumber(metrics.trade_count, 0)}
        sub={`换手 ${fmtNumber(metrics.turnover)} · 暴露 ${fmtPct(metrics.exposure)}`}
      />
      <Stat
        label="总费用"
        value={fmtMoney(metrics.total_fees)}
        sub={`已实现盈亏 ${fmtMoney(totalRealized)}`}
      />
    </div>
  )
}

export function RunDetailBody({ detail }: { detail: RunDetail }) {
  const [tab, setTab] = useState<ResultTab>('trades')
  const metrics = detail.metrics ?? {}
  return (
    <>
      <Card title="权益曲线" description="上方总权益，下方回撤；滚轮或拖拽缩放">
        <EquityChart points={detail.equity_curve} height={320} />
      </Card>

      <div className="grid grid-2">
        <Card title="月度收益">
          <MonthlyReturnChart returns={metrics.monthly_returns ?? {}} height={200} />
        </Card>
        <Card title="交易统计" flush>
          <div className="table-wrap">
            <table>
              <tbody>
                <tr>
                  <td>总收益率</td>
                  <td className={cx('right num', Number(metrics.total_return ?? 0) >= 0 ? 'up' : 'down')}>
                    {fmtSignedPct(metrics.total_return)}
                  </td>
                </tr>
                <tr><td>年化收益</td><td className="right num">{fmtSignedPct(metrics.annual_return)}</td></tr>
                <tr><td>卡玛比率</td><td className="right num">{fmtNumber(metrics.calmar, 2)}</td></tr>
                <tr>
                  <td>平均盈利 / 平均亏损</td>
                  <td className="right num">{fmtMoney(metrics.avg_win)} / {fmtMoney(metrics.avg_loss)}</td>
                </tr>
                <tr>
                  <td>基准收益 / 超额</td>
                  <td className="right num">{fmtSignedPct(metrics.benchmark_return)} / {fmtSignedPct(metrics.excess_return)}</td>
                </tr>
                <tr>
                  <td>回测区间</td>
                  <td className="right num tiny">{metrics.start ?? '—'} → {metrics.end ?? '—'}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card
        title="明细"
        actions={
          <Tabs<ResultTab>
            value={tab}
            onChange={setTab}
            options={[
              { value: 'trades', label: `成交 ${detail.trades.length}` },
              { value: 'orders', label: `委托 ${detail.orders.length}` },
              { value: 'positions', label: `持仓 ${detail.positions.length}` },
              { value: 'logs', label: `日志 ${detail.logs.length}` },
              { value: 'config', label: '配置' },
            ]}
          />
        }
        flush
      >
        {tab === 'trades' && (
          detail.trades.length === 0 ? (
            <Empty title="没有成交" hint="策略可能未触发信号，或订单被风控拒绝（见委托页）。" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>成交时间</th>
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
                  {detail.trades.map((trade) => (
                    <tr key={trade.id}>
                      <td className="tiny faint nowrap">{fmtDateTime(trade.dt)}</td>
                      <td className="strong mono">{trade.symbol}</td>
                      <td>
                        <span className={trade.side === 'buy' ? 'up' : 'down'}>
                          {trade.side === 'buy' ? '买入' : '卖出'}
                        </span>
                      </td>
                      <td className="right num">{trade.quantity.toLocaleString('zh-CN')}</td>
                      <td className="right num">{fmtMoney(trade.price)}</td>
                      <td className="right num">{fmtMoney(trade.gross)}</td>
                      <td className="right num tiny">{fmtMoney(trade.fees.total)}</td>
                      <td className={cx('right num', trade.realized_pnl > 0 ? 'up' : trade.realized_pnl < 0 ? 'down' : '')}>
                        {trade.realized_pnl ? fmtMoney(trade.realized_pnl) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}

        {tab === 'orders' && (
          detail.orders.length === 0 ? (
            <Empty title="没有委托" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>创建时间</th>
                    <th>代码</th>
                    <th>方向</th>
                    <th>类型</th>
                    <th className="right">数量</th>
                    <th className="right">成交价</th>
                    <th>状态</th>
                    <th>拒绝原因</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.orders.map((order) => (
                    <tr key={order.id}>
                      <td className="tiny faint nowrap">{fmtDateTime(order.created_at)}</td>
                      <td className="strong mono">{order.symbol}</td>
                      <td className={order.side === 'buy' ? 'up' : 'down'}>
                        {order.side === 'buy' ? '买入' : '卖出'}
                      </td>
                      <td className="tiny">{order.order_type}</td>
                      <td className="right num">{order.quantity.toLocaleString('zh-CN')}</td>
                      <td className="right num">
                        {order.avg_fill_price ? fmtMoney(order.avg_fill_price) : order.limit_price ? fmtMoney(order.limit_price) : '—'}
                      </td>
                      <td><StatusBadge status={order.status} /></td>
                      <td className="tiny faint truncate" style={{ maxWidth: 220 }} title={order.reason}>
                        {order.reason || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}

        {tab === 'positions' && (
          detail.positions.length === 0 ? (
            <Empty title="期末无持仓" hint="运行结束时所有仓位已平掉。" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>代码</th>
                    <th className="right">数量</th>
                    <th className="right">可用</th>
                    <th className="right">成本价</th>
                    <th className="right">最新价</th>
                    <th className="right">市值</th>
                    <th className="right">浮动盈亏</th>
                    <th className="right">已实现</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.positions.map((position) => (
                    <tr key={position.symbol}>
                      <td className="strong mono">{position.symbol}</td>
                      <td className="right num">{position.quantity.toLocaleString('zh-CN')}</td>
                      <td className="right num">{position.available.toLocaleString('zh-CN')}</td>
                      <td className="right num">{fmtMoney(position.avg_cost)}</td>
                      <td className="right num">{fmtMoney(position.last_price)}</td>
                      <td className="right num">{fmtMoney(position.market_value)}</td>
                      <td className={cx('right num', position.unrealized_pnl >= 0 ? 'up' : 'down')}>
                        {fmtMoney(position.unrealized_pnl)}（{fmtSignedPct(position.unrealized_pct)}）
                      </td>
                      <td className={cx('right num', position.realized_pnl >= 0 ? 'up' : 'down')}>
                        {fmtMoney(position.realized_pnl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}

        {tab === 'logs' && (
          detail.logs.length === 0 ? (
            <Empty title="本次运行没有日志" hint="引擎只在异常或重要节点记录日志。" />
          ) : (
            <div className="table-wrap" style={{ maxHeight: 420 }}>
              <table>
                <thead>
                  <tr><th>时间</th><th>级别</th><th>内容</th></tr>
                </thead>
                <tbody>
                  {detail.logs.map((log, index) => (
                    <tr key={`${log.dt ?? log.time ?? index}-${index}`}>
                      <td className="tiny faint nowrap">{fmtDateTime(String(log.dt ?? log.time ?? ''))}</td>
                      <td className="tiny">{String(log.level ?? 'info')}</td>
                      <td className="tiny">{String(log.message ?? JSON.stringify(log))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}

        {tab === 'config' && (
          <div className="card-body">
            <pre className="code-block">{JSON.stringify(detail.config, null, 2)}</pre>
          </div>
        )}
      </Card>
    </>
  )
}

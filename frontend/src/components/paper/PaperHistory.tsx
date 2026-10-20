/** 纸盘历史会话表与运行详情弹窗。 */

import type { PaperState, RunDetail, RunSummary } from '../../api/types'
import { RunDetailBody, RunMetrics } from '../RunDetail'
import { Badge, Banner, Button, Card, Empty, Loading, Modal, StatusBadge } from '../ui'
import { IconTrash } from '../icons'
import { cx, durationText, fmtDateTime, fmtSignedPct } from '../../utils/format'

export function PaperHistory({
  runs,
  sessions,
  resumingRun,
  detailOpen,
  detailRun,
  detailLoading,
  detailError,
  onDetail,
  onContinue,
  onDelete,
  onCloseDetail,
}: {
  runs: RunSummary[]
  sessions: PaperState[]
  resumingRun: string | null
  detailOpen: boolean
  detailRun: RunDetail | null
  detailLoading: boolean
  detailError: string
  onDetail: (runId: string) => void
  onContinue: (runId: string) => void
  onDelete: (runId: string) => void
  onCloseDetail: () => void
}) {
  return (
    <>
      <Card
        title="历史会话"
        description="纸盘结果同样落库；停止后可点「继续」从停止处接着跑（同一运行 ID）"
        flush
      >
        {runs.length === 0 ? (
          <Empty title="暂无历史会话" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>运行 ID</th>
                  <th>策略</th>
                  <th>标的</th>
                  <th>状态</th>
                  <th className="right">收益率</th>
                  <th className="right">耗时</th>
                  <th>结束时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.run_id}>
                    <td className="mono tiny">{run.run_id}</td>
                    <td className="strong">{run.strategy_id}</td>
                    <td className="mono tiny">{run.symbols.join(', ')}</td>
                    <td>
                      <StatusBadge status={run.status} />
                    </td>
                    <td
                      className={cx('right num', Number(run.metrics?.total_return ?? 0) >= 0 ? 'up' : 'down')}
                    >
                      {run.metrics?.total_return !== undefined ? fmtSignedPct(run.metrics.total_return) : '—'}
                    </td>
                    <td className="right num tiny">{durationText(run.elapsed_seconds)}</td>
                    <td className="tiny faint">{fmtDateTime(run.finished_at ?? run.created_at)}</td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <Button size="sm" variant="ghost" onClick={() => onDetail(run.run_id)}>
                          详情
                        </Button>
                        {(['stopped', 'interrupted', 'failed', 'cancelled'].includes(run.status) ||
                          (run.status === 'running' &&
                            !sessions.some((item) => item.run_id === run.run_id))) && (
                          <Button
                            size="sm"
                            loading={resumingRun === run.run_id}
                            onClick={() => onContinue(run.run_id)}
                            title="从停止处继续（恢复现金、持仓与回放进度，沿用同一运行 ID）"
                          >
                            继续
                          </Button>
                        )}
                        {!sessions.some((item) => item.run_id === run.run_id) && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => onDelete(run.run_id)}
                            title="删除这条历史记录（不可恢复）"
                          >
                            <IconTrash />
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Modal
        open={detailOpen}
        onClose={() => onCloseDetail()}
        title={
          <span className="row" style={{ gap: 8 }}>
            运行详情
            {detailRun && <Badge>{detailRun.run_id}</Badge>}
          </span>
        }
        width={1100}
      >
        {detailLoading && <Loading text="正在读取运行详情…" />}
        {detailError && <Banner kind="danger">{detailError}</Banner>}
        {detailRun && (
          <div className="col" style={{ gap: 16 }}>
            <div className="row between wrap" style={{ gap: 8 }}>
              <div className="row" style={{ gap: 8 }}>
                <StatusBadge status={detailRun.status} />
                <span className="small">{detailRun.strategy_id}</span>
                <span className="tiny faint mono">{detailRun.symbols.join(', ')}</span>
              </div>
              <span className="tiny faint">
                {durationText(detailRun.elapsed_seconds)} ·{' '}
                {fmtDateTime(detailRun.finished_at ?? detailRun.created_at)}
              </span>
            </div>
            {detailRun.status === 'running' && (
              <Banner kind="info">会话仍在运行，以下为最近一次落库的进度快照。</Banner>
            )}
            {detailRun.error && <Banner kind="danger">运行失败：{detailRun.error}</Banner>}
            {detailRun.status !== 'running' && <RunMetrics detail={detailRun} />}
            <RunDetailBody detail={detailRun} />
          </div>
        )}
      </Modal>
    </>
  )
}

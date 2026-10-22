/** 意见反馈：所有用户可提交；列表仅管理员可见。 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { adminApi, ApiError, feedbackApi } from '../api/client'
import type { Feedback } from '../api/types'
import { useAuth } from '../store/auth'
import { notify } from '../store/toast'
import { Badge, Button, Card, Empty, Field } from '../components/ui'
import { fmtDateTime } from '../utils/format'

const CATEGORY_LABELS: Record<string, string> = {
  suggestion: '建议',
  bug: '问题',
  other: '其他',
}

const STATUS_LABELS: Record<string, string> = {
  new: '未读',
  read: '已读',
  resolved: '已处理',
}

export function FeedbackPage() {
  const isAdmin = useAuth((state) => state.user?.role === 'admin')
  const [category, setCategory] = useState<'suggestion' | 'bug' | 'other'>('suggestion')
  const [content, setContent] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [items, setItems] = useState<Feedback[]>([])
  const [loading, setLoading] = useState(false)
  const [statusFilter, setStatusFilter] = useState('all')

  const load = useCallback(async () => {
    if (!isAdmin) return
    setLoading(true)
    try {
      setItems(await adminApi.feedback(statusFilter === 'all' ? undefined : statusFilter))
    } catch {
      // 管理员列表加载失败不阻塞表单
    } finally {
      setLoading(false)
    }
  }, [isAdmin, statusFilter])

  useEffect(() => {
    void load()
  }, [load])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!content.trim()) {
      notify('warning', '请填写反馈内容')
      return
    }
    setSubmitting(true)
    try {
      const result = await feedbackApi.create({ category, content: content.trim() })
      notify('success', result.message)
      setContent('')
      if (isAdmin) await load()
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const updateStatus = async (item: Feedback, status: 'new' | 'read' | 'resolved') => {
    try {
      await adminApi.updateFeedback(item.id, status)
      await load()
    } catch (err) {
      notify('error', err instanceof ApiError ? err.message : '更新失败')
    }
  }

  const pendingCount = useMemo(() => items.filter((item) => item.status === 'new').length, [items])

  return (
    <div className="col" style={{ gap: 18 }}>
      <Card title="意见反馈" description="提交使用建议、问题或想法；反馈内容仅管理员可见">
        <form className="col" style={{ gap: 12 }} onSubmit={submit}>
          <div className="form-grid">
            <Field label="类型">
              <select
                className="select"
                value={category}
                onChange={(event) => setCategory(event.target.value as typeof category)}
              >
                <option value="suggestion">功能建议</option>
                <option value="bug">问题反馈</option>
                <option value="other">其他</option>
              </select>
            </Field>
          </div>
          <Field label="内容" hint="最多 2000 字；请勿提交密码、密钥等敏感信息">
            <textarea
              className="input"
              rows={6}
              maxLength={2000}
              value={content}
              onChange={(event) => setContent(event.target.value)}
              placeholder="我希望增加…… / 我发现……"
            />
          </Field>
          <div className="row between">
            <span className="tiny faint">{content.length} / 2000</span>
            <Button type="submit" variant="primary" loading={submitting}>
              提交反馈
            </Button>
          </div>
        </form>
      </Card>

      {isAdmin && (
        <Card
          title="反馈管理"
          description={`仅管理员可见 · 未读 ${pendingCount} 条`}
          actions={
            <select
              className="select"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              style={{ width: 120 }}
            >
              <option value="all">全部</option>
              <option value="new">未读</option>
              <option value="read">已读</option>
              <option value="resolved">已处理</option>
            </select>
          }
          flush
        >
          {loading ? (
            <div className="card-body faint small">正在加载反馈…</div>
          ) : items.length === 0 ? (
            <Empty title="暂无反馈" hint="用户提交的意见会出现在这里。" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>用户</th>
                    <th>类型</th>
                    <th>内容</th>
                    <th>状态</th>
                    <th className="right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td className="tiny faint nowrap">{fmtDateTime(item.created_at)}</td>
                      <td className="strong">{item.username}</td>
                      <td className="tiny">{CATEGORY_LABELS[item.category] ?? item.category}</td>
                      <td className="small" style={{ maxWidth: 520, whiteSpace: 'pre-wrap' }}>
                        {item.content}
                      </td>
                      <td>
                        <Badge
                          tone={
                            item.status === 'new'
                              ? 'warn'
                              : item.status === 'resolved'
                                ? 'success'
                                : 'neutral'
                          }
                        >
                          {STATUS_LABELS[item.status] ?? item.status}
                        </Badge>
                      </td>
                      <td className="right">
                        <div className="row" style={{ justifyContent: 'flex-end', gap: 4 }}>
                          {item.status === 'new' && (
                            <Button size="sm" variant="ghost" onClick={() => void updateStatus(item, 'read')}>
                              标为已读
                            </Button>
                          )}
                          {item.status !== 'resolved' && (
                            <Button size="sm" onClick={() => void updateStatus(item, 'resolved')}>
                              已处理
                            </Button>
                          )}
                          {item.status === 'resolved' && (
                            <Button size="sm" variant="ghost" onClick={() => void updateStatus(item, 'read')}>
                              重新打开
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
      )}
    </div>
  )
}

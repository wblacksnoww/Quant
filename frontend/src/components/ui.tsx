/** 无样式依赖的小组件集，页面只用这些拼装，避免样式散落。 */

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
import { useEffect } from 'react'
import { cx } from '../utils/format'
import { IconX } from './icons'

// --------------------------------------------------------------------------- //
// 按钮
// --------------------------------------------------------------------------- //

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'primary' | 'danger' | 'ghost'
  size?: 'md' | 'sm'
  block?: boolean
  loading?: boolean
}

export function Button({
  variant = 'default',
  size = 'md',
  block,
  loading,
  children,
  className,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      className={cx(
        'btn',
        variant !== 'default' && `btn-${variant}`,
        size === 'sm' && 'btn-sm',
        block && 'btn-block',
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && <span className="spinner" />}
      {children}
    </button>
  )
}

// --------------------------------------------------------------------------- //
// 卡片
// --------------------------------------------------------------------------- //

interface CardProps {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  flush?: boolean
  className?: string
}

export function Card({ title, description, actions, children, flush, className }: CardProps) {
  return (
    <section className={cx('card', className)}>
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title && <div className="card-title">{title}</div>}
            {description && <div className="card-desc">{description}</div>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      <div className={cx('card-body', flush && 'flush')}>{children}</div>
    </section>
  )
}

// --------------------------------------------------------------------------- //
// 表单
// --------------------------------------------------------------------------- //

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx('input', className)} {...rest} />
}

export function Field({
  label,
  hint,
  children,
  className,
  style,
}: {
  label?: ReactNode
  hint?: ReactNode
  children: ReactNode
  className?: string
  style?: React.CSSProperties
}) {
  return (
    <label className={cx('field', className)} style={style}>
      {label && <span className="label">{label}</span>}
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  )
}

// --------------------------------------------------------------------------- //
// 状态徽标
// --------------------------------------------------------------------------- //

const STATUS_MAP: Record<string, { text: string; tone: string }> = {
  pending: { text: '等待中', tone: 'neutral' },
  queued: { text: '排队中', tone: 'neutral' },
  running: { text: '运行中', tone: 'accent' },
  paused: { text: '已暂停', tone: 'warn' },
  finished: { text: '已完成', tone: 'success' },
  failed: { text: '失败', tone: 'danger' },
  cancelled: { text: '已取消', tone: 'warn' },
  stopped: { text: '已停止', tone: 'warn' },
  interrupted: { text: '已中断', tone: 'danger' },
  filled: { text: '已成交', tone: 'success' },
  partially_filled: { text: '部分成交', tone: 'accent' },
  new: { text: '已报', tone: 'accent' },
  submitted: { text: '已提交', tone: 'accent' },
  accepted: { text: '已受理', tone: 'accent' },
  rejected: { text: '拒绝', tone: 'danger' },
  cancelled_order: { text: '已撤', tone: 'warn' },
  expired: { text: '过期', tone: 'neutral' },
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  const item = STATUS_MAP[status ?? ''] ?? { text: status || '未知', tone: 'neutral' }
  return (
    <span className={`badge badge-${item.tone}`}>
      <span className="dot" />
      {item.text}
    </span>
  )
}

export function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: 'neutral' | 'accent' | 'success' | 'danger' | 'warn'
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

// --------------------------------------------------------------------------- //
// 指标卡
// --------------------------------------------------------------------------- //

export function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: ReactNode
  value: ReactNode
  sub?: ReactNode
  tone?: 'up' | 'down' | 'warn' | 'accent'
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={cx('stat-value', tone)}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// 其他
// --------------------------------------------------------------------------- //

export function Spinner() {
  return <span className="spinner" />
}

export function Loading({ text = '加载中…' }: { text?: string }) {
  return (
    <div className="loading-block">
      <span className="spinner" />
      {text}
    </div>
  )
}

export function Empty({
  title = '暂无数据',
  hint,
  action,
}: {
  title?: string
  hint?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {hint && <div>{hint}</div>}
      {action && <div style={{ marginTop: 12 }}>{action}</div>}
    </div>
  )
}

export function Progress({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100
  return (
    <div className="progress" role="progressbar" aria-valuenow={pct}>
      <div className="progress-bar" style={{ width: `${pct}%` }} />
    </div>
  )
}

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  width,
}: {
  open: boolean
  title: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  width?: number
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal"
        style={width ? { width: `min(${width}px, 100%)` } : undefined}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <div className="card-title">{title}</div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="关闭">
            <IconX />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

export function Tabs<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T
  options: Array<{ value: T; label: string }>
  onChange: (value: T) => void
}) {
  return (
    <div className="tabs" role="tablist">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={value === option.value}
          className={cx('tab', value === option.value && 'active')}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function Banner({
  kind = 'info',
  children,
}: {
  kind?: 'info' | 'warn' | 'danger'
  children: ReactNode
}) {
  return <div className={`banner banner-${kind}`}>{children}</div>
}

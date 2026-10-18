/** 全局通知条。 */

import { useToast } from '../store/toast'
import { IconAlert, IconCheck, IconX } from './icons'

export function Toasts() {
  const items = useToast((state) => state.items)
  const dismiss = useToast((state) => state.dismiss)
  if (items.length === 0) return null

  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((toast) => (
        <div key={toast.id} className={`toast toast-${toast.kind}`}>
          {toast.kind === 'success' ? (
            <IconCheck style={{ width: 15, height: 15, color: 'var(--down)', flex: '0 0 auto' }} />
          ) : (
            <IconAlert
              style={{
                width: 15,
                height: 15,
                color: toast.kind === 'error' ? 'var(--up)' : 'var(--warn)',
                flex: '0 0 auto',
              }}
            />
          )}
          <span>{toast.message}</span>
          <button type="button" className="toast-close" onClick={() => dismiss(toast.id)}>
            <IconX style={{ width: 12, height: 12 }} />
          </button>
        </div>
      ))}
    </div>
  )
}

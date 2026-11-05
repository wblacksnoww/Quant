/** 渲染错误的兜底：没有它，任何一处组件抛错都会让整棵 React 树卸载成白屏。
 *
 * 事件处理器里的异常不会被这里捕获（React 不拦截事件回调），那类错误由
 * App 的全局 unhandledrejection / error 监听转成 toast，避免「点了按钮没反应
 * 也看不到任何提示」。
 */

import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('页面渲染失败', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="login-page">
        <div className="card" style={{ maxWidth: 620, width: '100%', padding: 22 }}>
          <div className="col" style={{ gap: 12 }}>
            <div className="strong">页面渲染失败</div>
            <div className="tiny faint">
              下方是具体报错。可以先「重试」；若反复出现，把这段信息发出来便于定位。
            </div>
            <pre
              className="tiny mono"
              style={{
                margin: 0,
                maxHeight: 220,
                overflow: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {error.message}
              {error.stack ? `\n\n${error.stack}` : ''}
            </pre>
            <div className="row" style={{ gap: 8 }}>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => this.setState({ error: null })}
              >
                重试
              </button>
              <button type="button" className="btn" onClick={() => window.location.reload()}>
                刷新页面
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }
}

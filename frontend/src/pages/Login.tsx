/** 登录页。TOTP 输入默认折叠，服务端要求时自动展开。 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError } from '../api/client'
import { useAuth } from '../store/auth'
import { notify } from '../store/toast'
import { Button, Field } from '../components/ui'
import { IconKey, IconLock, IconShield } from '../components/icons'

export function Login() {
  const login = useAuth((state) => state.login)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [showTotp, setShowTotp] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!username.trim() || !password) {
      setError('请输入用户名和密码')
      return
    }
    setLoading(true)
    setError('')
    try {
      await login(username.trim(), password, showTotp ? totp.trim() : undefined)
      notify('success', `欢迎回来，${username.trim()}`)
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.message.includes('两步验证')) {
          setShowTotp(true)
        }
        setError(err.message)
      } else {
        setError('登录失败，请稍后再试')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-orb login-orb-a" />
      <div className="login-orb login-orb-b" />

      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">
          <div className="brand-mark" style={{ width: 42, height: 42, borderRadius: 12, fontSize: 18 }}>
            Q
          </div>
          <div>
            <div className="login-title">Quant Terminal</div>
            <div className="login-sub">A 股量化研究 · 回测与模拟交易</div>
          </div>
        </div>

        <div className="col" style={{ gap: 14 }}>
          <Field label="用户名">
            <input
              className="input"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="admin"
            />
          </Field>

          <Field label="密码">
            <input
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="••••••••••"
            />
          </Field>

          {showTotp ? (
            <Field label="两步验证码" hint="6 位动态码，或 XXXX-XXXX 格式的恢复码">
              <input
                className="input mono"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={totp}
                onChange={(event) => setTotp(event.target.value)}
                placeholder="123456"
              />
            </Field>
          ) : (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              style={{ alignSelf: 'flex-start', paddingLeft: 0 }}
              onClick={() => setShowTotp(true)}
            >
              <IconKey /> 使用两步验证码
            </button>
          )}

          {error && (
            <div className="banner banner-danger" role="alert">
              <IconLock style={{ width: 15, height: 15, flex: '0 0 auto', marginTop: 1 }} />
              {error}
            </div>
          )}

          <Button type="submit" variant="primary" block loading={loading}>
            登录终端
          </Button>
        </div>

        <div className="login-foot">
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <IconShield style={{ width: 13, height: 13 }} />
            Argon2id · AES-256-GCM · 审计哈希链
          </span>
          <span>仅限授权访问</span>
        </div>
      </form>
    </div>
  )
}

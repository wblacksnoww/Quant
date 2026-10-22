/** 登录页：账号登录 / 注册（图形验证码 + 管理员审核）。 */

import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, authApi } from '../api/client'
import { useAuth } from '../store/auth'
import { notify } from '../store/toast'
import { Button, Field } from '../components/ui'
import { IconKey, IconLock, IconShield } from '../components/icons'

type Mode = 'login' | 'register'

export function Login() {
  const login = useAuth((state) => state.login)
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [totp, setTotp] = useState('')
  const [showTotp, setShowTotp] = useState(false)
  const [captchaToken, setCaptchaToken] = useState('')
  const [captchaImage, setCaptchaImage] = useState('')
  const [captchaAnswer, setCaptchaAnswer] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [registered, setRegistered] = useState(false)

  const refreshCaptcha = async () => {
    try {
      const challenge = await authApi.captcha()
      setCaptchaToken(challenge.token)
      setCaptchaImage(challenge.image)
      setCaptchaAnswer('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '验证码获取失败')
    }
  }

  useEffect(() => {
    if (mode === 'register' && !captchaToken) void refreshCaptcha()
  }, [mode, captchaToken])

  const submitLogin = async (event: FormEvent) => {
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
        if (err.message.includes('两步验证')) setShowTotp(true)
        setError(err.message)
      } else {
        setError('登录失败，请稍后再试')
      }
    } finally {
      setLoading(false)
    }
  }

  const submitRegister = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    if (username.trim().length < 2) {
      setError('用户名至少 2 个字符')
      return
    }
    if (password.length < 10) {
      setError('密码至少 10 位')
      return
    }
    if (password !== confirm) {
      setError('两次输入的密码不一致')
      return
    }
    if (!captchaToken || !captchaAnswer.trim()) {
      setError('请输入图形验证码')
      return
    }
    setLoading(true)
    try {
      const result = await authApi.register({
        username: username.trim(),
        password,
        captcha_token: captchaToken,
        captcha_answer: captchaAnswer.trim(),
      })
      notify('success', result.message)
      setRegistered(true)
      setMode('login')
      setPassword('')
      setConfirm('')
      setCaptchaAnswer('')
      setCaptchaToken('')
      setShowTotp(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '注册失败，请稍后再试')
      await refreshCaptcha()
    } finally {
      setLoading(false)
    }
  }

  const switchMode = (next: Mode) => {
    setMode(next)
    setError('')
    setRegistered(false)
    setShowTotp(false)
    if (next === 'register') {
      // effect 会在切换到注册模式后自动拉取验证码
      setCaptchaToken('')
    }
  }

  return (
    <div className="login-page">
      <div className="login-orb login-orb-a" />
      <div className="login-orb login-orb-b" />

      <form className="login-card" onSubmit={mode === 'login' ? submitLogin : submitRegister}>
        <div className="login-brand">
          <div className="brand-mark" style={{ width: 42, height: 42, borderRadius: 12, fontSize: 18 }}>
            Q
          </div>
          <div>
            <div className="login-title">Quant Terminal</div>
            <div className="login-sub">
              {mode === 'login' ? 'A 股量化研究 · 回测与模拟交易' : '注册后需管理员审核 · 提交后请耐心等待'}
            </div>
          </div>
        </div>

        {registered && mode === 'login' && (
          <div className="banner banner-info" role="status">
            注册申请已提交，管理员审核通过后即可登录。
          </div>
        )}

        <div className="col" style={{ gap: 14 }}>
          <Field label="用户名">
            <input
              className="input"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder={mode === 'login' ? 'admin' : '至少 2 个字符'}
            />
          </Field>

          <Field label="密码" hint={mode === 'register' ? '至少 10 位，建议包含大小写、数字和符号' : ''}>
            <input
              className="input"
              type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="••••••••••"
            />
          </Field>

          {mode === 'register' && (
            <>
              <Field label="确认密码">
                <input
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                  placeholder="••••••••••"
                />
              </Field>

              <Field label="图形验证码" hint="点击图片或「换一张」刷新">
                <div className="row" style={{ gap: 10, alignItems: 'center' }}>
                  {captchaImage ? (
                    <img
                      src={captchaImage}
                      alt="图形验证码"
                      title="点击换一张"
                      onClick={() => void refreshCaptcha()}
                      style={{
                        height: 52,
                        borderRadius: 8,
                        cursor: 'pointer',
                        border: '1px solid var(--border)',
                      }}
                    />
                  ) : (
                    <div className="tiny faint">加载验证码…</div>
                  )}
                  <input
                    className="input mono"
                    value={captchaAnswer}
                    onChange={(event) => setCaptchaAnswer(event.target.value)}
                    maxLength={8}
                    placeholder="输入图中字符"
                    style={{ flex: 1, minWidth: 100 }}
                  />
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    onClick={() => void refreshCaptcha()}
                  >
                    换一张
                  </button>
                </div>
              </Field>
            </>
          )}

          {mode === 'login' &&
            (showTotp ? (
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
            ))}

          {error && (
            <div className="banner banner-danger" role="alert">
              <IconLock style={{ width: 15, height: 15, flex: '0 0 auto', marginTop: 1 }} />
              {error}
            </div>
          )}

          <Button type="submit" variant="primary" block loading={loading}>
            {mode === 'login' ? '登录终端' : '提交注册申请'}
          </Button>

          <button
            type="button"
            className="btn btn-ghost btn-sm"
            style={{ alignSelf: 'center' }}
            onClick={() => switchMode(mode === 'login' ? 'register' : 'login')}
          >
            {mode === 'login' ? '没有账号？注册' : '已有账号？返回登录'}
          </button>
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

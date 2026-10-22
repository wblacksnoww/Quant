/** 设置页：账户安全、两步验证、会话、系统信息、凭据与审计。 */

import { useCallback, useEffect, useState } from 'react'
import QRCode from 'qrcode'
import type { FormEvent } from 'react'
import { adminApi, ApiError, authApi, credentialApi, systemApi } from '../api/client'
import type {
  AuditItem,
  AuditVerify,
  Credential,
  SessionInfo,
  SystemInfo,
  TotpSetup,
  User,
} from '../api/types'
import { Badge, Banner, Button, Card, Empty, Field, Loading, Modal } from '../components/ui'
import {
  IconCheck,
  IconKey,
  IconLock,
  IconPlus,
  IconRefresh,
  IconShield,
  IconTrash,
} from '../components/icons'
import { useAuth } from '../store/auth'
import { notify } from '../store/toast'
import { fmtDateTime } from '../utils/format'

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return fallback
}

export function SettingsPage() {
  const user = useAuth((state) => state.user)
  const reload = useAuth((state) => state.reload)
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [loading, setLoading] = useState(true)

  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [passwordBusy, setPasswordBusy] = useState(false)

  const [setup, setSetup] = useState<TotpSetup | null>(null)
  const [totpModal, setTotpModal] = useState(false)
  const [totpCode, setTotpCode] = useState('')
  const [totpBusy, setTotpBusy] = useState(false)
  const [qrDataUrl, setQrDataUrl] = useState('')
  const [disableModal, setDisableModal] = useState(false)
  const [disablePassword, setDisablePassword] = useState('')
  const [disableCode, setDisableCode] = useState('')

  const [credentials, setCredentials] = useState<Credential[]>([])
  const [credentialModal, setCredentialModal] = useState(false)
  const [credentialName, setCredentialName] = useState('')
  const [credentialKind, setCredentialKind] = useState<'broker' | 'datasource'>('datasource')
  const [credentialPayload, setCredentialPayload] = useState('{\n  "token": ""\n}')
  const [credentialDescription, setCredentialDescription] = useState('')
  const [credentialBusy, setCredentialBusy] = useState(false)
  const [revealed, setRevealed] = useState<{ name: string; payload: Record<string, unknown> } | null>(null)

  const [audit, setAudit] = useState<AuditItem[]>([])
  const [auditVerify, setAuditVerify] = useState<AuditVerify | null>(null)
  const [auditBusy, setAuditBusy] = useState(false)

  const [users, setUsers] = useState<User[]>([])
  const [userModal, setUserModal] = useState(false)
  const [newUsername, setNewUsername] = useState('')
  const [newUserPassword, setNewUserPassword] = useState('')
  const [newUserRole, setNewUserRole] = useState<'user' | 'admin'>('user')
  const [userBusy, setUserBusy] = useState(false)
  const [resetTarget, setResetTarget] = useState<User | null>(null)
  const [resetPassword, setResetPassword] = useState('')

  const isAdmin = user?.role === 'admin'

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await authApi.sessions())
    } catch {
      // 会话列表失败不阻塞其他卡片
    }
  }, [])

  const loadCredentials = useCallback(async () => {
    try {
      setCredentials(await credentialApi.list())
    } catch {
      // 同上
    }
  }, [])

  const loadAudit = useCallback(async () => {
    if (!isAdmin) return
    try {
      setAudit(await systemApi.audit(60))
    } catch {
      // 审计需要管理员
    }
  }, [isAdmin])

  const loadUsers = useCallback(async () => {
    if (!isAdmin) return
    try {
      setUsers(await adminApi.users())
    } catch {
      // 用户管理需要管理员
    }
  }, [isAdmin])

  useEffect(() => {
    let alive = true
    void (async () => {
      const [infoResult] = await Promise.allSettled([systemApi.info()])
      if (!alive) return
      if (infoResult.status === 'fulfilled') setInfo(infoResult.value)
      await Promise.all([loadSessions(), loadCredentials(), loadAudit(), loadUsers()])
      if (alive) setLoading(false)
    })()
    return () => {
      alive = false
    }
  }, [loadSessions, loadCredentials, loadAudit, loadUsers])

  const changePassword = async (event: FormEvent) => {
    event.preventDefault()
    if (newPassword.length < 10) {
      notify('warning', '新密码至少 10 位')
      return
    }
    if (newPassword !== confirmPassword) {
      notify('warning', '两次输入的新密码不一致')
      return
    }
    setPasswordBusy(true)
    try {
      const result = await authApi.changePassword(oldPassword, newPassword)
      notify('success', result.message)
      setOldPassword('')
      setNewPassword('')
      setConfirmPassword('')
      await loadSessions()
    } catch (err) {
      notify('error', errorText(err, '修改密码失败'))
    } finally {
      setPasswordBusy(false)
    }
  }

  useEffect(() => {
    if (!setup?.provisioning_uri) {
      setQrDataUrl('')
      return
    }
    let cancelled = false
    QRCode.toDataURL(setup.provisioning_uri, {
      margin: 1,
      width: 200,
      errorCorrectionLevel: 'M',
    })
      .then((url) => {
        if (!cancelled) setQrDataUrl(url)
      })
      .catch(() => {
        if (!cancelled) setQrDataUrl('')
      })
    return () => {
      cancelled = true
    }
  }, [setup])

  const beginTotpSetup = async () => {
    setTotpBusy(true)
    try {
      const result = await authApi.totpSetup()
      setSetup(result)
      setTotpCode('')
      setTotpModal(true)
    } catch (err) {
      notify('error', errorText(err, '生成 TOTP 种子失败'))
    } finally {
      setTotpBusy(false)
    }
  }

  const enableTotp = async () => {
    if (!totpCode.trim()) {
      notify('warning', '请输入验证器显示的 6 位验证码')
      return
    }
    setTotpBusy(true)
    try {
      await authApi.totpEnable(totpCode.trim())
      notify('success', '两步验证已启用')
      setTotpModal(false)
      setSetup(null)
      await reload()
      await loadAudit()
    } catch (err) {
      notify('error', errorText(err, '启用失败'))
    } finally {
      setTotpBusy(false)
    }
  }

  const disableTotp = async () => {
    setTotpBusy(true)
    try {
      await authApi.totpDisable(disablePassword, disableCode.trim())
      notify('success', '两步验证已关闭')
      setDisableModal(false)
      setDisablePassword('')
      setDisableCode('')
      await reload()
    } catch (err) {
      notify('error', errorText(err, '关闭失败'))
    } finally {
      setTotpBusy(false)
    }
  }

  const createCredential = async () => {
    let payload: Record<string, unknown>
    try {
      const parsed = JSON.parse(credentialPayload) as unknown
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
        throw new Error('payload 必须是 JSON 对象')
      payload = parsed as Record<string, unknown>
    } catch (err) {
      notify('error', `凭据内容不是合法 JSON 对象：${errorText(err, '')}`)
      return
    }
    setCredentialBusy(true)
    try {
      await credentialApi.create({
        name: credentialName.trim(),
        kind: credentialKind,
        payload,
        description: credentialDescription.trim(),
      })
      notify('success', '凭据已加密保存')
      setCredentialModal(false)
      setCredentialName('')
      setCredentialDescription('')
      setCredentialPayload('{\n  "token": ""\n}')
      await loadCredentials()
      await loadAudit()
    } catch (err) {
      notify('error', errorText(err, '保存失败'))
    } finally {
      setCredentialBusy(false)
    }
  }

  const revealCredential = async (id: number) => {
    try {
      const result = await credentialApi.reveal(id)
      setRevealed({ name: result.name, payload: result.payload })
      await loadAudit()
    } catch (err) {
      notify('error', errorText(err, '解密失败'))
    }
  }

  const removeCredential = async (id: number) => {
    if (!window.confirm('删除这条凭据？依赖它的数据源或券商连接将失效。')) return
    try {
      await credentialApi.remove(id)
      notify('success', '凭据已删除')
      await loadCredentials()
    } catch (err) {
      notify('error', errorText(err, '删除失败'))
    }
  }

  const verifyAudit = async () => {
    setAuditBusy(true)
    try {
      const result = await systemApi.verifyAudit()
      setAuditVerify(result)
      notify(result.valid ? 'success' : 'error', result.message)
    } catch (err) {
      notify('error', errorText(err, '校验失败'))
    } finally {
      setAuditBusy(false)
    }
  }

  const createUser = async () => {
    if (!newUsername.trim()) {
      notify('warning', '请输入用户名')
      return
    }
    if (newUserPassword.length < 10) {
      notify('warning', '初始密码至少 10 位')
      return
    }
    setUserBusy(true)
    try {
      await adminApi.createUser({
        username: newUsername.trim(),
        password: newUserPassword,
        role: newUserRole,
      })
      notify('success', `用户 ${newUsername.trim()} 已创建`)
      setUserModal(false)
      setNewUsername('')
      setNewUserPassword('')
      setNewUserRole('user')
      await loadUsers()
      await loadAudit()
    } catch (err) {
      notify('error', errorText(err, '创建失败'))
    } finally {
      setUserBusy(false)
    }
  }

  const updateUser = async (target: User, body: { role?: 'user' | 'admin'; is_active?: boolean }) => {
    setUserBusy(true)
    try {
      await adminApi.updateUser(target.id, body)
      notify('success', `${target.username} 已更新`)
      await loadUsers()
      await loadAudit()
    } catch (err) {
      notify('error', errorText(err, '更新失败'))
    } finally {
      setUserBusy(false)
    }
  }

  const reviewUser = async (target: User, action: 'approve' | 'reject') => {
    setUserBusy(true)
    try {
      await adminApi.approveUser(target.id, action)
      notify('success', action === 'approve' ? `${target.username} 已通过审核` : `${target.username} 已拒绝`)
      await loadUsers()
      await loadAudit()
    } catch (err) {
      notify('error', errorText(err, '审核失败'))
    } finally {
      setUserBusy(false)
    }
  }

  const submitResetPassword = async () => {
    if (!resetTarget) return
    if (resetPassword.length < 10) {
      notify('warning', '新密码至少 10 位')
      return
    }
    setUserBusy(true)
    try {
      const result = await adminApi.resetPassword(resetTarget.id, resetPassword)
      notify('success', result.message)
      setResetTarget(null)
      setResetPassword('')
      await loadUsers()
      await loadAudit()
    } catch (err) {
      notify('error', errorText(err, '重置失败'))
    } finally {
      setUserBusy(false)
    }
  }

  if (loading) return <Loading text="正在加载设置…" />

  return (
    <div className="col" style={{ gap: 18 }}>
      <div className="grid grid-sidebar">
        <div className="col" style={{ gap: 18 }}>
          <Card title="账户" description="密码使用 Argon2id 哈希，任何接口都不会返回明文">
            <dl className="kv">
              <dt>用户名</dt>
              <dd>{user?.username}</dd>
              <dt>角色</dt>
              <dd>{user?.role}</dd>
              <dt>两步验证</dt>
              <dd>
                <Badge tone={user?.totp_enabled ? 'success' : 'warn'}>
                  {user?.totp_enabled ? '已启用' : '未启用'}
                </Badge>
              </dd>
              <dt>最后登录</dt>
              <dd>{fmtDateTime(user?.last_login_at)}</dd>
              <dt>创建时间</dt>
              <dd>{fmtDateTime(user?.created_at)}</dd>
            </dl>
          </Card>

          <Card title="修改密码" description="修改后其它设备的刷新令牌会全部吊销">
            <form className="col" style={{ gap: 12 }} onSubmit={changePassword}>
              <Field label="当前密码">
                <input
                  className="input"
                  type="password"
                  autoComplete="current-password"
                  value={oldPassword}
                  onChange={(event) => setOldPassword(event.target.value)}
                />
              </Field>
              <div className="form-grid">
                <Field label="新密码" hint="至少 10 位，建议含大小写、数字与符号">
                  <input
                    className="input"
                    type="password"
                    autoComplete="new-password"
                    value={newPassword}
                    onChange={(event) => setNewPassword(event.target.value)}
                  />
                </Field>
                <Field label="确认新密码">
                  <input
                    className="input"
                    type="password"
                    autoComplete="new-password"
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                  />
                </Field>
              </div>
              <div className="row" style={{ justifyContent: 'flex-end' }}>
                <Button type="submit" loading={passwordBusy}>
                  <IconLock /> 更新密码
                </Button>
              </div>
            </form>
          </Card>

          <Card
            title="两步验证"
            description="基于 TOTP（RFC 6238），兼容 Google Authenticator、1Password 等"
            actions={
              user?.totp_enabled ? <Badge tone="success">已启用</Badge> : <Badge tone="warn">未启用</Badge>
            }
          >
            {user?.totp_enabled ? (
              <div className="col" style={{ gap: 12 }}>
                <div className="small muted">
                  登录时需要输入验证器生成的 6 位动态码。恢复码只在生成时展示一次，请妥善保管。
                </div>
                <Button variant="danger" onClick={() => setDisableModal(true)}>
                  关闭两步验证
                </Button>
              </div>
            ) : (
              <div className="col" style={{ gap: 12 }}>
                <div className="small muted">强烈建议在远程部署时启用。启用后登录需要「密码 + 动态码」。</div>
                <Button variant="primary" loading={totpBusy} onClick={() => void beginTotpSetup()}>
                  <IconShield /> 生成 TOTP 种子
                </Button>
              </div>
            )}
          </Card>
        </div>

        <div className="col" style={{ gap: 18 }}>
          <Card title="系统信息" description={info ? `${info.name} v${info.version}` : '不可用'}>
            {info ? (
              <div className="col" style={{ gap: 12 }}>
                <dl className="kv">
                  <dt>环境</dt>
                  <dd>{info.env}</dd>
                  <dt>Python</dt>
                  <dd>{info.python}</dd>
                  <dt>数据库</dt>
                  <dd>{info.database}</dd>
                  <dt>数据源</dt>
                  <dd>{info.data_provider}</dd>
                  <dt>可用数据源</dt>
                  <dd>{info.available_providers.join(', ') || '—'}</dd>
                  <dt>字段加密</dt>
                  <dd>{String(info.encryption.field_cipher ?? '—')}</dd>
                  <dt>密钥派生</dt>
                  <dd>{String(info.encryption.kdf ?? '—')}</dd>
                  <dt>密钥指纹</dt>
                  <dd className="mono tiny">{String(info.encryption.key_fingerprint ?? '—')}</dd>
                  <dt>令牌算法</dt>
                  <dd>{String(info.encryption.jwt_algorithm ?? '—')}</dd>
                  <dt>访问令牌 TTL</dt>
                  <dd>{String(info.auth.access_ttl ?? '—')} 秒</dd>
                  <dt>刷新令牌 TTL</dt>
                  <dd>{String(info.auth.refresh_ttl ?? '—')} 秒</dd>
                  <dt>强制 2FA</dt>
                  <dd>{info.auth.require_totp ? '是' : '否'}</dd>
                  <dt>用户数</dt>
                  <dd>
                    {String(info.auth.total_users ?? 0)}（{String(info.auth.totp_users ?? 0)} 个启用 2FA）
                  </dd>
                </dl>
                {info.warnings.length > 0 && (
                  <Banner kind="warn">
                    {info.warnings.map((warning) => (
                      <div key={warning}>{warning}</div>
                    ))}
                  </Banner>
                )}
              </div>
            ) : (
              <Empty title="无法读取系统信息" />
            )}
          </Card>

          <Card
            title="活跃会话"
            description="每个刷新令牌一条记录"
            actions={
              <Button size="sm" variant="ghost" onClick={() => void loadSessions()}>
                <IconRefresh />
              </Button>
            }
            flush
          >
            {sessions.length === 0 ? (
              <Empty title="没有活跃会话" />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>IP / 客户端</th>
                      <th>创建</th>
                      <th>过期</th>
                      <th className="right">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sessions.map((session) => (
                      <tr key={session.id}>
                        <td>
                          <div className="mono tiny">{session.ip || '—'}</div>
                          <div
                            className="tiny faint truncate"
                            style={{ maxWidth: 220 }}
                            title={session.user_agent}
                          >
                            {session.user_agent || '未知客户端'}
                          </div>
                        </td>
                        <td className="tiny faint">{fmtDateTime(session.created_at)}</td>
                        <td className="tiny faint">{fmtDateTime(session.expires_at)}</td>
                        <td className="right">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={async () => {
                              try {
                                await authApi.revokeSession(session.id)
                                notify('success', '会话已吊销')
                                await loadSessions()
                              } catch (err) {
                                notify('error', errorText(err, '吊销失败'))
                              }
                            }}
                          >
                            吊销
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      </div>

      <Card
        title="加密凭据"
        description="AES-256-GCM 加密后入库；列表不返回密文，明文仅管理员可解密查看"
        actions={
          <Button size="sm" variant="primary" onClick={() => setCredentialModal(true)}>
            <IconPlus /> 新建凭据
          </Button>
        }
        flush
      >
        {credentials.length === 0 ? (
          <Empty title="暂无凭据" hint="可保存 tushare token、券商 API key 等敏感信息。" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>名称</th>
                  <th>类型</th>
                  <th>说明</th>
                  <th>更新时间</th>
                  <th className="right">操作</th>
                </tr>
              </thead>
              <tbody>
                {credentials.map((item) => (
                  <tr key={item.id}>
                    <td className="strong">{item.name}</td>
                    <td>
                      <Badge tone={item.kind === 'broker' ? 'warn' : 'accent'}>{item.kind}</Badge>
                    </td>
                    <td className="tiny faint">{item.description || '—'}</td>
                    <td className="tiny faint">{fmtDateTime(item.updated_at ?? item.created_at)}</td>
                    <td className="right">
                      <div className="row" style={{ justifyContent: 'flex-end', gap: 4 }}>
                        {isAdmin && (
                          <Button size="sm" variant="ghost" onClick={() => void revealCredential(item.id)}>
                            <IconKey /> 查看
                          </Button>
                        )}
                        <Button size="sm" variant="ghost" onClick={() => void removeCredential(item.id)}>
                          <IconTrash />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {isAdmin && (
        <Card
          title="用户管理"
          description="创建账号、调整角色、停用或重置密码；所有操作写入审计日志"
          actions={
            <>
              <Button size="sm" variant="ghost" onClick={() => void loadUsers()}>
                <IconRefresh />
              </Button>
              <Button size="sm" variant="primary" onClick={() => setUserModal(true)}>
                <IconPlus /> 新建用户
              </Button>
            </>
          }
          flush
        >
          {users.length === 0 ? (
            <Empty title="暂无用户" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>用户名</th>
                    <th>角色</th>
                    <th>状态</th>
                    <th>审核</th>
                    <th>2FA</th>
                    <th>最后登录</th>
                    <th className="right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((item) => {
                    const self = item.id === user?.id
                    return (
                      <tr key={item.id}>
                        <td className="strong">
                          {item.username}
                          {self && <span className="tiny faint">（我）</span>}
                        </td>
                        <td>
                          <Badge tone={item.role === 'admin' ? 'accent' : 'neutral'}>{item.role}</Badge>
                        </td>
                        <td>
                          <Badge tone={item.is_active ? 'success' : 'danger'}>
                            {item.is_active ? '启用' : '已停用'}
                          </Badge>
                        </td>
                        <td>
                          <Badge
                            tone={
                              item.approval_status === 'pending'
                                ? 'warn'
                                : item.approval_status === 'rejected'
                                  ? 'danger'
                                  : 'success'
                            }
                          >
                            {item.approval_status === 'pending'
                              ? '待审核'
                              : item.approval_status === 'rejected'
                                ? '已拒绝'
                                : '已通过'}
                          </Badge>
                        </td>
                        <td>
                          <Badge tone={item.totp_enabled ? 'success' : 'warn'}>
                            {item.totp_enabled ? '已启用' : '未启用'}
                          </Badge>
                        </td>
                        <td className="tiny faint">{fmtDateTime(item.last_login_at)}</td>
                        <td className="right">
                          <div className="row" style={{ justifyContent: 'flex-end', gap: 4 }}>
                            {(item.approval_status === 'pending' || item.approval_status === 'rejected') && (
                              <Button
                                size="sm"
                                variant="primary"
                                disabled={userBusy}
                                onClick={() => void reviewUser(item, 'approve')}
                              >
                                通过
                              </Button>
                            )}
                            {item.approval_status === 'pending' && (
                              <Button
                                size="sm"
                                variant="danger"
                                disabled={userBusy}
                                onClick={() => void reviewUser(item, 'reject')}
                              >
                                拒绝
                              </Button>
                            )}
                            {item.approval_status !== 'pending' && item.approval_status !== 'rejected' && (
                              <>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  disabled={self || userBusy}
                                  title={self ? '不能修改自己的角色' : '切换角色'}
                                  onClick={() =>
                                    void updateUser(item, { role: item.role === 'admin' ? 'user' : 'admin' })
                                  }
                                >
                                  {item.role === 'admin' ? '降为用户' : '设为管理员'}
                                </Button>
                                <Button size="sm" variant="ghost" onClick={() => setResetTarget(item)}>
                                  重置密码
                                </Button>
                                <Button
                                  size="sm"
                                  variant={item.is_active ? 'danger' : 'ghost'}
                                  disabled={self || userBusy}
                                  title={self ? '不能停用自己' : undefined}
                                  onClick={() => void updateUser(item, { is_active: !item.is_active })}
                                >
                                  {item.is_active ? '停用' : '启用'}
                                </Button>
                              </>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {isAdmin && (
        <Card
          title="审计日志"
          description="SHA-256 哈希链，任何篡改都会让校验失效"
          actions={
            <>
              <Button size="sm" variant="ghost" onClick={() => void loadAudit()}>
                <IconRefresh />
              </Button>
              <Button size="sm" onClick={() => void verifyAudit()} loading={auditBusy}>
                <IconCheck /> 校验链
              </Button>
            </>
          }
          flush
        >
          {auditVerify && (
            <div className="card-body" style={{ paddingBottom: 0 }}>
              <Banner kind={auditVerify.valid ? 'info' : 'danger'}>
                {auditVerify.message} · 已校验 {auditVerify.checked} 条
              </Banner>
            </div>
          )}
          {audit.length === 0 ? (
            <Empty title="暂无审计记录" />
          ) : (
            <div className="table-wrap" style={{ maxHeight: 460 }}>
              <table>
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>操作者</th>
                    <th>动作</th>
                    <th>对象</th>
                    <th>IP</th>
                    <th>哈希</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((item) => (
                    <tr key={item.id}>
                      <td className="tiny faint nowrap">{fmtDateTime(item.ts)}</td>
                      <td className="strong">{item.actor}</td>
                      <td className="mono tiny">{item.action}</td>
                      <td className="tiny faint truncate" style={{ maxWidth: 200 }}>
                        {item.target || '—'}
                      </td>
                      <td className="mono tiny">{item.ip || '—'}</td>
                      <td className="mono tiny faint" title={item.hash}>
                        {item.hash.slice(0, 12)}…
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {/* 新建用户 */}
      <Modal
        open={userModal}
        title="新建用户"
        onClose={() => setUserModal(false)}
        footer={
          <>
            <Button onClick={() => setUserModal(false)}>取消</Button>
            <Button
              variant="primary"
              loading={userBusy}
              disabled={!newUsername.trim()}
              onClick={() => void createUser()}
            >
              创建
            </Button>
          </>
        }
      >
        <div className="col" style={{ gap: 12 }}>
          <div className="form-grid">
            <Field label="用户名" hint="2–64 位">
              <input
                className="input"
                value={newUsername}
                onChange={(event) => setNewUsername(event.target.value)}
                placeholder="alice"
              />
            </Field>
            <Field label="角色">
              <select
                className="select"
                value={newUserRole}
                onChange={(event) => setNewUserRole(event.target.value as 'user' | 'admin')}
              >
                <option value="user">普通用户</option>
                <option value="admin">管理员</option>
              </select>
            </Field>
          </div>
          <Field label="初始密码" hint="至少 10 位，建议含大小写、数字与符号；创建后请让用户自行修改">
            <input
              className="input"
              type="password"
              autoComplete="new-password"
              value={newUserPassword}
              onChange={(event) => setNewUserPassword(event.target.value)}
            />
          </Field>
          <Banner kind="info">
            普通用户可以运行回测与模拟盘、管理自己的凭据；只有管理员能看到用户管理与审计日志。
          </Banner>
        </div>
      </Modal>

      {/* 重置密码 */}
      <Modal
        open={resetTarget !== null}
        title={`重置密码：${resetTarget?.username ?? ''}`}
        onClose={() => {
          setResetTarget(null)
          setResetPassword('')
        }}
        footer={
          <>
            <Button
              onClick={() => {
                setResetTarget(null)
                setResetPassword('')
              }}
            >
              取消
            </Button>
            <Button variant="danger" loading={userBusy} onClick={() => void submitResetPassword()}>
              重置
            </Button>
          </>
        }
      >
        <div className="col" style={{ gap: 12 }}>
          <Banner kind="warn">重置后该用户的所有会话会被吊销，需要用新密码重新登录。</Banner>
          <Field label="新密码" hint="至少 10 位">
            <input
              className="input"
              type="password"
              autoComplete="new-password"
              value={resetPassword}
              onChange={(event) => setResetPassword(event.target.value)}
            />
          </Field>
        </div>
      </Modal>

      {/* TOTP 设置 */}
      <Modal
        open={totpModal && setup !== null}
        title="启用两步验证"
        onClose={() => setTotpModal(false)}
        footer={
          <>
            <Button onClick={() => setTotpModal(false)}>取消</Button>
            <Button variant="primary" loading={totpBusy} onClick={() => void enableTotp()}>
              验证并启用
            </Button>
          </>
        }
      >
        {setup && (
          <div className="col" style={{ gap: 14 }}>
            <Banner kind="warn">
              请现在保存密钥与恢复码。关闭弹窗后恢复码不再展示；丢失验证器时只能使用恢复码。
            </Banner>
            {qrDataUrl && (
              <div className="col" style={{ alignItems: 'center', gap: 6 }}>
                <img
                  src={qrDataUrl}
                  alt="两步验证二维码"
                  width={200}
                  height={200}
                  style={{ borderRadius: 8, background: '#fff', padding: 6 }}
                />
                <div className="tiny faint">用验证器扫码；扫不了就手动输入下方密钥</div>
              </div>
            )}
            <div>
              <div className="label">密钥（手动输入到验证器）</div>
              <div className="code-block" style={{ marginTop: 4 }}>
                {setup.secret}
              </div>
            </div>
            <div>
              <div className="label">配置 URI</div>
              <div className="code-block" style={{ marginTop: 4 }}>
                {setup.provisioning_uri}
              </div>
            </div>
            <div>
              <div className="label">恢复码</div>
              <div className="row wrap" style={{ gap: 6, marginTop: 6 }}>
                {setup.recovery_codes.map((code) => (
                  <span key={code} className="badge badge-neutral mono">
                    {code}
                  </span>
                ))}
              </div>
            </div>
            <Field label="验证器当前验证码">
              <input
                className="input mono"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={totpCode}
                onChange={(event) => setTotpCode(event.target.value)}
                placeholder="123456"
              />
            </Field>
          </div>
        )}
      </Modal>

      {/* 关闭 TOTP */}
      <Modal
        open={disableModal}
        title="关闭两步验证"
        onClose={() => setDisableModal(false)}
        footer={
          <>
            <Button onClick={() => setDisableModal(false)}>取消</Button>
            <Button variant="danger" loading={totpBusy} onClick={() => void disableTotp()}>
              确认关闭
            </Button>
          </>
        }
      >
        <div className="col" style={{ gap: 12 }}>
          <Banner kind="danger">关闭后登录只需要密码，安全性会明显下降。</Banner>
          <Field label="当前密码">
            <input
              className="input"
              type="password"
              value={disablePassword}
              onChange={(event) => setDisablePassword(event.target.value)}
            />
          </Field>
          <Field label="当前验证码">
            <input
              className="input mono"
              inputMode="numeric"
              value={disableCode}
              onChange={(event) => setDisableCode(event.target.value)}
              placeholder="123456"
            />
          </Field>
        </div>
      </Modal>

      {/* 新建凭据 */}
      <Modal
        open={credentialModal}
        title="新建加密凭据"
        onClose={() => setCredentialModal(false)}
        footer={
          <>
            <Button onClick={() => setCredentialModal(false)}>取消</Button>
            <Button
              variant="primary"
              loading={credentialBusy}
              disabled={!credentialName.trim()}
              onClick={() => void createCredential()}
            >
              加密保存
            </Button>
          </>
        }
      >
        <div className="col" style={{ gap: 12 }}>
          <div className="form-grid">
            <Field label="名称">
              <input
                className="input"
                value={credentialName}
                onChange={(event) => setCredentialName(event.target.value)}
                placeholder="tushare-prod"
              />
            </Field>
            <Field label="类型">
              <select
                className="select"
                value={credentialKind}
                onChange={(event) => setCredentialKind(event.target.value as 'broker' | 'datasource')}
              >
                <option value="datasource">数据源</option>
                <option value="broker">券商</option>
              </select>
            </Field>
          </div>
          <Field label="说明">
            <input
              className="input"
              value={credentialDescription}
              onChange={(event) => setCredentialDescription(event.target.value)}
              placeholder="可选"
            />
          </Field>
          <Field label="内容（JSON 对象）" hint="明文只在浏览器与内存中存在，入库前立即加密">
            <textarea
              className="textarea"
              value={credentialPayload}
              onChange={(event) => setCredentialPayload(event.target.value)}
              spellCheck={false}
            />
          </Field>
        </div>
      </Modal>

      {/* 查看凭据明文 */}
      <Modal
        open={revealed !== null}
        title={`凭据明文：${revealed?.name ?? ''}`}
        onClose={() => setRevealed(null)}
        footer={<Button onClick={() => setRevealed(null)}>关闭</Button>}
      >
        <Banner kind="warn">本次查看已写入审计日志。</Banner>
        <pre className="code-block" style={{ marginTop: 12 }}>
          {JSON.stringify(revealed?.payload ?? {}, null, 2)}
        </pre>
      </Modal>
    </div>
  )
}

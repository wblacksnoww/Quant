/** 应用外壳：侧边导航 + 顶栏 + 内容区。 */

import type { ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useAuth } from '../store/auth'
import {
  IconCandles,
  IconDashboard,
  IconFlask,
  IconLayers,
  IconLogout,
  IconPlay,
  IconSettings,
  IconShield,
  IconWifi,
} from './icons'

const PAGES: Record<string, { title: string; sub: string }> = {
  '/': { title: '总览', sub: '账户、运行与系统状态一屏掌握' },
  '/market': { title: '行情研究', sub: 'A 股日线与本地数据缓存' },
  '/strategies': { title: '策略库', sub: '内置策略与参数说明' },
  '/strategies/blueprint': { title: '蓝图编辑器', sub: '节点数据流图，编译为 spec v2' },
  '/backtest': { title: '回测', sub: '事件驱动引擎，下一根 K 线撮合，杜绝未来函数' },
  '/paper': { title: '模拟盘', sub: '与回测共用撮合与会话，仅时间驱动不同' },
  '/risk': { title: '风控', sub: '下单前逐条校验，拒绝原因全程留痕' },
  '/settings': { title: '设置', sub: '账户安全、审计与系统信息' },
}

const NAV = [
  { section: '总览', items: [{ to: '/', label: '仪表盘', icon: IconDashboard, end: true }] },
  {
    section: '交易研究',
    items: [
      { to: '/market', label: '行情', icon: IconCandles },
      { to: '/strategies', label: '策略库', icon: IconLayers },
      { to: '/backtest', label: '回测', icon: IconFlask },
      { to: '/paper', label: '模拟盘', icon: IconPlay },
      { to: '/risk', label: '风控', icon: IconShield },
    ],
  },
  { section: '系统', items: [{ to: '/settings', label: '设置', icon: IconSettings }] },
]

export function Layout({
  children,
  wsConnected,
}: {
  children: ReactNode
  wsConnected: boolean
}) {
  const user = useAuth((state) => state.user)
  const logout = useAuth((state) => state.logout)
  const location = useLocation()
  const meta = PAGES[location.pathname] ?? { title: 'Quant Terminal', sub: '' }
  const initial = (user?.username ?? '?').slice(0, 1).toUpperCase()

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">Q</div>
          <div className="brand-text">
            <div className="brand-name">Quant Terminal</div>
            <div className="brand-sub">A-Share Research</div>
          </div>
        </div>

        <nav className="nav">
          {NAV.map((group) => (
            <div key={group.section}>
              <div className="nav-section">{group.section}</div>
              {group.items.map((item) => {
                const Icon = item.icon
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={'end' in item ? item.end : false}
                    className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
                  >
                    <Icon />
                    <span>{item.label}</span>
                  </NavLink>
                )
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="user-chip">
            <div className="avatar">{initial}</div>
            <div className="user-meta">
              <div className="user-name">{user?.username ?? '未登录'}</div>
              <div className="user-role">{user?.role ?? 'guest'}</div>
            </div>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              title="退出登录"
              onClick={() => void logout()}
            >
              <IconLogout />
            </button>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div>
            <div className="page-title">{meta.title}</div>
            <div className="page-sub">{meta.sub}</div>
          </div>
          <div className="topbar-right">
            <span className={`badge ${wsConnected ? 'badge-success' : 'badge-neutral'}`} title="实时事件连接">
              <IconWifi style={{ width: 12, height: 12 }} />
              {wsConnected ? '实时' : '离线'}
            </span>
            <span className={`badge ${user?.totp_enabled ? 'badge-success' : 'badge-warn'}`}>
              {user?.totp_enabled ? '2FA 已启用' : '2FA 未启用'}
            </span>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  )
}

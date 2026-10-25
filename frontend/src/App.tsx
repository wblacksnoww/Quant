import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { onAuthChange } from './api/client'
import { Layout } from './components/Layout'
import { Toasts } from './components/Toasts'
import { useWebSocket } from './hooks/useWebSocket'
import { BacktestPage } from './pages/Backtest'
import { BlueprintPage } from './pages/Blueprint'
import { DashboardPage } from './pages/Dashboard'
import { FeedbackPage } from './pages/Feedback'
import { FundsPage } from './pages/Funds'
import { GatewayPage } from './pages/Gateway'
import { InstrumentPage } from './pages/Instrument'
import { LivePage } from './pages/Live'
import { Login } from './pages/Login'
import { MarketPage } from './pages/Market'
import { NewsPage } from './pages/News'
import { OptimizePage } from './pages/Optimize'
import { PaperPage } from './pages/Paper'
import { PortfolioPage } from './pages/Portfolio'
import { RiskPage } from './pages/Risk'
import { SettingsPage } from './pages/Settings'
import { StrategiesPage } from './pages/Strategies'
import { useAuth } from './store/auth'
import { useEvents } from './store/events'

export default function App() {
  const status = useAuth((state) => state.status)
  const bootstrap = useAuth((state) => state.bootstrap)
  const markAnonymous = useAuth((state) => state.markAnonymous)
  const handleEvent = useEvents((state) => state.handle)

  useEffect(() => {
    void bootstrap()
    return onAuthChange((authenticated) => {
      if (!authenticated) markAnonymous()
    })
  }, [bootstrap, markAnonymous])

  const { connected } = useWebSocket(handleEvent, status === 'authenticated')

  if (status === 'booting') {
    return (
      <div className="login-page">
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14 }}>
          <div className="brand-mark" style={{ width: 46, height: 46, borderRadius: 13, fontSize: 20 }}>
            Q
          </div>
          <span className="spinner" style={{ width: 22, height: 22 }} />
          <div className="faint small">正在恢复会话…</div>
        </div>
      </div>
    )
  }

  if (status === 'anonymous') {
    return (
      <>
        <Login />
        <Toasts />
      </>
    )
  }

  return (
    <>
      <Layout wsConnected={connected}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/market" element={<MarketPage />} />
          <Route path="/market/:code" element={<InstrumentPage />} />
          <Route path="/news" element={<NewsPage />} />
          <Route path="/feedback" element={<FeedbackPage />} />
          <Route path="/strategies" element={<StrategiesPage />} />
          <Route path="/strategies/blueprint" element={<BlueprintPage />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/optimize" element={<OptimizePage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/paper" element={<PaperPage />} />
          <Route path="/live" element={<LivePage />} />
          <Route path="/gateway" element={<GatewayPage />} />
          <Route path="/funds" element={<FundsPage />} />
          <Route path="/risk" element={<RiskPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
      <Toasts />
    </>
  )
}

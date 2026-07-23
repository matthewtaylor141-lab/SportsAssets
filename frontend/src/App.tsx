import { NavLink, Route, Routes } from 'react-router-dom'
import { useLiveFeed } from './lib/sse'
import { Admin } from './pages/Admin'
import { AITrader } from './pages/AITrader'
import { Alerts } from './pages/Alerts'
import { Engine } from './pages/Engine'
import { LiveFeed } from './pages/LiveFeed'
import { Markets } from './pages/Markets'
import { Matrix } from './pages/Matrix'
import { WhaleProfile } from './pages/WhaleProfile'
import { Whales } from './pages/Whales'

const TABS = [
  { to: '/', label: 'Live Feed' },
  { to: '/whales', label: 'Whales' },
  { to: '/matrix', label: 'Sport Matrix' },
  { to: '/markets', label: 'Markets' },
  { to: '/engine', label: '⚡ Engine' },
  { to: '/ai-trader', label: '🤖 AI Trader' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/admin', label: 'Admin' },
]

export default function App() {
  const { conn, live } = useLiveFeed()

  return (
    <div className="app">
      <nav className="nav">
        <span className="brand">
          SportsAssets <span>Hub</span>
        </span>
        {TABS.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.to === '/'} className={({ isActive }) => `tab${isActive ? ' active' : ''}`}>
            {t.label}
          </NavLink>
        ))}
        <span className="spacer" />
        <span className={`conn ${conn}`}>
          <span className="dot" />
          {conn === 'live' ? 'live' : conn === 'connecting' ? 'connecting…' : 'reconnecting…'}
        </span>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<LiveFeed live={live} conn={conn} />} />
          <Route path="/whales" element={<Whales />} />
          <Route path="/whales/:id" element={<WhaleProfile />} />
          <Route path="/matrix" element={<Matrix />} />
          <Route path="/markets" element={<Markets />} />
          <Route path="/engine" element={<Engine />} />
          <Route path="/ai-trader" element={<AITrader />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
        <p className="notice">
          Displays public on-chain and public-API data with attribution to its sources. Informational
          only — not betting or investment advice. Not affiliated with any prediction market
          operator.
        </p>
      </main>
    </div>
  )
}

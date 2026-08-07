import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { Admin } from './pages/Admin'
import { Analytics } from './pages/Analytics'
import { Engine } from './pages/Engine'
import { System } from './pages/System'
import { TrackRecord } from './pages/TrackRecord'
import { TradeDesk } from './pages/TradeDesk'

/* The site IS the AI trader now. The whale-hub pages remain in the repo
 * (and in git history) but are off the router: this product has one story
 * to tell — the machine's own record, told from its own ledger — plus the
 * operational pages that keep it honest and controllable. */

const TABS = [
  { to: '/', label: 'Performance' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/system', label: 'System' },
  { to: '/engine', label: '⚙ Engine' },
  { to: '/desk', label: 'Desk' },
  { to: '/admin', label: 'Ops' },
]

export default function App() {
  // Keying the content wrapper by path replays the entrance choreography
  // on every route change — navigation feels composed, not swapped.
  const { pathname } = useLocation()
  return (
    <div className="app">
      <nav className="nav">
        <span className="brand">
          BETTOR<span>EDGE</span>&nbsp;AI
        </span>
        {TABS.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.to === '/'}
            className={({ isActive }) => `tab${isActive ? ' active' : ''}`}
          >
            {t.label}
          </NavLink>
        ))}
        <span className="spacer" />
      </nav>
      <main className="main page-fade" key={pathname}>
        <Routes>
          <Route path="/" element={<TrackRecord />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/system" element={<System />} />
          <Route path="/engine" element={<Engine />} />
          <Route path="/desk" element={<TradeDesk />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
        <p className="notice">
          All figures are read live from the trading engine's own ledger — nothing on this
          site is entered by hand. Informational only — not betting or investment advice.
          Not affiliated with any prediction market operator.
        </p>
      </main>
    </div>
  )
}

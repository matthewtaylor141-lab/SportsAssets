import { Suspense, lazy, type ComponentType } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { Brand } from './components/Brand'
import { Admin } from './pages/Admin'
import { Analytics } from './pages/Analytics'
import { Engine } from './pages/Engine'
import { System } from './pages/System'
import { TrackRecord } from './pages/TrackRecord'
import { TradeDesk } from './pages/TradeDesk'

// Voice cockpit (owner deliverable 2026-08-21): lazy so the Claude/TTS/canvas
// stack never weighs down the main record pages.
const Jarvis = lazy(() => import('./pages/Jarvis'))
// Desk accounts view (venue balances + cash-out): lazy for the same reason.
// Tolerates either export style so the page module stays free to match
// the codebase's named-export convention.
const Accounts = lazy(() =>
  import('./pages/Accounts').then((m: Record<string, unknown>) => ({
    default: (m.default ?? m.Accounts) as ComponentType,
  })),
)

/* The site IS the AI trader now. The whale-hub pages remain in the repo
 * (and in git history) but are off the router: this product has one story
 * to tell — the machine's own record, told from its own ledger — plus the
 * operational pages that keep it honest and controllable. */

const TABS = [
  { to: '/', label: 'Performance' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/accounts', label: 'Accounts' },
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
        <Brand />
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
          <Route path="/accounts" element={<Suspense fallback={null}><Accounts /></Suspense>} />
          <Route path="/system" element={<System />} />
          <Route path="/engine" element={<Engine />} />
          <Route path="/desk" element={<TradeDesk />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="/jarvis" element={<Suspense fallback={null}><Jarvis /></Suspense>} />
        </Routes>
        <p className="notice">
          Performance shown is the whale copy portfolio: every settled copy trade,
          uncapped. Full account statements available to investors on request.
          All figures are read live from the platform's own order-level ledger —
          nothing on this site is entered by hand. Informational only — not betting
          or investment advice. Not affiliated with any prediction market operator.
          {' '}<span style={{ opacity: 0.55 }}>build {__BUILD_SHA__.replace('bsha_', '')}</span>
        </p>
      </main>
    </div>
  )
}

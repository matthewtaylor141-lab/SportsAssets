import { Suspense, lazy, useEffect, useRef, useState, type ComponentType } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { Brand } from './components/Brand'
import { CommandPalette } from './components/CommandPalette'
import { PulseLine } from './components/PulseLine'
import { Tape } from './components/Tape'
import { Admin } from './pages/Admin'
import { Analytics } from './pages/Analytics'
import { Engine } from './pages/Engine'
import { System } from './pages/System'
import { TrackRecord } from './pages/TrackRecord'
import { TradeDesk } from './pages/TradeDesk'

// Voice cockpit (owner deliverable 2026-08-21): lazy so the Claude/TTS/canvas
// stack never weighs down the main record pages.
const Jarvis = lazy(() => import('./pages/Jarvis'))
const Wall = lazy(() => import('./pages/Wall'))
// Management reports (owner order 2026-08-28): whale × sport × type ×
// period pivots with copy latency — lazy, admin-token gated inside.
const Reports = lazy(() => import('./pages/Reports'))
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
  { to: '/reports', label: 'Reports' },
  { to: '/accounts', label: 'Accounts' },
  { to: '/desk', label: 'Desk' },
  { to: '/meridian', label: 'Meridian' },
  { to: '/system', label: 'System' },
  { to: '/engine', label: '⚙ Engine' },
  { to: '/admin', label: 'Ops' },
]

/* ── Mobile bottom tab bar (PWA-first, owner directive 2026-08-22) ──
 * Under 720px the top nav collapses to a brand strip and these four
 * primary destinations move into a fixed bottom bar — thumb-reach,
 * 44px+ targets, safe-area padded for the iPhone home indicator.
 * Everything else lives behind the More sheet. Desktop is untouched:
 * the bar and sheet are display:none above the breakpoint. */
const MOBILE_TABS = [
  { to: '/', label: 'Performance' },
  { to: '/accounts', label: 'Accounts' },
  { to: '/desk', label: 'Desk' },
  { to: '/meridian', label: 'Meridian' },
]
const MORE_TABS = [
  { to: '/analytics', label: 'Analytics' },
  { to: '/reports', label: 'Reports' },
  { to: '/system', label: 'System' },
  { to: '/engine', label: 'Engine' },
  { to: '/admin', label: 'Ops' },
]

/** Line-icon set for the tab bar — stroke follows currentColor so the
 * active tint comes free from CSS. */
function TabIcon({ name }: { name: string }) {
  const common = {
    width: 22, height: 22, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 1.8,
    strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  }
  switch (name) {
    case 'Performance': // rising equity line
      return <svg {...common}><path d="M3 17l5.2-5.2 3.6 3.6L21 7" /><path d="M15 7h6v6" /></svg>
    case 'Accounts': // wallet
      return <svg {...common}><rect x="3" y="6" width="18" height="13" rx="2.5" /><path d="M16 12.5h.01" /><path d="M3 9.5h18" /></svg>
    case 'Desk': // order ticket
      return <svg {...common}><rect x="4" y="3.5" width="16" height="17" rx="2.5" /><path d="M8 8h8M8 12h8M8 16h4" /></svg>
    case 'Meridian': // voice orb
      return <svg {...common}><circle cx="12" cy="12" r="8.5" /><path d="M8.5 10.5v3M12 8v8M15.5 10.5v3" /></svg>
    default: // More: ellipsis
      return <svg {...common}><circle cx="5" cy="12" r="1.1" fill="currentColor" /><circle cx="12" cy="12" r="1.1" fill="currentColor" /><circle cx="19" cy="12" r="1.1" fill="currentColor" /></svg>
  }
}

function TabBar() {
  const { pathname } = useLocation()
  const [more, setMore] = useState(false)
  // Route change closes the sheet — a tap either navigates or dismisses.
  useEffect(() => { setMore(false) }, [pathname])
  const moreActive = MORE_TABS.some((t) => t.to === pathname)
  return (
    <>
      {more && <div className="sheet-overlay" onClick={() => setMore(false)} aria-hidden />}
      {more && (
        <div className="sheet" role="dialog" aria-label="More pages">
          <div className="sheet-grab" aria-hidden />
          {MORE_TABS.map((t) => (
            <NavLink key={t.to} to={t.to}
              className={({ isActive }) => `sheet-link${isActive ? ' active' : ''}`}>
              {t.label}
            </NavLink>
          ))}
        </div>
      )}
      <nav className="tabbar" aria-label="Primary">
        {MOBILE_TABS.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.to === '/'}
            className={({ isActive }) => `tabbar-item${isActive && !more ? ' active' : ''}`}>
            <TabIcon name={t.label} />
            <span>{t.label}</span>
          </NavLink>
        ))}
        <button type="button"
          className={`tabbar-item${more || moreActive ? ' active' : ''}`}
          aria-expanded={more}
          onClick={() => setMore((v) => !v)}>
          <TabIcon name="More" />
          <span>More</span>
        </button>
      </nav>
    </>
  )
}

/** UTC + local console clock: seconds tick because they are data. */
function NavClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  const utc = now.toISOString().slice(11, 19)
  return <span className="nd-clock">{utc}Z</span>
}

/** The single sliding amber caret under the active tab. */
function NavCaret({ nav }: { nav: React.RefObject<HTMLElement | null> }) {
  const { pathname } = useLocation()
  const [style, setStyle] = useState<{ transform: string; width: number } | null>(null)
  useEffect(() => {
    const el = nav.current?.querySelector('a.tab.active') as HTMLElement | null
    if (!el || !nav.current) { setStyle(null); return }
    const nb = nav.current.getBoundingClientRect()
    const b = el.getBoundingClientRect()
    setStyle({ transform: `translateX(${b.left - nb.left}px)`, width: b.width })
  }, [pathname, nav])
  if (!style) return null
  return <span className="nd-caret" style={{ transform: style.transform, width: style.width }} aria-hidden />
}

/** Standalone (Add to Home Screen) detection → body class, so CSS can pad
 * for the translucent status bar and home indicator only when installed. */
function useStandaloneClass() {
  useEffect(() => {
    const mq = window.matchMedia?.('(display-mode: standalone)')
    const legacy =
      (navigator as unknown as { standalone?: boolean }).standalone === true
    const apply = () =>
      document.body.classList.toggle('standalone', legacy || !!mq?.matches)
    apply()
    mq?.addEventListener?.('change', apply)
    return () => mq?.removeEventListener?.('change', apply)
  }, [])
}

export default function App() {
  // Keying the content wrapper by path replays the entrance choreography
  // on every route change — navigation feels composed, not swapped.
  const { pathname } = useLocation()
  useStandaloneClass()
  // The MERIDIAN cockpit is a full-screen room with its own chrome (brand
  // strip, exit ✕). Site nav/tabbar must not float over it — and the
  // page-fade animation would trap its position:fixed root in a stacking
  // context under them — so /jarvis gets a bare, chrome-less main. The
  // TV wall boards (/wall/*) are full-screen always-on displays and get
  // the same treatment. The Desk (owner order 2026-08-28) is a venue
  // PORTAL — indistinguishable from Polymarket/Kalshi themselves — so
  // it owns its full viewport too: no site chrome may frame the venue.
  const cockpit = pathname === '/jarvis' || pathname === '/meridian'
    || pathname === '/desk' || pathname.startsWith('/wall')
  const navRef = useRef<HTMLElement | null>(null)
  return (
    <div className="app">
      {!cockpit && (
        <div className="nd-aurora" aria-hidden><i /><i /><i /></div>
      )}
      {!cockpit && (
      <nav className="nav" ref={navRef} style={{ position: 'sticky' }}>
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
        <NavClock />
        <PulseLine />
        <NavCaret nav={navRef} />
      </nav>
      )}
      <main className={cockpit ? 'main' : 'main page-fade'} key={pathname}>
        <Routes>
          <Route path="/" element={<TrackRecord />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/accounts" element={<Suspense fallback={null}><Accounts /></Suspense>} />
          <Route path="/system" element={<System />} />
          <Route path="/engine" element={<Engine />} />
          <Route path="/desk" element={<TradeDesk />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="/reports" element={<Suspense fallback={null}><Reports /></Suspense>} />
          {/* MERIDIAN is the product name (owner order 2026-08-28);
              /jarvis stays as an alias so old bookmarks keep working. */}
          <Route path="/meridian" element={<Suspense fallback={null}><Jarvis /></Suspense>} />
          <Route path="/jarvis" element={<Suspense fallback={null}><Jarvis /></Suspense>} />
          <Route path="/wall/*" element={<Suspense fallback={null}><Wall /></Suspense>} />
        </Routes>
        {!cockpit && (
        <p className="notice">
          Performance shown is the whale copy portfolio: every settled copy trade,
          uncapped. Full account statements available to investors on request.
          All figures are read live from the platform's own order-level ledger —
          nothing on this site is entered by hand. Informational only — not betting
          or investment advice. Not affiliated with any prediction market operator.
          {' '}<span style={{ opacity: 0.55 }}>build {__BUILD_SHA__.replace('bsha_', '')}</span>
        </p>
        )}
      </main>
      {!cockpit && <Tape />}
      {!cockpit && <TabBar />}
      {!cockpit && <CommandPalette />}
    </div>
  )
}

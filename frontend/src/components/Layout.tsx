import { useState, useEffect, useRef } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

const navItems = [
  { to: '/',          label: 'Dashboard', icon: '◈' },
  { to: '/scanner',   label: 'Scanner',   icon: '⌖' },
  { to: '/decision',  label: 'Decision',  icon: '◇' },
  { to: '/portfolio', label: 'Carteira',  icon: '◉' },
]

export function Layout() {
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()
  const drawerRef = useRef<HTMLDivElement>(null)

  // Close menu on route change
  useEffect(() => { setMenuOpen(false) }, [location.pathname])

  // Close on outside tap
  useEffect(() => {
    if (!menuOpen) return
    const handle = (e: MouseEvent) => {
      if (drawerRef.current && !drawerRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [menuOpen])

  // Prevent body scroll when menu open on mobile
  useEffect(() => {
    document.body.style.overflow = menuOpen ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [menuOpen])

  const currentLabel = navItems.find(n => {
    if (n.to === '/') return location.pathname === '/'
    return location.pathname.startsWith(n.to)
  })?.label ?? 'Apex'

  return (
    <div className="app-shell">
      {/* ── Sidebar (desktop) / Drawer (mobile) ── */}
      {menuOpen && <div className="drawer-backdrop" onClick={() => setMenuOpen(false)} />}

      <aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`} ref={drawerRef}>
        <div className="brand-wrap">
          <div className="brand-kicker">Apex Terminal</div>
          <div className="brand">Apex</div>
          <div className="brand-subtitle">Investment Discovery & Portfolio Intelligence</div>
        </div>

        <nav className="nav">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) => `nav-link ${isActive ? 'nav-link-active' : ''}`}
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-stat-label">Modo</div>
          <div className="sidebar-stat-value">Long-term spot</div>
          <div className="muted small">Scanner orientado a semanas, meses e anos.</div>
        </div>
      </aside>

      {/* ── Main content ── */}
      <main className="content">
        {/* Mobile top bar */}
        <div className="mobile-topbar">
          <button
            className="hamburger"
            onClick={() => setMenuOpen(v => !v)}
            aria-label="Menu"
          >
            <span className={`ham-line ${menuOpen ? 'ham-open' : ''}`} />
            <span className={`ham-line ${menuOpen ? 'ham-open' : ''}`} />
            <span className={`ham-line ${menuOpen ? 'ham-open' : ''}`} />
          </button>
          <span className="mobile-page-title">{currentLabel}</span>
          <span className="mobile-brand">APEX</span>
        </div>

        {/* Desktop top bar */}
        <div className="topbar desktop-only">
          <div>
            <div className="topbar-title">Bloomberg-style overview</div>
            <div className="topbar-subtitle">Radar visual para scanner, carteira e tese de investimento.</div>
          </div>
          <div className="topbar-badges">
            <span className="terminal-badge">DISCOVERY</span>
            <span className="terminal-badge terminal-badge-warn">PORTFOLIO</span>
          </div>
        </div>

        <Outlet />

        {/* Mobile bottom navigation */}
        <nav className="bottom-nav">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) => `bottom-nav-item ${isActive ? 'bottom-nav-active' : ''}`}
            >
              <span className="bottom-nav-icon">{item.icon}</span>
              <span className="bottom-nav-label">{item.label}</span>
            </NavLink>
          ))}
        </nav>
      </main>
    </div>
  )
}

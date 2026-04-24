import { useState, useEffect, useRef } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'

const navItems = [
  { to: '/',          label: 'Dashboard', icon: '◈' },
  { to: '/scanner',   label: 'Scanner',   icon: '⌖' },
  { to: '/decision',  label: 'Decision',  icon: '◇' },
  { to: '/portfolio', label: 'Carteira',  icon: '◉' },
]

interface SearchResult {
  ticker: string
  name: string
  sector?: string
  current_price: number | null
}

function GlobalSearch() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [ingesting, setIngesting] = useState(false)
  const timeout = useRef<number | null>(null)

  useEffect(() => {
    if (!query || query.length < 1) {
      setResults([])
      setOpen(false)
      return
    }
    if (timeout.current) window.clearTimeout(timeout.current)
    timeout.current = window.setTimeout(async () => {
      setLoading(true)
      try {
        const res = await api.get<SearchResult[]>(`/assets/search?q=${encodeURIComponent(query)}`)
        setResults(res)
        setOpen(true)
      } catch {
        setResults([])
      } finally {
        setLoading(false)
      }
    }, 200)
    return () => { if (timeout.current) window.clearTimeout(timeout.current) }
  }, [query])

  async function handleIngest(ticker: string) {
    setIngesting(true)
    try {
      await api.post(`/assets/${ticker}/ingest`)
      // Now navigate to the asset page
      handleSelect(ticker)
    } catch {
      // Try anyway
      handleSelect(ticker)
    } finally {
      setIngesting(false)
    }
  }

  function handleSelect(ticker: string) {
    setQuery('')
    setOpen(false)
    navigate(`/asset/${ticker}`)
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && results.length > 0) handleSelect(results[0].ticker)
    if (e.key === 'Escape') { setQuery(''); setOpen(false) }
  }

  return (
    <div className="global-search">
      <div className="global-search-input-wrap">
        <span className="search-icon">⌕</span>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value.toUpperCase())}
          onKeyDown={handleKeyDown}
          onBlur={() => setTimeout(() => setOpen(false), 200)}
          onFocus={() => results.length > 0 && setOpen(true)}
          placeholder="Pesquisar ticker..."
          autoComplete="off"
          className="global-search-input"
        />
        {loading && <span className="search-loading">…</span>}
      </div>
      {query.length >= 1 && !loading && (
        <div className="global-search-dropdown">
          {results.length > 0 ? results.slice(0, 8).map((r) => (
            <div key={r.ticker} className="global-search-item" onMouseDown={() => handleSelect(r.ticker)}>
              <div className="gs-left">
                <strong className="gs-ticker">{r.ticker}</strong>
                <span className="gs-name">{r.name}</span>
              </div>
              <div className="gs-right">
                {r.current_price != null && <span className="gs-price">${r.current_price.toFixed(2)}</span>}
                {r.sector && <span className="gs-sector">{r.sector}</span>}
              </div>
            </div>
          )) : (
            <div className="global-search-item gs-not-found">
              <div className="gs-left">
                <strong className="gs-ticker">{query}</strong>
                <span className="gs-name">Não encontrado na DB</span>
              </div>
              <button
                className="btn-ingest"
                onMouseDown={() => handleIngest(query)}
                disabled={ingesting}
              >
                {ingesting ? '⏳ A adicionar…' : '+ Adicionar à DB'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function Layout() {
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()
  const drawerRef = useRef<HTMLDivElement>(null)

  useEffect(() => { setMenuOpen(false) }, [location.pathname])

  useEffect(() => {
    if (!menuOpen) return
    const handle = (e: MouseEvent) => {
      if (drawerRef.current && !drawerRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [menuOpen])

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
      {menuOpen && <div className="drawer-backdrop" onClick={() => setMenuOpen(false)} />}

      <aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`} ref={drawerRef}>
        <div className="brand-wrap">
          <div className="brand-kicker">Apex Terminal</div>
          <div className="brand">Apex</div>
          <div className="brand-subtitle">Investment Discovery & Portfolio Intelligence</div>
        </div>

        <div className="sidebar-search">
          <GlobalSearch />
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

      <main className="content">
        <div className="mobile-topbar">
          <button className="hamburger" onClick={() => setMenuOpen(v => !v)} aria-label="Menu">
            <span className={`ham-line ${menuOpen ? 'ham-open' : ''}`} />
            <span className={`ham-line ${menuOpen ? 'ham-open' : ''}`} />
            <span className={`ham-line ${menuOpen ? 'ham-open' : ''}`} />
          </button>
          <span className="mobile-page-title">{currentLabel}</span>
          <div className="mobile-search-wrap">
            <GlobalSearch />
          </div>
        </div>

        <div className="topbar desktop-only">
          <div>
            <div className="topbar-title">Bloomberg-style overview</div>
            <div className="topbar-subtitle">Radar visual para scanner, carteira e tese de investimento.</div>
          </div>
          <div className="topbar-right">
            <GlobalSearch />
            <div className="topbar-badges">
              <span className="terminal-badge">DISCOVERY</span>
              <span className="terminal-badge terminal-badge-warn">PORTFOLIO</span>
            </div>
          </div>
        </div>

        <Outlet />

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

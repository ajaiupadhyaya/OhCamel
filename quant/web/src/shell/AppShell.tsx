/**
 * Global chrome: collapsible sidebar, top bar (palette trigger, portfolio chip, theme
 * toggle) and the routed content area. Mobile: the sidebar becomes a drawer.
 */
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { Icon } from "../components/Icon";
import { fmtPct } from "../lib/format";
import { useLocalStorage, useMediaQuery } from "../lib/hooks";
import { totalWeight, usePortfolio } from "../lib/portfolio";
import { NAV_SECTIONS, ROUTES } from "../lib/routes";
import { seriesVar, useTheme } from "../lib/theme";
import { CommandPalette } from "./CommandPalette";

const SECTIONS = NAV_SECTIONS;

function Logo({ collapsed }: { collapsed: boolean }) {
  return (
    <Link to="/" className="oc-logo" aria-label="OhCamel Quant home">
      <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden>
        <rect width="32" height="32" rx="8" fill="var(--text)" />
        <path d="M6 21.5c2.6-7.5 5.2-7.5 7-3.6s3.6 4.2 5.4-1.7 3.8-7 6.6-3.2" fill="none" stroke="var(--ochre)" strokeWidth="2.4" strokeLinecap="round" />
      </svg>
      {!collapsed && (
        <span className="oc-logo-text">
          <span className="display">OhCamel</span>
          <span className="oc-logo-sub">Quant</span>
        </span>
      )}
    </Link>
  );
}

function PortfolioChip() {
  const { portfolio } = usePortfolio();
  const top = [...portfolio.holdings].sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight)).slice(0, 3);
  const sum = totalWeight(portfolio.holdings);
  return (
    <Link to="/portfolio" className="oc-pchip" title="Active portfolio — open Portfolio Lab">
      <span className="oc-pchip-bars" aria-hidden>
        {portfolio.holdings.slice(0, 8).map((h, i) => (
          <span key={h.ticker} style={{ flex: Math.max(0.02, Math.abs(h.weight)), background: seriesVar(i) }} />
        ))}
      </span>
      <span className="oc-pchip-text">
        <span className="oc-pchip-name">{portfolio.name || "Untitled portfolio"}</span>
        <span className="oc-pchip-meta num">
          {portfolio.holdings.length} pos · {top.map((h) => h.ticker).join(" ")}
          {portfolio.holdings.length > 3 ? " …" : ""}
          {Math.abs(sum - 1) > 1e-6 ? ` · Σ ${fmtPct(sum, 0)}` : ""}
        </span>
      </span>
    </Link>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useLocalStorage("ohcamel.sidebar.collapsed", false);
  const [drawer, setDrawer] = useState(false);
  const [palette, setPalette] = useState(false);
  const mobile = useMediaQuery("(max-width: 900px)");
  const { resolved, toggle } = useTheme();
  const loc = useLocation();

  useEffect(() => setDrawer(false), [loc.pathname]);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      } else if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement)?.tagName) && !(e.target as HTMLElement)?.isContentEditable) {
        e.preventDefault();
        setPalette(true);
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  const isCollapsed = !mobile && collapsed;
  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

  return (
    <div className={`oc-app ${isCollapsed ? "collapsed" : ""} ${mobile ? "mobile" : ""} ${drawer ? "drawer-open" : ""}`}>
      <div className="oc-sidebar-cell">
      <aside className="oc-sidebar" aria-label="Primary">
        <div className="oc-sidebar-top">
          <Logo collapsed={isCollapsed} />
          {mobile && (
            <button className="icon-btn" aria-label="Close menu" onClick={() => setDrawer(false)}>
              <Icon name="x" />
            </button>
          )}
        </div>
        <nav className="oc-nav">
          {SECTIONS.map((sec) => (
            <div key={sec} className="oc-nav-section">
              {!isCollapsed && sec !== "Home" && <div className="oc-nav-heading">{sec}</div>}
              {ROUTES.filter((r) => r.nav && r.section === sec).map((r) => (
                <NavLink
                  key={r.path}
                  to={r.to}
                  end={r.path === "/"}
                  className={({ isActive }) => `oc-nav-link ${isActive || (r.path.includes(":") && loc.pathname.startsWith(r.path.split("/:")[0] + "/")) ? "active" : ""}`}
                  title={isCollapsed ? r.title : undefined}
                >
                  <Icon name={r.icon} size={18} />
                  {!isCollapsed && <span>{r.title}</span>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        {!mobile && (
          <button className="oc-collapse" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            <Icon name={collapsed ? "chevron-right" : "chevron-left"} size={16} />
            {!isCollapsed && <span>Collapse</span>}
          </button>
        )}
      </aside>
      </div>
      {mobile && drawer && <div className="oc-scrim" onClick={() => setDrawer(false)} />}

      <div className="oc-main">
        <header className="oc-topbar">
          {mobile && (
            <button className="icon-btn" aria-label="Open menu" onClick={() => setDrawer(true)}>
              <Icon name="menu" />
            </button>
          )}
          {mobile && <Logo collapsed />}
          <button className="oc-search-trigger" onClick={() => setPalette(true)} aria-label="Search (Cmd/Ctrl-K)">
            <Icon name="search" size={16} />
            <span className="oc-search-placeholder">Search tickers, companies, pages…</span>
            <span className="oc-search-kbd">
              <span className="kbd">{isMac ? "⌘" : "Ctrl"}</span>
              <span className="kbd">K</span>
            </span>
          </button>
          <div className="spacer" />
          <PortfolioChip />
          <button className="icon-btn" onClick={toggle} aria-label={`Switch to ${resolved === "dark" ? "light" : "dark"} mode`} title={`Switch to ${resolved === "dark" ? "light" : "dark"} mode`}>
            <Icon name={resolved === "dark" ? "sun" : "moon"} size={18} />
          </button>
        </header>
        <main className="oc-content" id="main">
          {children}
        </main>
      </div>
      <CommandPalette open={palette} onClose={() => setPalette(false)} onToggleTheme={toggle} />
    </div>
  );
}

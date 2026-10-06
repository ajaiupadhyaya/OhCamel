/**
 * The function-code row: one code per nav route (full title on hover), the active code
 * inverted, the command button and the theme toggle. On a narrow screen the codes scroll
 * inside their own row; the page never scrolls sideways.
 */
import { useEffect, useRef } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { navRoutes, type RouteDef } from "../lib/routes";
import { useTheme } from "../lib/theme";

function isActive(r: RouteDef, pathname: string, matched: boolean): boolean {
  if (matched) return true;
  // /company/:ticker is active on /company and /company/AAPL alike.
  return r.path.includes(":") && pathname.startsWith(r.path.split("/:")[0]);
}

export function FunctionNav({ onCommand }: { onCommand: () => void }) {
  const { resolved, toggle } = useTheme();
  const { pathname } = useLocation();
  const next = resolved === "carbon" ? "paper" : "carbon";
  const row = useRef<HTMLElement>(null);

  // Keep the active code in view inside the row (narrow screens), without moving the page.
  useEffect(() => {
    const el = row.current;
    const a = el?.querySelector<HTMLElement>(".oc-fcode.active");
    if (!el || !a) return;
    const left = a.offsetLeft - el.offsetLeft;
    if (left < el.scrollLeft || left + a.offsetWidth > el.scrollLeft + el.clientWidth) el.scrollLeft = left - 8;
  }, [pathname]);

  return (
    <div className="oc-fnav">
      <nav aria-label="Sections" className="oc-fnav-codes" ref={row}>
        {navRoutes().map((r) => (
          <NavLink key={r.path} to={r.to} end={r.path === "/"} title={r.title} className={({ isActive: m }) => `oc-fcode${isActive(r, pathname, m) ? " active" : ""}`}>
            {r.code}
          </NavLink>
        ))}
      </nav>
      <div className="oc-fnav-tools">
        <button type="button" className="oc-ftool" onClick={onCommand} aria-label="Command line (/ or Ctrl-K)" title="Command line — / or ⌘K">
          <span className="oc-ftool-key" aria-hidden>
            /
          </span>
          <span className="oc-ftool-text">CMD</span>
        </button>
        <button type="button" className="oc-ftool" onClick={toggle} aria-label={`Theme ${resolved}; switch to ${next}`} title={`Switch to ${next}`}>
          {resolved.toUpperCase()}
        </button>
      </div>
    </div>
  );
}

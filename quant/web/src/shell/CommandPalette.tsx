/**
 * Cmd/Ctrl-K palette: page jumps + ticker search (GET /api/market/search).
 * Typing a bare ticker and pressing Enter opens /ticker/<TICKER> even if search is down.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { Icon } from "../components/Icon";
import { useDebounced } from "../lib/hooks";
import { useTickerSearch } from "../lib/market";
import { cleanTicker } from "../lib/portfolio";
import { ROUTES } from "../lib/routes";

interface Item {
  id: string;
  kind: "page" | "ticker" | "action";
  label: string;
  hint?: string;
  icon: Parameters<typeof Icon>[0]["name"];
  run: () => void;
}

export function CommandPalette({ open, onClose, onToggleTheme }: { open: boolean; onClose: () => void; onToggleTheme: () => void }) {
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const nav = useNavigate();
  const dq = useDebounced(q, 140);
  const search = useTickerSearch(dq, open);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (open) {
      setQ("");
      setHi(0);
      setTimeout(() => input.current?.focus(), 0);
    }
  }, [open]);

  const items = useMemo<Item[]>(() => {
    const go = (to: string) => () => (nav(to), onClose());
    const needle = q.trim().toLowerCase();
    const pages: Item[] = ROUTES.filter((r) => r.nav || r.path.startsWith("/ticker"))
      .filter((r) => !needle || `${r.title} ${r.keywords ?? ""} ${r.blurb}`.toLowerCase().includes(needle))
      .map((r) => ({ id: `page:${r.path}`, kind: "page", label: r.title, hint: r.blurb, icon: r.icon, run: go(r.to) }));
    const tickers: Item[] = (search.data ?? []).slice(0, 8).map((h) => ({ id: `t:${h.ticker}`, kind: "ticker", label: h.ticker, hint: h.name !== h.ticker ? h.name : "Open ticker", icon: "ticker", run: go(`/ticker/${h.ticker}`) }));
    const raw = cleanTicker(q);
    if (raw && raw.length <= 10 && /^[A-Z0-9.^=-]+$/.test(raw) && !tickers.some((t) => t.label === raw)) {
      tickers.push({ id: `t-raw:${raw}`, kind: "ticker", label: raw, hint: "Open ticker page", icon: "arrow-right", run: go(`/ticker/${raw}`) });
      tickers.push({ id: `c-raw:${raw}`, kind: "ticker", label: `${raw} fundamentals`, hint: "Open company page", icon: "company", run: go(`/company/${raw}`) });
    }
    const actions: Item[] = [{ id: "a:theme", kind: "action", label: "Toggle light / dark", icon: "moon", run: () => (onToggleTheme(), onClose()) }].filter((a) => !needle || a.label.toLowerCase().includes(needle)) as Item[];
    return needle ? [...tickers, ...pages, ...actions] : [...pages, ...actions];
  }, [q, search.data, nav, onClose, onToggleTheme]);

  useEffect(() => setHi(0), [q]);
  useEffect(() => {
    list.current?.querySelector(`[data-idx="${hi}"]`)?.scrollIntoView({ block: "nearest" });
  }, [hi]);

  if (!open) return null;
  let lastKind = "";
  return createPortal(
    <div className="oc-palette-overlay" onMouseDown={onClose}>
      <div className="oc-palette" role="dialog" aria-modal="true" aria-label="Command palette" onMouseDown={(e) => e.stopPropagation()}>
        <div className="oc-palette-search">
          <Icon name="search" size={18} />
          <input
            ref={input}
            autoFocus
            value={q}
            placeholder="Search tickers, companies or pages…"
            spellCheck={false}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
            e.preventDefault();
            setHi((h) => Math.min(h + 1, items.length - 1));
          }
              else if (e.key === "ArrowUp") {
            e.preventDefault();
            setHi((h) => Math.max(h - 1, 0));
          }
              else if (e.key === "Enter") {
            e.preventDefault();
            items[hi]?.run();
          }
              else if (e.key === "Escape") onClose();
            }}
          />
          {search.isFetching && <span className="oc-palette-spin" aria-hidden />}
          <span className="kbd">esc</span>
        </div>
        <ul className="oc-palette-list" ref={list} role="listbox">
          {items.length === 0 && <li className="oc-palette-empty">No matches.</li>}
          {items.map((it, i) => {
            const header = it.kind !== lastKind ? (it.kind === "page" ? "Pages" : it.kind === "ticker" ? "Tickers" : "Actions") : null;
            lastKind = it.kind;
            return (
              <li key={it.id} role="presentation">
                {header && <div className="oc-palette-group">{header}</div>}
                <div role="option" aria-selected={i === hi} data-idx={i} className={`oc-palette-item ${i === hi ? "active" : ""}`} onMouseMove={() => setHi(i)} onClick={it.run}>
                  <Icon name={it.icon} size={16} />
                  <span className={it.kind === "ticker" ? "num oc-palette-label" : "oc-palette-label"}>{it.label}</span>
                  {it.hint && <span className="oc-palette-hint">{it.hint}</span>}
                  {i === hi && <Icon name="arrow-right" size={14} className="oc-palette-go" />}
                </div>
              </li>
            );
          })}
        </ul>
        <div className="oc-palette-foot">
          <span><span className="kbd">↑</span> <span className="kbd">↓</span> navigate</span>
          <span><span className="kbd">↵</span> open</span>
          {search.isError && <span className="warn">ticker search unavailable</span>}
        </div>
      </div>
    </div>,
    document.body,
  );
}

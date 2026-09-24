/**
 * Ticker input with autocomplete against GET /api/market/search.
 *   <TickerInput onSelect={(t) => add(t)} placeholder="Add ticker…" />
 * Enter with no highlighted suggestion submits the typed text (uppercased), so tickers the
 * search index doesn't know can still be used. `clearOnSelect` (default true) resets the field.
 */
import { useEffect, useRef, useState } from "react";
import { useDebounced } from "../lib/hooks";
import { useTickerSearch, type SearchHit } from "../lib/market";
import { cleanTicker } from "../lib/portfolio";
import { Icon } from "./Icon";

export function TickerInput({ onSelect, placeholder = "Search ticker or company…", clearOnSelect = true, autoFocus, exclude, className, initial = "" }: { onSelect: (ticker: string, hit?: SearchHit) => void; placeholder?: string; clearOnSelect?: boolean; autoFocus?: boolean; exclude?: string[]; className?: string; initial?: string }) {
  const [text, setText] = useState(initial);
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(-1);
  const wrap = useRef<HTMLDivElement>(null);
  const q = useDebounced(text, 160);
  const search = useTickerSearch(q, open);
  const hits = (search.data ?? []).filter((h) => !exclude?.includes(h.ticker)).slice(0, 8);

  useEffect(() => setHi(-1), [q]);
  useEffect(() => {
    const on = (e: MouseEvent) => !wrap.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", on);
    return () => document.removeEventListener("mousedown", on);
  }, []);

  const choose = (t: string, hit?: SearchHit) => {
    const tk = cleanTicker(t);
    if (!tk) return;
    onSelect(tk, hit);
    setText(clearOnSelect ? "" : tk);
    setOpen(false);
  };

  return (
    <div className={`oc-tickerinput ${className ?? ""}`} ref={wrap}>
      <Icon name="search" size={14} className="oc-tickerinput-icon" />
      <input
        className="input"
        value={text}
        placeholder={placeholder}
        autoFocus={autoFocus}
        spellCheck={false}
        autoComplete="off"
        role="combobox"
        aria-expanded={open && hits.length > 0}
        aria-autocomplete="list"
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setHi((h) => Math.min(h + 1, hits.length - 1));
          }
          else if (e.key === "ArrowUp") {
            e.preventDefault();
            setHi((h) => Math.max(h - 1, -1));
          }
          else if (e.key === "Enter") {
            e.preventDefault();
            if (hi >= 0 && hits[hi]) choose(hits[hi].ticker, hits[hi]);
            else choose(text);
          } else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && text.trim() && (hits.length > 0 || search.isError) && (
        <ul className="oc-menu" role="listbox">
          {hits.map((h, i) => (
            <li key={h.ticker} role="option" aria-selected={i === hi} className={i === hi ? "active" : ""} onMouseEnter={() => setHi(i)} onMouseDown={(e) => (e.preventDefault(), choose(h.ticker, h))}>
              <span className="num oc-menu-ticker">{h.ticker}</span>
              <span className="oc-menu-name">{h.name !== h.ticker ? h.name : ""}</span>
            </li>
          ))}
          {search.isError && <li className="oc-menu-note">Search unavailable — press Enter to use “{cleanTicker(text)}”.</li>}
        </ul>
      )}
    </div>
  );
}

/**
 * The command line: one 32px mono input under the function-code row. Enter runs
 * commandPath(parseCommand(v)); a line that parses as a search lists up to 8 pages and
 * tickers below (arrow keys, Enter). With an empty line it lists the last 8 commands.
 * The right edge echoes what Enter will do. Esc closes and returns focus.
 *
 * KeyMap is the `?` overlay: global keys and the command grammar, as a ruled table.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useDebounced, useLocalStorage } from "../lib/hooks";
import { useTickerSearch } from "../lib/market";
import { ROUTES } from "../lib/routes";
import { commandPath, parseCommand, pushRecent, RECENT_MAX } from "./command";
import { CHORDS } from "./hotkeys";

interface Row {
  id: string;
  code: string;
  label: string;
  hint: string;
  /** Path to open, or a recent command line to re-run. */
  to?: string;
  line?: string;
}

const RECENT_KEY = "ohcamel.cmd.recent";

export function CommandLine({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNavigate();
  const uid = useId();
  const [v, setV] = useState("");
  const [hi, setHi] = useState(-1);
  const [recent, setRecent] = useLocalStorage<string[]>(RECENT_KEY, []);
  const input = useRef<HTMLInputElement>(null);
  const strip = useRef<HTMLDivElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);

  const cmd = useMemo(() => parseCommand(v), [v]);
  const path = commandPath(cmd);
  const query = cmd.kind === "search" ? cmd.query : "";
  const dq = useDebounced(query, 140);
  const search = useTickerSearch(dq, open && dq.length > 0);

  // Open: remember focus, clear, focus the input. Close: hand focus back.
  useEffect(() => {
    if (!open) return;
    returnTo.current = document.activeElement as HTMLElement | null;
    setV("");
    setHi(-1);
    const t = setTimeout(() => input.current?.focus(), 0);
    return () => {
      clearTimeout(t);
      const el = returnTo.current;
      if (el && el.isConnected && el !== document.body) el.focus({ preventScroll: true });
    };
  }, [open]);

  // A press outside the strip closes it.
  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => {
      if (strip.current && !strip.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", down);
    return () => document.removeEventListener("mousedown", down);
  }, [open, onClose]);

  const rows = useMemo<Row[]>(() => {
    if (!v.trim()) {
      const list = Array.isArray(recent) ? recent : [];
      return list.slice(0, RECENT_MAX).map((line, i) => {
        const p = commandPath(parseCommand(line));
        return { id: `r${i}`, code: "RECENT", label: line, hint: p ?? "SEARCH", line };
      });
    }
    if (cmd.kind !== "search") return [];
    const needle = query.toLowerCase();
    const pages: Row[] = ROUTES.filter((r) => `${r.code} ${r.title} ${r.keywords ?? ""}`.toLowerCase().includes(needle)).map((r) => ({
      id: `p:${r.path}`,
      code: r.code,
      label: r.title.toUpperCase(),
      hint: r.to,
      to: r.to,
    }));
    const tickers: Row[] = (search.data ?? []).map((h) => ({
      id: `t:${h.ticker}`,
      code: "GP",
      label: h.ticker,
      hint: h.name !== h.ticker ? h.name.toUpperCase() : `/ticker/${h.ticker}`,
      to: `/ticker/${h.ticker}`,
    }));
    return [...pages, ...tickers].slice(0, 8);
  }, [v, cmd.kind, query, recent, search.data]);

  useEffect(() => setHi(-1), [v]);

  if (!open) return null;

  const go = (to: string, line: string) => {
    setRecent((r) => pushRecent(r, line));
    nav(to);
    onClose();
  };

  const runRow = (r: Row) => {
    if (r.to) go(r.to, r.label);
    else if (r.line) {
      const p = commandPath(parseCommand(r.line));
      if (p) go(p, r.line);
      else setV(r.line);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (rows.length) setHi((h) => Math.min(h + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi((h) => Math.max(h - 1, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (hi >= 0 && rows[hi]) runRow(rows[hi]);
      else if (path) go(path, v);
      else if (rows.length) setHi(0); // a search: Enter selects the first result, Enter again opens it
    }
  };

  const listId = `${uid}-list`;
  const expanded = rows.length > 0;
  const echo = path ?? (cmd.kind === "search" ? (search.isFetching ? "SEARCHING" : "SEARCH") : "");
  const status =
    cmd.kind === "search" && dq && search.isError ? "TICKER SEARCH UNAVAILABLE" : cmd.kind === "search" && dq && !search.isFetching && rows.length === 0 ? "NO MATCH" : "";

  return (
    <div className="oc-cmd" ref={strip}>
      <div className="oc-cmd-line">
        <label className="oc-cmd-prompt" htmlFor={`${uid}-in`}>
          CMD
        </label>
        <input
          id={`${uid}-in`}
          ref={input}
          className="oc-cmd-input num"
          value={v}
          maxLength={200}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="characters"
          placeholder="SPY GP · AAPL DES · GO RATES"
          role="combobox"
          aria-expanded={expanded}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={hi >= 0 && rows[hi] ? `${uid}-${rows[hi].id}` : undefined}
          onChange={(e) => setV(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <span className="oc-cmd-echo num" aria-live="polite">
          {echo && (
            <>
              <span className="oc-cmd-arrow" aria-hidden>
                →
              </span>
              {echo}
            </>
          )}
        </span>
        <button type="button" className="oc-cmd-esc" onClick={onClose} aria-label="Close command line">
          ESC
        </button>
      </div>
      {(expanded || status) && (
        <div className="oc-cmd-results">
          {expanded && (
            <ul id={listId} role="listbox" aria-label={v.trim() ? "Results" : "Recent commands"} className="oc-cmd-list">
              {rows.map((r, i) => (
                <li
                  key={r.id}
                  id={`${uid}-${r.id}`}
                  role="option"
                  aria-selected={i === hi}
                  className={`oc-cmd-row${i === hi ? " active" : ""}`}
                  onMouseMove={() => setHi(i)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => runRow(r)}
                >
                  <span className="oc-cmd-code">{r.code}</span>
                  <span className="oc-cmd-label num">{r.label}</span>
                  <span className="oc-cmd-hint num">{r.hint}</span>
                </li>
              ))}
            </ul>
          )}
          {status && <p className="oc-cmd-status num">{status}</p>}
        </div>
      )}
    </div>
  );
}

const GLOBAL_KEYS: [string, string][] = [
  ["/", "COMMAND LINE"],
  ["⌘K · CTRL K", "COMMAND LINE"],
  ...CHORDS.map((c): [string, string] => [`g ${c.key}`, c.label]),
  ["?", "KEY MAP"],
];

const GRAMMAR: [string, string][] = [
  ["<CODE>", "PAGE BY FUNCTION CODE"],
  ["GO <CODE>", "PAGE"],
  ["<TICKER> GP", "PRICE HISTORY"],
  ["<TICKER> DES", "COMPANY"],
  ["<TICKER> OMON", "OPTION MONITOR"],
  ["PORT RISK 99 10D", "VAR · α 90 95 97.5 99 99.5 · 1–20D"],
  ["JOB <KIND>", "COMPUTE JOBS"],
];

export function KeyMap({ open, onClose }: { open: boolean; onClose: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  const uid = useId();
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    box.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape" || e.key === "?") {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", key, true);
    return () => {
      window.removeEventListener("keydown", key, true);
      if (prev && prev.isConnected && prev !== document.body) prev.focus({ preventScroll: true });
    };
  }, [open, onClose]);

  if (!open) return null;
  return createPortal(
    <div className="oc-keymap-overlay" onMouseDown={onClose}>
      <div className="oc-keymap" ref={box} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={`${uid}-t`} onMouseDown={(e) => e.stopPropagation()}>
        <div className="oc-keymap-head">
          <h2 id={`${uid}-t`} className="oc-keymap-title">
            KEY MAP
          </h2>
          <button type="button" className="oc-cmd-esc" onClick={onClose} aria-label="Close key map">
            ESC
          </button>
        </div>
        <table className="oc-keymap-table">
          <caption className="oc-keymap-cap">KEYS</caption>
          <tbody>
            {GLOBAL_KEYS.map(([k, a]) => (
              <tr key={k}>
                <th scope="row" className="num">
                  {k}
                </th>
                <td>{a}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <table className="oc-keymap-table">
          <caption className="oc-keymap-cap">COMMANDS</caption>
          <tbody>
            {GRAMMAR.map(([k, a]) => (
              <tr key={k}>
                <th scope="row" className="num">
                  {k}
                </th>
                <td>{a}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>,
    document.body,
  );
}

/**
 * The machine's 46 px bar (spec section 6.4): the wordmark, the site in plain words, the web mode, search and the New
 * York clock. It stands in for the masthead, tape and function codes, which step aside on `/`.
 */
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { commandPath, parseCommand } from "../../shell/command";
import { formatDateline } from "../../shell/dateline";
import { sessionOf, useOps } from "../../shell/ops";
import { search } from "./search";
import { shownLabel } from "./shown";
import type { MNode } from "./types";
import { viewSearch } from "./url";
import type { View } from "./url";
import { PHONE, useMedia } from "./useMedia";
import type { GraphIndex, WebMode } from "./web";

const PAGES: [string, string][] = [
  ["Tape", "/tape"], ["Markets", "/markets"], ["Risk", "/risk"], ["Portfolios", "/portfolio"], ["Optimizer", "/optimize"], ["Research", "/research"],
  ["Volatility", "/options"], ["Rates", "/macro"], ["Companies", "/company"], ["Desk", "/deck"], ["System", "/system"],
];
const MODES: [WebMode, string][] = [["web", "web"], ["reads", "reads"], ["feeds", "feeds"], ["none", "no web"]];

export interface TopBarProps {
  gi: GraphIndex;
  now: Date;
  /** The view, so that moving between the machine and the list keeps the selection. */
  view: View;
  /** The web mode, on the machine; the list lights nothing, so it has none. */
  mode?: WebMode;
  onMode?(mode: WebMode): void;
  onSelect(id: string): void;
  /** Whether this browser can draw the machine; the list links back to it only then. */
  drawable: boolean;
}

const links = PAGES.map(([word, path]) => (
  <Link key={path} to={path}>
    {word}
  </Link>
));

/**
 * The links as a menu, for a bar too narrow to hold them (machine.css says when), so the machine always reaches the
 * rest of the site. Open, it is the topmost thing: Esc closes it and goes no further, and so does a press outside.
 */
function PagesMenu({ other }: { other: ReactNode }) {
  const box = useRef<HTMLDetailsElement>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = (): void => {
      if (box.current) box.current.open = false;
    };
    const key = (e: KeyboardEvent): void => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      close();
      box.current?.querySelector("summary")?.focus();
    };
    const press = (e: PointerEvent): void => {
      if (!box.current?.contains(e.target as Node)) close();
    };
    window.addEventListener("keydown", key, true);
    document.addEventListener("pointerdown", press, true);
    return () => {
      window.removeEventListener("keydown", key, true);
      document.removeEventListener("pointerdown", press, true);
    };
  }, [open]);
  return (
    <details ref={box} className="mx-pages" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Pages</summary>
      <nav aria-label="Pages">
        {links}
        {other}
      </nav>
    </details>
  );
}

export function TopBar({ gi, now, view, mode, onMode, onSelect, drawable }: TopBarProps) {
  const ops = useOps();
  const [date, time, session] = formatDateline(now, sessionOf(ops.data)).split(" · ");
  const to = { search: viewSearch({ ...view, list: !view.list }) };
  // the same place in the other form: the bar has room for one short word, and the menu for the whole name
  const other = !drawable ? null : view.list ? ["machine", "The machine, drawn"] : ["list", "The machine as a list"];
  return (
    <header className="mx-top">
      <Link to="/" className="mx-brand">
        OhCamel
      </Link>
      <nav className="mx-seg mx-views" aria-label="Pages">
        {links}
      </nav>
      <PagesMenu
        other={
          other && (
            <Link className="mx-other" to={to}>
              {other[1]}
            </Link>
          )
        }
      />
      {mode && onMode && (
        <div className="mx-seg mx-modes" role="group" aria-label="Web mode">
          {MODES.map(([m, word]) => (
            <button key={m} type="button" className={m === mode ? "on" : undefined} aria-pressed={m === mode} onClick={() => onMode(m)}>
              {word}
            </button>
          ))}
        </div>
      )}
      {other && (
        <Link className="mx-aslist" to={to} aria-label={other[1]}>
          {other[0]}
        </Link>
      )}
      <Search gi={gi} onSelect={onSelect} />
      <span className="mx-status">
        <span className="mx-status-date">{date} · </span>
        <span className="mx-status-time">{time} · </span>
        <span className="mx-status-word">{session.slice(0, session.indexOf(" ") + 1)}</span>
        {session.slice(session.indexOf(" ") + 1)}
      </span>
    </header>
  );
}

export const hitName = (n: MNode): string => (n.k === "series" && n.n && n.n !== n.l ? `${n.l} · ${n.n}` : n.n || shownLabel(n));
const hitKind = (n: MNode): string => (typeof n.m.track === "string" && n.m.track !== "daily" ? `${n.k} · ${n.m.track}` : n.k);

function Search({ gi, onSelect }: { gi: GraphIndex; onSelect(id: string): void }) {
  const nav = useNavigate();
  const phone = useMedia(PHONE);
  const uid = useId();
  const input = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const [open, setOpen] = useState(false);

  const hits = useMemo(() => search(gi, q, 12), [gi, q]);
  const path = hits.length === 0 ? commandPath(parseCommand(q)) : null;
  const active = Math.min(hi, hits.length - 1);
  const showing = open && q.trim() !== "";

  const finish = (): void => {
    setOpen(false);
    setQ("");
    input.current?.blur();
  };
  const pick = (i: number): void => {
    onSelect(gi.out.nodes[i].id);
    finish();
  };
  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      input.current?.blur();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setHi(Math.max(0, Math.min(active + (e.key === "ArrowDown" ? 1 : -1), hits.length - 1)));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (hits.length > 0) pick(hits[active]);
      else if (path) {
        finish();
        nav(path);
      }
    }
  };

  const listId = `${uid}-list`;
  return (
    <div className="mx-search">
      <input
        id="mx-search"
        ref={input}
        value={q}
        placeholder={phone ? "find" : "find a ticker, job, model"}
        aria-label="Search the machine"
        role="combobox"
        aria-expanded={showing}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showing && hits.length > 0 ? `${uid}-${active}` : undefined}
        autoComplete="off"
        spellCheck={false}
        maxLength={200}
        onChange={(e) => {
          setQ(e.target.value);
          setHi(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {showing && (
        <div id={listId} className="mx-results" role="listbox" aria-label="Results" onMouseDown={(e) => e.preventDefault()}>
          {hits.map((i, k) => {
            const n = gi.out.nodes[i];
            return (
              <button key={n.id} id={`${uid}-${k}`} type="button" role="option" aria-selected={k === active} tabIndex={-1} className={k === active ? "hi" : undefined} onMouseMove={() => setHi(k)} onClick={() => pick(i)}>
                {hitName(n)}
                <span>{hitKind(n)}</span>
              </button>
            );
          })}
          {hits.length === 0 && <p>{path ? `Enter opens ${path}` : "no match"}</p>}
        </div>
      )}
    </div>
  );
}

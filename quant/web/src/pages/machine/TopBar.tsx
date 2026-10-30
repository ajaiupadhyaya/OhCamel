/**
 * The machine's 46 px bar (spec section 6.4): the wordmark, the site in plain words, the web mode, search and the New
 * York clock. It stands in for the masthead, tape and function codes, which step aside on `/`.
 */
import { useId, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { commandPath, parseCommand } from "../../shell/command";
import { formatDateline } from "../../shell/dateline";
import { sessionOf, useOps } from "../../shell/ops";
import { search } from "./search";
import type { MNode } from "./types";
import type { GraphIndex, WebMode } from "./web";

const PAGES: [string, string][] = [
  ["Tape", "/tape"], ["Markets", "/markets"], ["Risk", "/risk"], ["Portfolios", "/portfolio"], ["Optimizer", "/optimize"], ["Research", "/research"],
  ["Volatility", "/options"], ["Rates", "/macro"], ["Companies", "/company"], ["Desk", "/deck"], ["System", "/system"],
];
const MODES: [WebMode, string][] = [["web", "web"], ["reads", "reads"], ["feeds", "feeds"], ["none", "no web"]];

export interface TopBarProps {
  gi: GraphIndex;
  now: Date;
  mode: WebMode;
  onMode(mode: WebMode): void;
  onSelect(id: string): void;
}

export function TopBar({ gi, now, mode, onMode, onSelect }: TopBarProps) {
  const ops = useOps();
  return (
    <header className="mx-top">
      <Link to="/" className="mx-brand">
        OhCamel
      </Link>
      <nav className="mx-seg mx-views" aria-label="Pages">
        {PAGES.map(([word, path]) => (
          <Link key={path} to={path}>
            {word}
          </Link>
        ))}
      </nav>
      <div className="mx-seg" role="group" aria-label="Web mode">
        {MODES.map(([m, word]) => (
          <button key={m} type="button" className={m === mode ? "on" : undefined} aria-pressed={m === mode} onClick={() => onMode(m)}>
            {word}
          </button>
        ))}
      </div>
      <Search gi={gi} onSelect={onSelect} />
      <span className="mx-status">{formatDateline(now, sessionOf(ops.data))}</span>
    </header>
  );
}

const hitName = (n: MNode): string => (n.k === "series" && n.n && n.n !== n.l ? `${n.l} · ${n.n}` : n.n || n.l);
const hitKind = (n: MNode): string => (typeof n.m.track === "string" && n.m.track !== "daily" ? `${n.k} · ${n.m.track}` : n.k);

function Search({ gi, onSelect }: { gi: GraphIndex; onSelect(id: string): void }) {
  const nav = useNavigate();
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
        placeholder="find a ticker, job, model"
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

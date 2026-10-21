/**
 * Methodology (/methodology) -- the reference behind every number: each model (./methodology/
 * models.ts), the engine and the compute kernels (./methodology/compute.ts), the data sources
 * and limitations (./methodology/sources.ts) and one bibliography (./methodology/references.ts).
 * This file only lays them out: a ruled index, a find box, ruled entries (text left, formulas
 * right). No API calls. Pages link here with a Note marker (#<entry id>).
 */
import { useEffect, useMemo } from "react";
import { Link, useLocation } from "react-router-dom";
import { DataTable, Formula, Page, useTabParam, type Column } from "../components";
import { EXTRA_SECTIONS, KERNEL_API, type Entry } from "./methodology/compute";
import { MODELS, SECTIONS } from "./methodology/models";
import { citation, REFS, shortCite } from "./methodology/references";
import { LIMITATIONS, SOURCES, type Source } from "./methodology/sources";
import { fmtNum } from "../lib/format";
import "./methodology/methodology.css";

interface Group {
  id: string;
  title: string;
  entries: Entry[];
}

const GROUPS: Group[] = [
  ...SECTIONS.map((s) => ({ id: s.id, title: s.title, entries: MODELS.filter((m) => m.section === s.id) })),
  ...EXTRA_SECTIONS,
];
const ALL: (Entry & { group: string })[] = GROUPS.flatMap((g) => g.entries.map((e) => ({ ...e, group: g.title })));

function haystack(e: Entry & { group: string }): string {
  return [e.id, e.name, e.summary, e.keywords ?? "", e.assumptions.join(" "), e.refs.map(citation).join(" "), e.group].join(" ").toLowerCase();
}

const SOURCE_COLS: Column<Source>[] = [
  {
    key: "name",
    label: "SOURCE",
    width: "18%",
    wrap: true,
    render: (s) => (
      <>
        <a href={s.href} target="_blank" rel="noreferrer" className="me-src-name">
          {s.name}
        </a>
        {s.offline && <span className="me-src-flag num">OFFLINE SUBSET</span>}
      </>
    ),
  },
  { key: "supplies", label: "SUPPLIES", wrap: true },
  { key: "cadence", label: "REFRESH", wrap: true, hideBelow: 900 },
  { key: "notes", label: "CAVEATS", wrap: true, hideBelow: 1200 },
];

export default function Methodology() {
  const [q, setQ] = useTabParam<string>("q", "");
  const loc = useLocation();
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const hay = useMemo(() => new Map(ALL.map((e) => [e.id, haystack(e)])), []);
  const match = (e: Entry) => terms.every((t) => hay.get(e.id)!.includes(t));
  const shown = ALL.filter(match);
  const cited = useMemo(() => {
    const keys = new Set(ALL.flatMap((m) => m.refs));
    return Object.keys(REFS)
      .filter((k) => keys.has(k))
      .sort((a, b) => REFS[a].authors.localeCompare(REFS[b].authors) || REFS[a].year.localeCompare(REFS[b].year));
  }, []);

  // Deep links (#entry-id, #data-sources …): scroll once the lazily-loaded page has rendered.
  useEffect(() => {
    if (!loc.hash) return;
    const t = window.setTimeout(() => document.getElementById(decodeURIComponent(loc.hash.slice(1)))?.scrollIntoView({ block: "start" }), 60);
    return () => window.clearTimeout(t);
  }, [loc.hash]);

  return (
    <Page
      title="Methodology"
      meta={
        <>
          <span>{fmtNum(MODELS.length, 0)} MODELS</span>
          <span>{fmtNum(KERNEL_API.length, 0)} KERNELS</span>
          <span>{fmtNum(cited.length, 0)} REFERENCES</span>
          <span>{fmtNum(SOURCES.length, 0)} SOURCES</span>
        </>
      }
    >
      <div className="me-layout">
        <nav className="me-toc" aria-label="Contents">
          <div className="me-toc-label">ENTRIES</div>
          {GROUPS.map((g) => {
            const n = g.entries.filter(match).length;
            return (
              <a key={g.id} href={`#sec-${g.id}`} className={n ? "" : "dim"}>
                <span>{g.title}</span>
                <span className="num">{fmtNum(n, 0)}</span>
              </a>
            );
          })}
          <div className="me-toc-label">REFERENCE</div>
          <a href="#data-sources">
            <span>Data sources</span>
            <span className="num">{fmtNum(SOURCES.length, 0)}</span>
          </a>
          <a href="#limitations">
            <span>Limitations</span>
            <span className="num">{fmtNum(LIMITATIONS.length, 0)}</span>
          </a>
          <a href="#bibliography">
            <span>Bibliography</span>
            <span className="num">{fmtNum(cited.length, 0)}</span>
          </a>
        </nav>

        <div className="me-main">
          <label className="me-search">
            <span className="me-search-label">FIND</span>
            <input type="search" placeholder="sharpe · ledoit · garch · cscv" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find in the methodology" />
            <span className="num me-search-n">{terms.length ? `${fmtNum(shown.length, 0)} / ${fmtNum(ALL.length, 0)}` : ""}</span>
          </label>
          {terms.length > 0 && shown.length === 0 && (
            <div className="me-empty">
              <span className="num">NO MATCH · {q.toUpperCase()}</span>
              <button type="button" className="btn btn-sm" onClick={() => setQ("")}>
                CLEAR
              </button>
            </div>
          )}

          {GROUPS.map((g) => {
            const es = g.entries.filter(match);
            if (!es.length) return null;
            return (
              <section key={g.id} id={`sec-${g.id}`} className="me-section">
                <h2 className="me-section-title">
                  {g.title} <span className="num">{fmtNum(es.length, 0)}</span>
                </h2>
                {es.map((m) => (
                  <EntryRow key={m.id} m={m} />
                ))}
              </section>
            );
          })}

          <section id="data-sources" className="me-section">
            <h2 className="me-section-title">
              Data sources <span className="num">{fmtNum(SOURCES.length, 0)}</span>
            </h2>
            <DataTable<Source> columns={SOURCE_COLS} rows={SOURCES} rowKey={(s) => s.name} />
          </section>

          <section id="limitations" className="me-section">
            <h2 className="me-section-title">
              Limitations <span className="num">{fmtNum(LIMITATIONS.length, 0)}</span>
            </h2>
            <ol className="me-limits">
              {LIMITATIONS.map((l, i) => (
                <li key={l.title}>
                  <span className="num me-n">{String(i + 1).padStart(2, "0")}</span>
                  <span className="me-limit-title">{l.title}</span>
                  <span className="me-limit-text">{l.text}</span>
                </li>
              ))}
            </ol>
          </section>

          <section id="bibliography" className="me-section">
            <h2 className="me-section-title">
              Bibliography <span className="num">{fmtNum(cited.length, 0)}</span>
            </h2>
            <ol className="me-bib">
              {cited.map((k) => {
                const r = REFS[k];
                const users = ALL.filter((m) => m.refs.includes(k));
                return (
                  <li key={k} id={`ref-${k}`}>
                    <span className="me-bib-authors">{r.authors}</span> ({r.year}).{" "}
                    {r.href ? (
                      <a href={r.href} target="_blank" rel="noreferrer">
                        <em>{r.title.replace(/\.$/, "")}</em>
                      </a>
                    ) : (
                      <em>{r.title.replace(/\.$/, "")}</em>
                    )}
                    . <span className="me-bib-venue">{r.venue.replace(/\.$/, "")}.</span>
                    <span className="me-bib-used num">
                      {users.map((m) => (
                        <a key={m.id} href={`#${m.id}`}>
                          {m.id}
                        </a>
                      ))}
                    </span>
                  </li>
                );
              })}
            </ol>
          </section>
        </div>
      </div>
    </Page>
  );
}

function EntryRow({ m }: { m: Entry }) {
  return (
    <article id={m.id} className="me-entry">
      <div className="me-entry-text">
        <h3 className="me-entry-name">
          <a href={`#${m.id}`} className="me-anchor num" aria-label={`Link to ${m.name}`}>
            §
          </a>
          {m.name}
        </h3>
        <p className="me-summary">{m.summary}</p>
        {m.assumptions.length > 0 && (
          <>
            <div className="me-label">ASSUMPTIONS</div>
            <ul className="me-assume">
              {m.assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </>
        )}
        {m.appears.length > 0 && (
          <div className="me-appears">
            <span className="me-label">SEEN ON</span>
            {m.appears.map((a) => (
              <Link key={a.to + a.label} to={a.to} className="me-go num">
                {a.label.toUpperCase()} →
              </Link>
            ))}
          </div>
        )}
      </div>
      <div className="me-entry-side">
        {m.formulas.map((f) => (
          <figure key={f.tex} className="me-formula" tabIndex={0} aria-label={f.caption ? `Formula: ${f.caption}` : "Formula"}>
            <Formula tex={f.tex} />
            {f.caption && <figcaption>{f.caption}</figcaption>}
          </figure>
        ))}
        {m.refs.length > 0 && (
          <div className="me-refs">
            <div className="me-label">REFERENCES</div>
            <ul>
              {m.refs.map((k) => (
                <li key={k}>
                  <a href={`#ref-${k}`} title={shortCite(k)}>
                    {citation(k)}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </article>
  );
}

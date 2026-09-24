/**
 * Methodology (/methodology) — a searchable reference of every model in the platform, the
 * data sources behind them, the platform's limitations and a consolidated bibliography.
 * Content is structured data in ./methodology/{models,references,sources}.ts; this file
 * only lays it out. No API calls.
 */
import { useEffect, useMemo } from "react";
import { Link, useLocation } from "react-router-dom";
import { Formula, Page, useTabParam } from "../components";
import { Icon } from "../components/Icon";
import { MODELS, SECTIONS, type Model } from "./methodology/models";
import { citation, REFS, shortCite } from "./methodology/references";
import { LIMITATIONS, SOURCES } from "./methodology/sources";
import "./methodology/methodology.css";

function haystack(m: Model): string {
  return [m.name, m.summary, m.keywords ?? "", m.assumptions.join(" "), m.refs.map(citation).join(" "), SECTIONS.find((s) => s.id === m.section)?.title ?? ""].join(" ").toLowerCase();
}

export default function Methodology() {
  const [q, setQ] = useTabParam<string>("q", "");
  const loc = useLocation();
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const hay = useMemo(() => new Map(MODELS.map((m) => [m.id, haystack(m)])), []);
  const shown = MODELS.filter((m) => terms.every((t) => hay.get(m.id)!.includes(t)));
  const cited = useMemo(() => {
    const keys = new Set(MODELS.flatMap((m) => m.refs));
    return Object.keys(REFS)
      .filter((k) => keys.has(k))
      .sort((a, b) => REFS[a].authors.localeCompare(REFS[b].authors) || REFS[a].year.localeCompare(REFS[b].year));
  }, []);

  // Deep links (#model-id, #data-sources …): scroll once the lazily-loaded page has rendered.
  useEffect(() => {
    if (!loc.hash) return;
    const t = window.setTimeout(() => document.getElementById(decodeURIComponent(loc.hash.slice(1)))?.scrollIntoView({ block: "start" }), 60);
    return () => window.clearTimeout(t);
  }, [loc.hash]);

  return (
    <Page
      eyebrow="Reference"
      title="Methodology"
      subtitle="Every model in OhCamel Quant: what it does in plain English, the formula, what it assumes, where it appears, and the paper it comes from."
      meta={
        <span className="subtle small">
          <span className="num">{MODELS.length}</span> models · <span className="num">{cited.length}</span> references · <span className="num">{SOURCES.length}</span> data sources
        </span>
      }
    >
      <div className="me-layout">
        <nav className="me-toc" aria-label="Contents">
          <div className="me-toc-label">Models</div>
          {SECTIONS.map((s) => {
            const n = shown.filter((m) => m.section === s.id).length;
            return (
              <a key={s.id} href={`#sec-${s.id}`} className={n ? "" : "dim"}>
                <span>{s.title}</span>
                <span className="num">{n}</span>
              </a>
            );
          })}
          <div className="me-toc-label">Reference</div>
          <a href="#data-sources">Data sources</a>
          <a href="#limitations">Limitations</a>
          <a href="#bibliography">Bibliography</a>
        </nav>

        <div className="me-main">
          <div className="me-search">
            <Icon name="search" size={16} />
            <input type="search" placeholder="Search models, authors, pages… e.g. “sharpe”, “Ledoit”, “VaR backtest”" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search the methodology" />
            <span className="subtle small num">{terms.length ? `${shown.length} of ${MODELS.length}` : ""}</span>
          </div>
          {terms.length > 0 && shown.length === 0 && (
            <div className="me-empty">
              Nothing matches “{q}”. Try an author (“Fama”), a measure (“drawdown”) or a page (“Optimizer”).{" "}
              <button type="button" className="btn btn-sm" onClick={() => setQ("")}>
                Clear search
              </button>
            </div>
          )}

          {SECTIONS.map((s) => {
            const ms = shown.filter((m) => m.section === s.id);
            if (!ms.length) return null;
            return (
              <section key={s.id} id={`sec-${s.id}`} className="me-section">
                <header className="me-section-head">
                  <h2 className="display">{s.title}</h2>
                  <p>{s.blurb}</p>
                </header>
                {ms.map((m) => (
                  <ModelCard key={m.id} m={m} />
                ))}
              </section>
            );
          })}

          <section id="data-sources" className="me-section">
            <header className="me-section-head">
              <h2 className="display">Data sources</h2>
              <p>Every number on every page is traced to one of these providers; the panel footer names which one served it and when. All are public or free-tier sources.</p>
            </header>
            <div className="me-sources">
              {SOURCES.map((s) => (
                <article key={s.name} className="me-source">
                  <header>
                    <a href={s.href} target="_blank" rel="noreferrer" className="me-source-name">
                      {s.name} <Icon name="external" size={12} />
                    </a>
                    {s.offline && <span className="badge accent" title="Part of the committed offline dataset">offline subset</span>}
                  </header>
                  <p>{s.supplies}</p>
                  <dl>
                    <dt>Refresh</dt>
                    <dd>{s.cadence}</dd>
                    <dt>Caveats</dt>
                    <dd>{s.notes}</dd>
                  </dl>
                </article>
              ))}
            </div>
          </section>

          <section id="limitations" className="me-section">
            <header className="me-section-head">
              <h2 className="display">Limitations</h2>
              <p>What this platform cannot do, stated plainly.</p>
            </header>
            <ol className="me-limits">
              {LIMITATIONS.map((l) => (
                <li key={l.title}>
                  <strong>{l.title}.</strong> {l.text}
                </li>
              ))}
            </ol>
          </section>

          <section id="bibliography" className="me-section">
            <header className="me-section-head">
              <h2 className="display">Bibliography</h2>
              <p>Every work cited above, alphabetically. Each entry links back to the models that use it.</p>
            </header>
            <ol className="me-bib">
              {cited.map((k) => {
                const r = REFS[k];
                const users = MODELS.filter((m) => m.refs.includes(k));
                return (
                  <li key={k} id={`ref-${k}`}>
                    <span className="me-bib-authors">{r.authors}</span> ({r.year}). {r.href ? <a href={r.href} target="_blank" rel="noreferrer"><em>{r.title.replace(/\.$/, "")}</em></a> : <em>{r.title.replace(/\.$/, "")}</em>}. <span className="subtle">{r.venue.replace(/\.$/, "")}.</span>
                    <span className="me-bib-used">
                      {users.map((m) => (
                        <a key={m.id} href={`#${m.id}`}>
                          {m.name}
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

function ModelCard({ m }: { m: Model }) {
  return (
    <article id={m.id} className="me-card">
      <div className="me-card-text">
        <h3>
          <a href={`#${m.id}`} className="me-anchor" aria-label={`Link to ${m.name}`}>
            #
          </a>
          {m.name}
        </h3>
        <p className="me-summary">{m.summary}</p>
        {m.assumptions.length > 0 && (
          <>
            <div className="me-label">Assumptions & caveats</div>
            <ul className="me-assume">
              {m.assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </>
        )}
        {m.appears.length > 0 && (
          <div className="me-appears">
            <span className="me-label">Where it appears</span>
            {m.appears.map((a) => (
              <Link key={a.to + a.label} to={a.to} className="me-chip">
                {a.label} <Icon name="arrow-right" size={12} />
              </Link>
            ))}
          </div>
        )}
      </div>
      <div className="me-card-side">
        {m.formulas.map((f) => (
          <figure key={f.tex} className="me-formula">
            <Formula tex={f.tex} />
            {f.caption && <figcaption>{f.caption}</figcaption>}
          </figure>
        ))}
        {m.refs.length > 0 && (
          <div className="me-refs">
            <div className="me-label">References</div>
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

/**
 * The left-hand panel (spec sections 6.4 and 6.5): what the selection is, its verdict and provenance, a lens for its
 * kind, and what it reads and feeds. Every figure comes from the response through `fmt`; a figure the response lacks is
 * left out, never shown as a dash, and a lens with nothing to show is not drawn.
 */
import { useEffect, useMemo, useRef, type ReactNode, type RefObject } from "react";
import { instant, num, nyDay, nyTime, pct, plural } from "./fmt";
import { cellRef, estimator, fin, jobRuns, known, obj, reason, rows, schedule, str } from "./prose";
import type { Seg } from "./prose";
import { Segs } from "./Segs";
import type { MNode, NodeKind, Run } from "./types";
import { countKinds, nearestFirst, summaryLine, webOf } from "./web";
import type { GraphIndex, WebMode } from "./web";

const KIND: Record<NodeKind, string> = {
  series: "SERIES", job: "JOB", product: "PRODUCT", result: "RESULT", strategy: "STRATEGY", feed: "FEED", limit: "LIMIT", book: "THE BOOK", kill: "KILL SWITCH",
};
/** The chips a side of the web shows, closest first. */
export const MAX_CHIPS = 48;
/** The rows a lens's table shows before "+N more". */
const MAX_ROWS = 8;

export interface FocusPanelProps {
  gi: GraphIndex;
  i: number;
  mode: WebMode;
  runs: Run[];
  now: Date;
  /** The name's heading, where focus goes after a selection made inside a panel. */
  heading: RefObject<HTMLHeadingElement>;
  onSelect(id: string): void;
  onClose(): void;
}

export function FocusPanel({ gi, i, mode, runs, now, heading, onSelect, onClose }: FocusPanelProps) {
  const n = gi.out.nodes[i];
  const web = useMemo(() => webOf(gi, i, mode), [gi, i, mode]);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (panel.current) panel.current.scrollTop = 0; // a new selection starts at its own top
  }, [n.id]);
  const verdict = str(n.m.verdict), detail = str(n.m.detail);
  const sub = n.k === "series" ? n.n : n.k === "job" || n.k === "product" ? str(n.m.kind) : null;
  const lens = n.status === "missing" ? null : <Lens gi={gi} i={i} n={n} runs={runs} now={now} onSelect={onSelect} />;
  return (
    <aside ref={panel} className="mx-panel mx-focus" aria-label="The selection">
      <div className="mx-idl">
        <span className="mx-idl-id">
          <b>{KIND[n.k] ?? n.k}</b> · {n.id}
        </span>
        <button type="button" className="mx-x" aria-label="Deselect" onClick={onClose}>
          ×
        </button>
      </div>
      <h2 ref={heading} className="mx-name" tabIndex={-1}>
        {n.k === "series" ? n.l : n.n}
      </h2>
      {sub && sub !== (n.k === "series" ? n.l : n.n) && <div className="mx-vdetail">{sub}</div>}
      {(verdict || n.status === "missing" || n.m.stale === true) && (
        <div className="mx-stamps">
          {verdict && <span className={`mx-verdict ${/FAIL/.test(verdict) ? "fail" : /PASS/.test(verdict) ? "pass" : "desc"}`}>{verdict}</span>}
          {n.status === "missing" && <span className="mx-verdict desc">MISSING</span>}
          {n.m.stale === true && <span className="mx-verdict desc">STALE</span>}
          <span className="mx-vdetail">{n.status === "missing" ? reason(gi, str(n.why) ?? "") : detail}</span>
        </div>
      )}
      {n.k === "product" && <Provenance m={n.m} />}
      {lens}
      <Side gi={gi} tone="up" word="reads" web={web.up} onSelect={onSelect} />
      <Side gi={gi} tone="down" word="feeds" web={web.down} onSelect={onSelect} />
    </aside>
  );
}

function Side({ gi, tone, word, web, onSelect }: { gi: GraphIndex; tone: string; word: string; web: Map<number, number>; onSelect(id: string): void }) {
  if (web.size === 0) return null;
  const { shown, more } = nearestFirst(gi, web, MAX_CHIPS);
  return (
    <section className="mx-sec">
      <h3 className="mx-h3">
        <i className={`mx-sw ${tone}`} aria-hidden="true" />
        {word} <b>{summaryLine(countKinds(gi, web.keys()))}</b>
      </h3>
      <div className="mx-chips">
        {shown.map((j) => (
          <Chip key={j} n={gi.out.nodes[j]} onSelect={onSelect} />
        ))}
        {more > 0 && <span className="mx-chip more">+{num(more)} more</span>}
      </div>
    </section>
  );
}

function Chip({ n, note, onSelect }: { n: MNode; note?: string; onSelect(id: string): void }) {
  return (
    <button type="button" className={`mx-chip${/FAIL/.test(str(n.m.verdict) ?? "") ? " fail" : ""}`} title={n.n} onClick={() => onSelect(n.id)}>
      {n.l}
      {note && <sub>{note}</sub>}
    </button>
  );
}

function Provenance({ m }: { m: Record<string, unknown> }) {
  const code = str(m.code), engine = str(m.engine), cpu = fin(m.cpu), asof = nyDay(str(m.asof)), finished = str(m.finished);
  const parts: ReactNode[] = [
    code && <>code <b title={code}>{code.slice(0, 7)}</b></>,
    known(asof) && <>data <b>{asof}</b></>,
    cpu !== null && <><b>{num(cpu)}</b> cpu-s</>,
    engine && <b>{engine}</b>,
    finished && known(nyDay(finished)) && <>finished <b>{nyTime(finished)}</b> {nyDay(finished)}</>,
  ].filter(Boolean);
  if (parts.length === 0) return null;
  return (
    <div className="mx-prov">
      {parts.map((part, k) => (
        <span key={k}>
          {k > 0 && " · "}
          {part}
        </span>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ lenses
interface LensProps {
  gi: GraphIndex;
  i: number;
  n: MNode;
  runs: Run[];
  now: Date;
  onSelect(id: string): void;
}

function Lens(p: LensProps) {
  const { n } = p;
  if (n.k === "series") return n.m.track === "daily" ? <TickerLens {...p} /> : null;
  if (n.k === "feed") return <FeedLens {...p} />;
  if (n.k === "job") return <JobLens {...p} />;
  if (n.k !== "product") return null;
  switch (n.m.kind) {
    case "farm.sweep": return <FarmLens {...p} />;
    case "vol.forecast_league": return <VolLeagueLens {...p} />;
    case "cov.league": return <CovLeagueLens {...p} />;
    case "risk.mc_atlas": return <AtlasLens {...p} />;
    case "models.xs_lgbm": return <XsLens {...p} />;
    default: return null;
  }
}

type Stat = [figure: string, word: string];

function Stats({ stats }: { stats: (Stat | false | null)[] }) {
  const kept = stats.filter((s): s is Stat => Boolean(s));
  if (kept.length === 0) return null;
  return (
    <div className="mx-stats">
      {kept.map(([figure, word]) => (
        <div key={word}>
          <b>{figure}</b>
          {word}
        </div>
      ))}
    </div>
  );
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const lo = Math.min(...values), hi = Math.max(...values), w = 340, h = 50;
  const at = (x: number): number => Math.round(x * 10) / 10;
  const points = values.map((v, k) => `${at((k / (values.length - 1)) * w)},${at(h - 3 - ((v - lo) / (hi - lo || 1)) * (h - 6))}`).join(" ");
  return (
    <svg className="mx-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <polyline points={points} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

const TRACKS: [string, string][] = [["m:", "minute bars"], ["o:", "option chain"], ["s:", "SEC filings"]];

function TickerLens({ gi, n, onSelect }: LensProps) {
  const m = n.m;
  const closes = fin(m.n), last = fin(m.last), ret = fin(m.ret1y), vol = fin(m.vol);
  const spark = (Array.isArray(m.spark) ? m.spark : []).map(fin).filter((v): v is number => v !== null);
  const tracks = TRACKS.filter(([p]) => gi.byId.has(p + n.l));
  const peers = rows(m.peers)
    .map(([t, c]) => ({ node: gi.out.nodes[gi.byId.get(`d:${str(t)}`) ?? -1], c: fin(c) }))
    .filter((p) => p.node && p.c !== null);
  return (
    <>
      {(spark.length > 1 || last !== null || ret !== null || vol !== null) && (
        <section className="mx-sec">
          <Sparkline values={spark} />
          <Stats
            stats={[
              last !== null && [`$${num(last, 2)}`, "last close"],
              ret !== null && closes !== null && [pct(ret, 1, true), closes === 253 ? "one year" : `${num(closes)} sessions`],
              vol !== null && [pct(vol, 1), "volatility"],
            ]}
          />
        </section>
      )}
      <section className="mx-sec">
        <h3 className="mx-h3">on {plural(tracks.length + 1, "track")}</h3>
        <div className="mx-chips">
          <span className="mx-chip plain">daily bars</span>
          {tracks.map(([p, word]) => (
            <button key={p} type="button" className="mx-chip" onClick={() => onSelect(p + n.l)}>
              {word}
            </button>
          ))}
        </div>
      </section>
      {peers.length > 0 && (
        <section className="mx-sec">
          <h3 className="mx-h3">moves most with</h3>
          <div className="mx-chips">
            {peers.map((p) => (
              <Chip key={p.node.id} n={p.node} note={num(p.c, 2)} onSelect={onSelect} />
            ))}
          </div>
        </section>
      )}
    </>
  );
}

function FeedLens({ n }: LensProps) {
  const m = n.m;
  const keys = fin(m.keys), behind = fin(m.behind), asof = nyDay(str(m.asof)), ok = str(m.ok);
  return (
    <section className="mx-sec">
      <Stats
        stats={[
          keys !== null && [num(keys), "series"],
          known(asof) && [asof, "data as of"],
          ok !== null && known(nyTime(ok)) && [nyTime(ok), `${nyDay(ok)}, last pull`],
          behind !== null && [num(behind), "series behind"],
        ]}
      />
    </section>
  );
}

function JobLens({ gi, n, runs, now, onSelect }: LensProps) {
  const m = n.m;
  const when = schedule(m); // the readings' own words, so a wire word such as "weekdays" is never printed as it comes
  const timed = when.some((s) => "b" in s);
  const notes = [m.heavy === true && "heavy: never during the session", str(m.mem) && `memory class ${str(m.mem)}`].filter(Boolean);
  const { ran } = jobRuns(runs, str(m.kind) ?? n.id.replace(/^j:/, ""), now);
  const dated = (r: Run): number => instant(r.start ?? r.end) ?? 0;
  const recent = [...ran].sort((a, b) => dated(b) - dated(a)).slice(0, MAX_ROWS);
  return (
    <>
      {(when.length > 0 || notes.length > 0) && (
        <section className="mx-sec">
          <h3 className="mx-h3">scheduled</h3>
          <div className="mx-vdetail">
            <Segs segs={when} has={(id) => gi.byId.has(id)} onSelect={onSelect} />
            {timed && " ET"}
            {when.length > 0 && notes.length > 0 && " · "}
            {notes.join(" · ")}
          </div>
        </section>
      )}
      <section className="mx-sec">
        <h3 className="mx-h3">
          in the last day <b>{plural(ran.length, "run")}</b>
        </h3>
        {recent.length > 0 && (
          <table className="mx-kv">
            <tbody>
              {recent.map((r) => (
                <tr key={r.id}>
                  <td>{[r.start && nyTime(r.start), r.end && nyTime(r.end)].filter(Boolean).join(" to ")}{r.state === "done" ? "" : ` · ${r.state}`}</td>
                  <td className="n">{fin(r.cpu) === null ? "" : `${num(r.cpu, 1)} cpu-s`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}

function Tok({ gi, seg, onSelect }: { gi: GraphIndex; seg: Seg; onSelect(id: string): void }) {
  return <Segs segs={[seg]} has={(id) => gi.byId.has(id)} onSelect={onSelect} />;
}

function Table({ title, head, body, more }: { title: string; head: string[]; body: ReactNode[][]; more?: number }) {
  return (
    <section className="mx-sec">
      <h3 className="mx-h3">{title}</h3>
      <table className="mx-kv">
        <thead>
          <tr>
            {head.map((h, k) => (
              <th key={k} scope="col" className={k > 0 ? "n" : undefined}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, r) => (
            <tr key={r}>
              {row.map((cell, k) => (
                <td key={k} className={k > 0 ? "n" : undefined}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {more !== undefined && more > 0 && <div className="mx-vdetail mx-more">+{num(more)} more</div>}
    </section>
  );
}

function FarmLens({ gi, n, onSelect }: LensProps) {
  const board = rows(obj(n.m.lens).board).filter((r) => str(r[0]));
  if (board.length === 0) return null;
  return (
    <Table
      title="leaderboard, chosen variant per cell"
      head={["cell", "Sharpe", "DSR", "verdict"]}
      body={board.slice(0, MAX_ROWS).map(([cell, verdict, sharpe, dsr]) => [
        <Tok key="c" gi={gi} seg={cellRef(gi, cell as string)} onSelect={onSelect} />,
        fin(sharpe) === null ? "" : num(sharpe as number, 2),
        fin(dsr) === null ? "" : num(dsr as number, 2),
        <span key="v" className={/FAIL/.test(str(verdict) ?? "") ? "mx-f" : undefined}>{str(verdict)}</span>,
      ])}
      more={board.length - MAX_ROWS}
    />
  );
}

function VolLeagueLens({ gi, n, onSelect }: LensProps) {
  const models = rows(obj(n.m.lens).models).filter((r) => str(r[0]));
  if (models.length === 0) return null;
  return (
    <Table
      title="models, out of sample"
      head={["model", "best set", "mean rank"]}
      body={models.map(([model, rate, rank]) => [
        <Tok key="m" gi={gi} seg={{ ref: `lm:${model}`, t: String(model).toUpperCase() }} onSelect={onSelect} />,
        fin(rate) === null ? "" : pct(rate as number, 0),
        fin(rank) === null ? "" : num(rank as number, 2),
      ])}
    />
  );
}

function CovLeagueLens({ gi, n, onSelect }: LensProps) {
  const all = rows(obj(n.m.lens).estimators).filter((r) => str(r[0]) && str(r[1]));
  if (all.length === 0) return null;
  const universes = [...new Set(all.map((r) => r[1] as string))];
  const estimators = [...new Set(all.map((r) => r[0] as string))];
  const cell = (e: string, u: string): ReactNode => {
    const row = all.find((r) => r[0] === e && r[1] === u);
    const vol = row && fin(row[2]);
    if (vol === undefined || vol === null) return "";
    return (
      <>
        {pct(vol, 1)} <span className="mx-dim">#{num(row![3] as number)}</span>
      </>
    );
  };
  return (
    <Table
      title="realized vol of each estimator's minimum-variance book"
      head={["estimator", ...universes.map((u) => u.replace(/_/g, " "))]}
      body={estimators.map((e) => [<Tok key="e" gi={gi} seg={estimator(gi, e)} onSelect={onSelect} />, ...universes.map((u) => cell(e, u))])}
    />
  );
}

const METHOD: Record<string, string> = { fhs: "FHS", copula_t: "t-copula" };

function AtlasLens({ gi, n, onSelect }: LensProps) {
  const measures = rows(obj(n.m.lens).measures).filter((r) => str(r[0]) && fin(r[1]) !== null);
  if (measures.length === 0) return null;
  return (
    <Table
      title="measures"
      head={["method", "horizon", "VaR", "ES"]}
      body={measures.map(([method, horizon, v, es]) => {
        const id = `am:${method}:${horizon}`;
        const word = METHOD[method as string] ?? String(method).replace(/_/g, " ");
        return [
          <Tok key="m" gi={gi} seg={gi.byId.has(id) ? { ref: id, t: word } : { t: word }} onSelect={onSelect} />,
          `${num(horizon as number)} d`,
          fin(v) === null ? "" : pct(v as number, 2),
          fin(es) === null ? "" : pct(es as number, 2),
        ];
      })}
    />
  );
}

function XsLens({ n }: LensProps) {
  const deciles = rows(obj(n.m.lens).deciles)
    .map(([d, r]) => [fin(d), fin(r)] as const)
    .filter((x): x is readonly [number, number] => x[0] !== null && x[1] !== null)
    .sort((a, b) => a[0] - b[0]);
  if (deciles.length === 0) return null;
  const w = 340, h = 54, mid = h / 2, reach = mid - 3;
  const top = Math.max(...deciles.map(([, r]) => Math.abs(r))) || 1;
  const slot = w / deciles.length;
  return (
    <section className="mx-sec">
      <h3 className="mx-h3">next-month return by score decile</h3>
      <svg className="mx-bars" viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`Mean next-month return of ${plural(deciles.length, "decile")}`}>
        {deciles.map(([d, r], k) => {
          const tall = (Math.abs(r) / top) * reach;
          return (
            <rect key={d} className={r < 0 ? "neg" : undefined} x={k * slot + slot * 0.12} y={r >= 0 ? mid - tall : mid} width={slot * 0.76} height={tall}>
              <title>{`Decile ${num(d)}: ${pct(r, 2, true)}`}</title>
            </rect>
          );
        })}
        <line x1={0} x2={w} y1={mid} y2={mid} />
      </svg>
    </section>
  );
}

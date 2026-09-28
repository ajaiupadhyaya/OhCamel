/**
 * Flight Deck (/deck) — one book as three film-style cockpit instruments, read from real data
 * and refreshed live (design: docs/superpowers/specs/2026-09-24-quant-flight-deck-design.md).
 *
 *   A. Targeting computer — how close is the book to each of its limits?   (deck/scope.ts)
 *   B. Radar              — where is the risk, and what is moving today?  (deck/radar.ts)
 *   C. Lamp panel         — is the data behind this alive?               (deck/LampPanel.tsx)
 *
 * Sources (?source=, a SegmentedControl): your portfolio (default) and the reference book both
 * POST /deck/reading, polled every 15 s while the US session is open and every 5 min while it
 * is closed; the OCaml engine is GET /engine/snapshot through the read-only bridge, every 2 s
 * (its 503 is rendered, never replaced). All three become one DeckModel (deck/model.ts).
 * Below the instruments: the session tape, the marks with provenance, and the limits editor.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { DataTable, Panel, Section, SegmentedControl, useTabParam, type Column } from "../components";
import { DataUnavailableError, type ApiError } from "../lib/api";
import { fmtDate, fmtNum, fmtPct, fmtRelativeTime, fmtSignedPct } from "../lib/format";
import { usePortfolio } from "../lib/portfolio";
import { useApiPost, useApiQuery } from "../lib/query";
import type { HistoryOut, SnapshotOut } from "./engine/types";
import { Counters, LampPanel } from "./deck/LampPanel";
import { LimitsEditor } from "./deck/LimitsEditor";
import { DEFAULT_LIMITS, loadLimits, sanitizeLimits, saveLimits, toWire, type LimitIn } from "./deck/limits";
import { appendTrail, fromEngine, fromReading, historyToTape, tapeFromServer, trailToTape, type BooksOut, type DeckModel, type DeckSource, type Reading, type ReadingMark, type TapeOut, type TrailPoint } from "./deck/model";
import { Radar } from "./deck/RadarScope";
import { SessionTape } from "./deck/SessionTape";
import { TargetingComputer } from "./deck/TargetingComputer";
import "./deck/deck.css";

const ALPHA = 0.95;
const EWMA_LAMBDA = 0.94;
const OPEN_POLL = 15_000;
const CLOSED_POLL = 300_000;
const ENGINE_POLL = 2_000;
const REFERENCE_KEY = "core";

const SOURCES: { value: DeckSource; label: string; title: string }[] = [
  { value: "portfolio", label: "Your portfolio", title: "The portfolio you are building across the app" },
  { value: "reference", label: "Reference book", title: "The server's reference book, recorded all session by the flight recorder" },
  { value: "engine", label: "OCaml engine", title: "The OCaml real-time engine's book, through the read-only bridge" },
];
const isSource = (s: string): s is DeckSource => SOURCES.some((x) => x.value === s);

/** What every Panel on the deck is handed: a query-shaped view of the current DeckModel. */
interface DeckQuery {
  data: DeckModel | undefined;
  error: unknown;
  isLoading: boolean;
  isFetching: boolean;
  isError: boolean;
  refetch: () => Promise<any>;
  /** The last poll failed but an earlier reading is on screen. */
  stale: boolean;
  staleError: ApiError | null;
  cadence: string;
}

// The portfolio source's own tape: readings accumulated while the page is open. Kept per book
// in module memory, so it survives moving around the app but not a reload (said on the panel).
const TRAILS = new Map<string, TrailPoint[]>();

// ------------------------------------------------------------------ limits (localStorage)

function useDeckLimits() {
  const [custom, setCustom] = useState<LimitIn[] | null>(() => loadLimits());
  const set = (ls: LimitIn[]) => {
    setCustom(ls);
    saveLimits(ls);
  };
  const reset = () => {
    setCustom(null);
    saveLimits(null);
  };
  return { custom, set, reset };
}

// ------------------------------------------------------------------ page

export default function Deck() {
  const [params, setParams] = useSearchParams();
  const focus = params.get("focus") === "1";
  const [raw, setSource] = useTabParam<string>("source", "portfolio");
  const source: DeckSource = isSource(raw) ? raw : "portfolio";
  return (
    <div className="dk-flight">
      <header className="dk-flight-header">
        <h1><span>OhCamel /</span> FLIGHT DECK</h1>
        <SegmentedControl<DeckSource> className="dk-source" ariaLabel="What the deck is flying" options={SOURCES} value={source} onChange={(v) => setSource(v)} />
        <button className="dk-button" onClick={() => {const next = new URLSearchParams(params); if (focus) next.delete("focus"); else next.set("focus","1"); setParams(next);}}>{focus ? "Exit focus" : "Focus view"}</button>
      </header>
      <DeckBody key={source} source={source} />
    </div>
  );
}

function DeckBody({ source }: { source: DeckSource }) {
  const { request, portfolio } = usePortfolio();
  const limits = useDeckLimits();
  const drawer = useRef<HTMLDialogElement>(null);
  const [inspection, setInspection] = useState("Reading details");
  const inspect = (text: string) => {
    setInspection(text);
    drawer.current?.showModal();
  };
  const wire = toWire(limits.custom);

  // Reference books (and the server's default limits) — not needed for the engine.
  const books = useApiQuery<BooksOut>("/deck/books", undefined, { enabled: source !== "engine", staleTime: 60_000 });
  const core = books.data?.books?.find((b) => b.key === REFERENCE_KEY) ?? null;
  const serverDefaults = sanitizeLimits(core?.limits ?? books.data?.default_limits);
  const defaults = serverDefaults?.length ? serverDefaults : DEFAULT_LIMITS;

  const body = useMemo(() => {
    if (source === "portfolio") return { ...request, limits: wire, alpha: ALPHA, ewma_lambda: EWMA_LAMBDA };
    if (source === "reference" && core) {
      const holdings = core.holdings ?? [];
      return { holdings, start: core.start ?? null, end: core.end ?? null, benchmark: core.benchmark ?? "SPY", notional: core.notional ?? 1_000_000, limits: wire, alpha: ALPHA, ewma_lambda: EWMA_LAMBDA };
    }
    return null;
  }, [source, request, core, wire && JSON.stringify(wire)]); // eslint-disable-line react-hooks/exhaustive-deps

  const reading = useApiPost<Reading>("/deck/reading", body, {
    enabled: source !== "engine" && body != null,
    placeholderData: undefined,
    staleTime: 0,
    refetchInterval: (q) => (q.state.data?.clock?.is_open ? OPEN_POLL : CLOSED_POLL),
  });
  const snap = useApiQuery<SnapshotOut>("/engine/snapshot", undefined, {
    enabled: source === "engine",
    staleTime: 0,
    // a bridge that is off answers 503 on every read; ask again every 30 s, not every 2
    refetchInterval: (q) => (q.state.status === "error" && q.state.data == null ? 30_000 : ENGINE_POLL),
  });

  const model = useMemo<DeckModel | undefined>(() => {
    if (source === "engine") return snap.data ? fromEngine(snap.data) : undefined;
    return reading.data ? fromReading(reading.data, source) : undefined;
  }, [source, snap.data, reading.data]);

  const q: DeckQuery = useMemo(() => {
    const base = source === "engine" ? snap : reading;
    const refError = source === "reference" ? (books.error ?? (books.data && !core ? new Error(`The server lists no reference book "${REFERENCE_KEY}".`) : null)) : null;
    const hasData = !!model;
    const error = hasData ? null : (refError ?? base.error);
    const loading = !hasData && !error && (source === "reference" ? books.isLoading || base.isLoading || (!!core && base.isPending) : base.isLoading);
    const open = model?.clock?.is_open;
    return {
      data: model,
      error,
      isLoading: loading,
      isFetching: base.isFetching,
      isError: !!error,
      refetch: () => (refError ? books.refetch() : base.refetch()),
      stale: hasData && base.isError,
      staleError: hasData && base.isError ? (base.error as ApiError) : null,
      cadence: source === "engine" ? "every 2 s" : open ? "every 15 s while the session is open" : "every 5 min while the session is closed",
    };
  }, [source, snap, reading, books, core, model]);

  return (
    <>
      <div className="dk-command-row">
        <StatusStrip q={q} source={source} />
        <button className="dk-button" onClick={() => inspect("Reading details")}>Details / limits</button>
      </div>
      {model?.quality && (!model.quality.complete || model.quality.offline || model.quality.fixtures) && (
        <p className="dk-notice" role="status">{(model.quality.offline || model.quality.fixtures) ? "HISTORICAL FIXTURES · NOT LIVE" : "PARTIAL READING"} · {model.quality.complete ? "Committed real market observations. These are historical observations, not current quotes." : `Missing ${model.quality.missing.join(", ")}. Risk uses held-close estimates; limits are unevaluated.`}</p>
      )}
      {q.isError ? <SourceError source={source} error={q.error} onRetry={() => void q.refetch()} /> : (
        <>
          <Instruments q={q} onInspect={inspect} />
          <div className="dk-tape-shell"><div className="dk-instrument-heading"><h2>Session scanner</h2><span>TIME / RISK / EVENTS</span></div><Tape source={source} q={q} /></div>
        </>
      )}
      <footer className="dk-provenance-strip">
        <span>READ-ONLY INSTRUMENTS</span>
        <span>{source === "engine" ? "OCaml engine" : `Quote cache ${model?.quality?.quote_cache_s ?? 60}s · 1-day EWMA risk`}</span>
        <span>History through {model?.quality?.history_as_of ?? "—"}</span>
        <button onClick={() => inspect("Sources & model assumptions")}>Sources & assumptions</button>
      </footer>
      <dialog ref={drawer} className="dk-details" aria-labelledby="dk-details-title">
        <header className="dk-details-header"><h2 id="dk-details-title">{inspection}</h2><button className="btn" onClick={() => drawer.current?.close()}>Close</button></header>
        <p className="small subtle">Quotes and risk estimates have different observation times. Policy limits are your settings. Escape closes this drawer.</p>
        {model?.quality && <p>Basis: {model.quality.basis}. Historical window ends {model.quality.history_as_of}.</p>}
        {model?.notes.map((note, i) => <p key={i} className="small">{note}</p>)}
        {model?.feeds.map(f => <p key={f.key} className="small"><strong>{f.label}: {f.state}</strong> — {f.detail}{f.age_s != null ? ` · ${Math.round(f.age_s)}s old at reading` : ""}</p>)}
        <p className="small">Corridor depth shows observed ÷ threshold. The red counter shows the worst breach when one exists, otherwise the nearest limit’s remaining headroom. Select a limit for its actual value and units.</p>
        <p className="small">Radar bearings follow alphabetical ticker order; radius is absolute share of value at risk, size is absolute portfolio weight, and hollow marks are hedges. Values beyond the rim are clamped and marked. The green phosphor is a display color; today’s signed move is in holding inspection and the marks table.</p>
        <MarksPanel q={q} />
      <Section title="Limits" description="Your policy lines. They are sent with every reading, so the targeting computer and the lamps show your limits, not ours.">
        {source === "engine" ? (
          <Panel title="Engine limits" notes={[]}>
            <p className="small subtle">The OCaml engine evaluates the limits configured in its own book file; they are read-only here. Switch to your portfolio or the reference book to edit limits.</p>
          </Panel>
        ) : (
          <Panel
            title="Limits editor"
            subtitle="Exposure and drawdown limits are a percent of equity; value at risk, expected shortfall and day loss are a percent of notional, evaluated in dollars."
            info={{ text: "A limit is breached when its observed value exceeds its threshold. Utilisation = observed ÷ threshold. A limit whose observation cannot be computed (say, no history for a holding) is shown as unevaluated, never as fine." }}
            notes={source === "reference" ? ["The recorded session tape uses the server's default limits; your edits apply to the live reading above."] : []}
          >
            <LimitsEditor
              limits={limits.custom ?? defaults}
              customised={limits.custom != null}
              onChange={limits.set}
              onReset={limits.reset}
              notional={source === "reference" ? (core?.notional ?? null) : portfolio.notional}
              tickers={(source === "reference" ? (core?.holdings ?? []) : portfolio.holdings).map((h) => h.ticker)}
            />
          </Panel>
        )}
      </Section>
      </dialog>
    </>
  );
}

// ------------------------------------------------------------------ status

function StatusStrip({ q, source }: { q: DeckQuery; source: DeckSource }) {
  const m = q.data;
  if (!m) return null;
  const clock = m.clock;
  return (
    <div className="dk-status">
      {clock ? (
        <span className={`badge ${clock.is_open ? "gain" : "unknown"}`} title={clock.note ?? undefined}>
          {clock.source === "alpaca" ? "US session" : "Estimated session"} {clock.is_open ? "open" : "closed"}
          {clock.source ? ` · ${clock.source}` : ""}
        </span>
      ) : (
        source === "engine" && <span className="badge accent">Engine book · no session clock</span>
      )}
      {m.as_of && (
        <span className="subtle small">
          Response <span className="num">{fmtRelativeTime(m.as_of.replace(" ", "T"))}</span>
        </span>
      )}
      {q.stale && (
        <span className="badge warn" title={q.staleError?.detail}>
          Last refresh failed — showing the previous reading
        </span>
      )}
      {source !== "engine" && <span className="subtle small">Quotes are cached for 60 s on the server, so marks move about once a minute.</span>}
    </div>
  );
}

function SourceError({ source, error, onRetry }: { source: DeckSource; error: unknown; onRetry: () => void }) {
  if (source === "engine") {
    const body = (error as DataUnavailableError | undefined)?.body as { configured?: boolean } | undefined;
    const off = error instanceof DataUnavailableError && body?.configured === false;
    return (
      <Panel
        title="OCaml engine"
        subtitle={
          off ? (
            <>
              The engine bridge is not enabled on this server, so there is no engine book to fly — and nothing is simulated in its place. <Link to="/engine">Live Engine</Link> explains how it is turned on.
            </>
          ) : (
            "The bridge is configured but the engine did not answer. Asking again every 30 seconds."
          )
        }
        error={error}
        onRetry={onRetry}
      />
    );
  }
  return <Panel title={source === "reference" ? "Reference book" : "Your portfolio"} subtitle="The deck could not take a reading." error={error} onRetry={onRetry} />;
}

// ------------------------------------------------------------------ instruments

function Instruments({ q, onInspect }: { q: DeckQuery; onInspect: (text: string) => void }) {
  const m = q.data;
  const unevaluated = (m?.unevaluated ?? []).map(u => u.name);
  const stale = q.stale || !!m?.feeds.some(f => f.state === "stale" || f.state === "down");
  // Vendor observation times identify a new market reading, not the response timestamp.
  const quoteKey = m?.marks.map(x => `${x.ticker}:${x.as_of}:${x.price}`).join("|") ?? null;
  return (
    <div className="dk-deck" aria-busy={q.isLoading}>
      <section className="dk-instrument dk-inst-scope">
        <div className="dk-instrument-heading"><h2>Limit corridor</h2><span>{m?.limits.some(l => l.breached) ? "BREACH" : "PORTFOLIO RISK VIEW"}</span></div>
        {!m && <p className="dk-no-reading">{q.isLoading ? "ACQUIRING READING" : "NO READING"}</p>}
        <TargetingComputer limits={m?.limits ?? []} unevaluated={unevaluated} stale={stale} onInspect={onInspect} />
        {m && <Counters model={m} />}
      </section>
      <section className="dk-instrument dk-inst-radar">
        <div className="dk-instrument-heading"><h2>Risk radar</h2><span>1D VaR</span></div>
        <Radar blips={m?.blips ?? []} readingKey={quoteKey} onInspect={onInspect} />
      </section>
      <section className="dk-instrument dk-inst-system">
        <div className="dk-instrument-heading"><h2>Systems</h2><span>STATUS</span></div>
        {m ? <LampPanel model={m} onInspect={onInspect} stale={q.stale} /> : <p className="dk-no-reading">NO READING</p>}
      </section>
    </div>
  );
}

// ------------------------------------------------------------------ session tape

function Tape({ source, q }: { source: DeckSource; q: DeckQuery }) {
  if (source === "reference") return <ReferenceTape open={!!q.data?.clock?.is_open} />;
  if (source === "engine") return <EngineTape />;
  return <TrailTape model={q.data} />;
}

function ReferenceTape({ open }: { open: boolean }) {
  const tape = useApiQuery<TapeOut>("/deck/tape", { book: REFERENCE_KEY }, { staleTime: 0, refetchInterval: open ? 60_000 : false });
  const books = useApiQuery<BooksOut>("/deck/books", undefined, { staleTime: 60_000 });
  const rec = books.data?.recorder;
  const columns = useMemo(() => (tape.data ? tapeFromServer(tape.data) : null), [tape.data]);
  return (
    <Panel<TapeOut>
      title="Recorded session"
      subtitle={
        <>
          The flight recorder writes a reading of the reference book every {rec?.interval_s ?? 60} s while the session is open, so this tape is the whole day and survives reloads.
          {tape.data?.session ? <> Session <span className="num">{fmtDate(tape.data.session)}</span>.</> : null}
          {rec ? <> Recorder {rec.enabled ? (rec.running ? "running" : "enabled, not running") : "off"}{rec.last_write ? <>, last write <span className="num">{fmtRelativeTime(rec.last_write)}</span></> : null}{rec.last_error ? <>; last error: {rec.last_error}</> : null}.</> : null}
        </>
      }
      query={tape}
      skeletonHeight={260}
    >
      {() => columns && <SessionTape tape={columns} emptyText="The recorder has not written a reading for this book yet — it records while the US session is open." />}
    </Panel>
  );
}

function EngineTape() {
  const hist = useApiQuery<HistoryOut>("/engine/history", undefined, { staleTime: 0, refetchInterval: (q) => (q.state.status === "error" && q.state.data == null ? 30_000 : 5_000) });
  const columns = useMemo(() => (hist.data ? historyToTape(hist.data.history) : null), [hist.data]);
  return (
    <Panel<HistoryOut> title="Engine trail" subtitle="What the engine has seen since it last started, kept in its memory. It records equity and VaR/ES, not per-limit utilisation." query={hist} skeletonHeight={260}>
      {() => columns && <SessionTape tape={columns} emptyText="The engine restarted recently and has not appended a point yet." />}
    </Panel>
  );
}

function TrailTape({ model }: { model: DeckModel | undefined }) {
  const { request } = usePortfolio();
  const key = useMemo(() => JSON.stringify([request.holdings, request.notional, request.start, request.end, model?.clock?.session, model?.limits.map(l => [l.name, l.threshold])]), [request, model?.clock?.session, model?.limits]);
  const [trail, setTrail] = useState<TrailPoint[]>(() => TRAILS.get(key) ?? []);
  useEffect(() => setTrail(TRAILS.get(key) ?? []), [key]);
  useEffect(() => {
    if (!model) return;
    setTrail((t) => {
      const next = appendTrail(t, model);
      if (next !== t) {
        TRAILS.set(key, next);
        if (TRAILS.size > 12) TRAILS.delete(TRAILS.keys().next().value!);
      }
      return next;
    });
  }, [model, key]);
  const columns = useMemo(() => trailToTape(trail), [trail]);
  return (
    <Panel
      title="This visit's trail"
      subtitle={`${trail.length} reading${trail.length === 1 ? "" : "s"} collected by this page while it has been open.`}
      notes={["Your portfolio is not recorded on the server: this tape is kept in the page's memory and is lost on reload. The reference book's tape is recorded all session."]}
    >
      <SessionTape tape={columns} emptyText="The first reading will start the tape." />
    </Panel>
  );
}

// ------------------------------------------------------------------ marks

function ageText(s: number | null): string {
  if (s == null) return "—";
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${fmtNum(s / 3600, 1)} h`;
  return `${Math.round(s / 86400)} d`;
}

function MarksPanel({ q }: { q: DeckQuery }) {
  const cols: Column<ReadingMark>[] = [
    { key: "ticker", label: "Ticker", render: (r) => <strong className="num">{r.ticker}</strong> },
    { key: "price", label: "Price", numeric: true, format: (v) => fmtNum(v, 2) },
    { key: "change_pct", label: "Move", numeric: true, format: (v) => fmtSignedPct(v, 2), color: "sign", info: { text: "Change since the previous close." } },
    {
      key: "live_weight",
      label: "Weight → live",
      numeric: true,
      render: (r) => (
        <span className="num">
          {fmtPct(r.weight, 1)} → {fmtPct(r.live_weight, 1)}
        </span>
      ),
      info: { text: "The holding's weight at the last close, and after today's moves.", formula: "w_i' = \\frac{w_i (1 + r_i)}{1 + \\sum_j w_j r_j}" },
    },
    { key: "age_s", label: "Age", numeric: true, format: (v) => ageText(v), hideBelow: 600 },
    {
      key: "source",
      label: "Source",
      hideBelow: 600,
      wrap: true,
      render: (r) => (r.error ? <span className="badge unknown" title={r.error}>{r.error}</span> : <span className="small">{r.source ?? "—"}</span>),
    },
  ];
  return (
    <Panel<DeckModel> title="Marks" subtitle="The prices the instruments are reading, one row per holding." query={q} flush skeletonHeight={220}>
      {(d) => <DataTable columns={cols} rows={d.marks} rowKey={(r) => r.ticker} empty="No marks in this reading." />}
    </Panel>
  );
}

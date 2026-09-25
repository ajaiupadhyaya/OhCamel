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
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { DataTable, Page, Panel, Section, SegmentedControl, useTabParam, type Column } from "../components";
import { DataUnavailableError, type ApiError } from "../lib/api";
import { fmtDate, fmtNum, fmtPct, fmtRelativeTime, fmtSignedPct } from "../lib/format";
import { usePortfolio } from "../lib/portfolio";
import { useApiPost, useApiQuery } from "../lib/query";
import type { HistoryOut, SnapshotOut } from "./engine/types";
import { LampPanel } from "./deck/LampPanel";
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
  const [raw, setSource] = useTabParam<string>("source", "portfolio");
  const source: DeckSource = isSource(raw) ? raw : "portfolio";
  const label = SOURCES.find((s) => s.value === source)!.label;

  return (
    <Page
      eyebrow={`Live · ${label}`}
      title="Flight Deck"
      subtitle="Your book's limits, risk and data feeds as three cockpit instruments, each read from real data and refreshed while you watch. Nothing is simulated: a number that cannot be computed is shown as missing."
      actions={<SegmentedControl<DeckSource> className="dk-source" ariaLabel="What the deck is flying" options={SOURCES} value={source} onChange={(v) => setSource(v)} />}
    >
      {/* keyed: a new source is a new set of queries, so no reading from another source is ever shown as a placeholder */}
      <DeckBody key={source} source={source} />
    </Page>
  );
}

function DeckBody({ source }: { source: DeckSource }) {
  const { request, portfolio } = usePortfolio();
  const limits = useDeckLimits();
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
      <StatusStrip q={q} source={source} />
      {q.isError ? (
        <SourceError source={source} error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <>
          <Section title="Instruments" description={`Refreshed ${q.cadence}. Each screen has a plain-text equivalent beside or below it.`}>
            <Instruments q={q} />
          </Section>
          <Section title="Session tape" description="The day so far: how full each limit has been, and day P&L against the 1-day VaR line.">
            <Tape source={source} q={q} />
          </Section>
          <Section title="Marks" description="Every price behind the reading, with how old it is and where it came from.">
            <MarksPanel q={q} />
          </Section>
        </>
      )}
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
    </>
  );
}

// ------------------------------------------------------------------ status

function StatusStrip({ q, source }: { q: DeckQuery; source: DeckSource }) {
  const m = q.data;
  if (!m) return null;
  const clock = m.clock;
  return (
    <div className="dk-status" role="status">
      {clock ? (
        <span className={`badge ${clock.is_open ? "gain" : "unknown"}`} title={clock.note ?? undefined}>
          US session {clock.is_open ? "open" : "closed"}
          {clock.source ? ` · ${clock.source}` : ""}
        </span>
      ) : (
        source === "engine" && <span className="badge accent">Engine book · no session clock</span>
      )}
      {m.as_of && (
        <span className="subtle small">
          Reading <span className="num">{fmtRelativeTime(m.as_of.replace(" ", "T"))}</span>
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

function Instruments({ q }: { q: DeckQuery }) {
  const m = q.data;
  const unevaluated = useMemo(() => (m?.unevaluated ?? []).map((u) => u.name), [m?.unevaluated]);
  return (
    <div className="dk-deck">
      <Panel<DeckModel>
        className="dk-inst dk-inst-scope"
        title="A · Targeting computer"
        subtitle="How close is the book to each of its limits? The nearest is the target; the red readout is its room to spare."
        info={{ text: "Every limit still ahead is a gate in the tunnel, drawn nearer the fuller it is (scale 1 / (1 + 5·headroom)). A breached limit has passed the screen and is a pair of red rails. A limit that cannot be evaluated is the dashed gate beyond the vanishing point, never drawn as far away and safe. Money limits read in dollars, fractions in basis points.", reference: "After Figure 2 of the OhCamel desk (web/scope.js)." }}
        query={q}
        notes={m?.unevaluated.length ? m.unevaluated.map((u) => `${u.name}: ${u.reason}.`) : []}
        provenance={[]}
        skeletonHeight={320}
      >
        {(d) => <TargetingComputer limits={d.limits} unevaluated={unevaluated} stale={q.stale} />}
      </Panel>
      <Panel<DeckModel>
        className="dk-inst"
        title="B · Radar"
        subtitle="Where is the risk, and what is moving today?"
        info={{ title: "Euler share of VaR", text: "Each holding sits at a fixed bearing (book order). Its distance from the centre is its share of the book's 1-day parametric VaR (EWMA covariance, live weights); rings at 10, 25 and 50 %; beyond 60 % it is pinned to the rim with a tick. Size is its live weight; hollow means its component VaR is negative (it hedges).", formula: "\\text{share}_i = \\frac{w_i (\\Sigma w)_i}{w^\\top \\Sigma w}", reference: "Tasche (2000)" }}
        query={q}
        notes={[]}
        provenance={[]}
        skeletonHeight={320}
      >
        {(d) => <Radar blips={d.blips} readingKey={d.as_of} />}
      </Panel>
      <Panel<DeckModel>
        className="dk-inst"
        title="C · Lamp panel"
        subtitle="Is the data behind this alive?"
        info={{ text: "A square lamp for each feed the reading reports (quotes per source, the session clock, the flight recorder, the engine bridge): green live, amber stale, red down, dark off. The counters are day P&L and VaR in dollars, the oldest mark's age, and the time to the close or the next open." }}
        query={q}
        notes={[]}
        provenance={[]}
        skeletonHeight={320}
      >
        {(d) => <LampPanel model={d} />}
      </Panel>
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
  const key = useMemo(() => JSON.stringify([request.holdings, request.notional, request.start, request.end]), [request]);
  const [trail, setTrail] = useState<TrailPoint[]>(() => TRAILS.get(key) ?? []);
  useEffect(() => setTrail(TRAILS.get(key) ?? []), [key]);
  useEffect(() => {
    if (!model) return;
    setTrail((t) => {
      const next = appendTrail(t, model);
      if (next !== t) TRAILS.set(key, next);
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

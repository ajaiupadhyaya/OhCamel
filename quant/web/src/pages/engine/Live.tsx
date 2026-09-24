/**
 * The engine's live state through the read-only bridge: GET /api/engine/{status,snapshot,history}.
 * Polls every 5 s while the engine answers; a 503 renders as "bridge not enabled"
 * (OHCAMEL_QUANT_ENGINE_URL unset) or "engine unreachable" — never as stand-in numbers.
 */
import { useMemo } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, Panel, StatGrid, StatTile, TimeSeriesChart, type Column } from "../../components";
import { Icon } from "../../components/Icon";
import { DataUnavailableError } from "../../lib/api";
import { fmtCurrency, fmtDate, fmtNum, fmtPct, fmtRelativeTime } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import type { EngineLimit, EnginePosition, EngineStatus, HistoryOut, SnapshotOut } from "./types";

const LIVE_HOST = "https://live.ohcamel.ajaiupadhyaya.com";
const POLL = 5000;
const usd = (x: number | null | undefined) => fmtCurrency(x, { digits: 0 });
const usdC = (x: number | null | undefined) => fmtCurrency(x, { compact: true, digits: 1 });

function uptime(s: number | undefined): string {
  if (s == null) return "—";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

export function LiveEngine() {
  const status = useApiQuery<EngineStatus>("/engine/status", undefined, { refetchInterval: (q) => (q.state.status === "success" ? POLL : false), staleTime: 0 });
  const up = status.isSuccess;
  const snap = useApiQuery<SnapshotOut>("/engine/snapshot", undefined, { enabled: up, refetchInterval: up ? POLL : false, staleTime: 0 });
  const hist = useApiQuery<HistoryOut>("/engine/history", undefined, { enabled: up, refetchInterval: up ? POLL : false, staleTime: 0 });

  if (status.isLoading) return <Panel title="Engine status" loading skeletonHeight={160} />;
  if (status.isError) {
    const body = (status.error as DataUnavailableError | undefined)?.body as { configured?: boolean } | undefined;
    if (status.error instanceof DataUnavailableError && body?.configured === false) return <BridgeOff detail={status.error.detail} />;
    return (
      <Panel title="Engine status" subtitle="The bridge is configured but the engine did not answer." query={status}>
        {null}
      </Panel>
    );
  }
  const st = status.data!;
  const s = snap.data?.snapshot;
  return (
    <div className="stack-lg">
      <Panel<EngineStatus> title="Engine status" subtitle="Is the OCaml process up, which feed is it on, and is its graph moving? Polled every 5 seconds." query={status} skeletonHeight={100}>
        {() => (
          <div className="en-status">
            <span className={`badge ${st.ops?.mode === "live" ? "gain" : st.ops?.mode === "demo" ? "warn" : "unknown"}`}>
              <span className="en-pulse" /> {st.ops?.mode === "live" ? "LIVE" : st.ops?.mode === "demo" ? "DEMO · synthetic feed" : "mode unknown"}
            </span>
            <Kv k="Feed" v={st.health.healthy ? "healthy" : `${st.health.stale.length} stale · ${st.health.never_seen.length} never seen`} tone={st.health.healthy ? "gain" : "warn"} />
            <Kv k="Symbols" v={String(st.health.symbols.length)} />
            <Kv k="Uptime" v={uptime(st.ops?.uptime_s)} />
            <Kv k="Round trip" v={`${fmtNum(st.latency_ms, 0)} ms`} />
            <Kv k="Build" v={st.ops?.build?.git_sha ?? "—"} />
            {s && <Kv k="Nodes recomputed" v={`${s.nodes_recomputed.toLocaleString()}${s.nodes_recomputed_delta ? ` (+${s.nodes_recomputed_delta})` : ""}`} />}
            <a className="btn btn-sm" href={LIVE_HOST} target="_blank" rel="noreferrer">
              Open the live desk <Icon name="external" size={13} />
            </a>
          </div>
        )}
      </Panel>

      <Panel<SnapshotOut> title="Book risk now" subtitle={s ? `Snapshot as of ${fmtRelativeTime(s.as_of.replace(" ", "T"))} — value at risk, shortfall and exposure recomputed on the last tick.` : "The engine's whole book as of its last stabilization."} query={snap} skeletonHeight={130}>
        {(d) => {
          const x = d.snapshot;
          return (
            <StatGrid min={150}>
              <StatTile label="Equity" value={usdC(x.equity)} caption={x.current_drawdown != null ? `drawdown ${fmtPct(x.current_drawdown, 2)}` : undefined} />
              <StatTile label="Gross exposure" value={usdC(x.gross_exposure)} caption={x.net_exposure != null ? `net ${usdC(x.net_exposure)}` : undefined} />
              <StatTile label="VaR (1-day)" value={usd(x.value_at_risk_notional)} tone="loss" info="var" caption={x.historical_var != null ? `${fmtPct(x.historical_var, 2)} historical` : "warming up"} />
              <StatTile label="Expected shortfall" value={usd(x.expected_shortfall_notional)} tone="loss" info="es" caption={x.expected_shortfall != null ? fmtPct(x.expected_shortfall, 2) : undefined} />
              <StatTile label="Parametric VaR" value={x.parametric_var ?? null} format={(v) => fmtPct(v, 2)} info={{ title: "Parametric VaR", text: "Normal (variance–covariance) VaR from the engine's covariance matrix; the EWMA variant weights recent returns more (RiskMetrics λ).", formula: "z_\\alpha\\sqrt{w^\\top\\Sigma w}" }} caption={x.parametric_var_ewma != null ? `EWMA ${fmtPct(x.parametric_var_ewma, 2)} (λ ${fmtNum(x.ewma_lambda, 2)})` : undefined} />
              <StatTile label={`Beta vs ${x.factor ?? "market"}`} value={x.portfolio_beta ?? null} format={(v) => fmtNum(v, 2)} info="beta" />
              <StatTile label="Diversification ratio" value={x.diversification_ratio ?? null} format={(v) => `${fmtNum(v, 2)}×`} info={{ title: "Diversification ratio", text: "Sum of the positions' standalone risks divided by the book's risk. 1× means no diversification; higher means positions offset each other.", reference: "Choueifaty & Coignard (2008), JPM 35(1)" }} />
            </StatGrid>
          );
        }}
      </Panel>

      {s && (
        <div className="grid-2">
          <Panel title="Risk versus money" subtitle="Each position's share of the book's capital next to its share of VaR (Euler allocation). Bars that differ show where risk hides." info={{ title: "Euler risk contribution", text: "Component VaR = weight × marginal VaR; the components add up to total VaR, so each is a position's fair share of the risk.", formula: "\\text{VaR} = \\sum_i w_i \\frac{\\partial\\,\\text{VaR}}{\\partial w_i}", reference: "Tasche (2000)" }} notes={[]}>
            <RiskVsMoney positions={s.positions} />
          </Panel>
          <Panel title="Limits" subtitle="Each risk limit, how much of it is used, and whether it is breached." info={{ text: "Limits are configured in the engine; utilisation = observed / threshold. Red bars are breached." }} notes={s.unevaluated?.length ? [`Not yet evaluated: ${s.unevaluated.join(", ")}`] : []}>
            <Limits limits={s.limits ?? []} />
          </Panel>
        </div>
      )}

      {s && (
        <Panel<SnapshotOut> title="Positions" subtitle="The book line by line, with each position's contribution to value at risk." query={snap} flush notes={[]} provenance={[]}>
          {(d) => <Positions rows={d.snapshot.positions} />}
        </Panel>
      )}

      <Panel<HistoryOut> title="Intraday trail" subtitle="What the engine has seen since it last started: equity, and VaR/ES in dollars. Kept in memory only." query={hist} skeletonHeight={240}>
        {(d) => <Trail h={d.history} />}
      </Panel>
    </div>
  );
}

function Kv({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return (
    <span className="en-kv">
      <span className="subtle small">{k}</span> <span className={`num ${tone ?? ""}`}>{v}</span>
    </span>
  );
}

function BridgeOff({ detail }: { detail: string }) {
  return (
    <section className="oc-panel en-off">
      <div className="en-off-head">
        <div className="oc-state-icon en-off-icon">
          <Icon name="cloud-off" size={20} />
        </div>
        <div>
          <h3 className="en-off-title">The engine bridge is not enabled on this server</h3>
          <p className="subtle small">
            This app reads the engine through a narrow, GET-only bridge. It is switched off here, so there is no live book to show — and nothing is simulated in its place.
          </p>
          <div className="oc-state-detail num en-off-detail">{detail}</div>
        </div>
      </div>
      <div className="en-off-grid">
        <div>
          <div className="en-mini">See it running</div>
          <p className="small">
            The engine runs 24/7 on its own private host, behind a password.{" "}
            <a href={LIVE_HOST} target="_blank" rel="noreferrer">
              live.ohcamel.ajaiupadhyaya.com <Icon name="external" size={12} />
            </a>
          </p>
        </div>
        <div>
          <div className="en-mini">Turn the bridge on</div>
          <p className="small">
            Set <code className="num">OHCAMEL_QUANT_ENGINE_URL</code> to the engine's base URL (on the droplet, <code className="num">http://ohcamel-live:8081</code>) and restart the quant server. Only <code className="num">/api/health</code>, <code className="num">/api/ops</code>, <code className="num">/api/snapshot</code> and{" "}
            <code className="num">/api/history</code> are ever read.
          </p>
        </div>
        <div>
          <div className="en-mini">What appears here then</div>
          <p className="small">Feed health and mode, book equity and exposure, 1-day VaR and expected shortfall (historical, parametric, EWMA), beta, Euler risk contributions per position, limit utilisation, and the intraday trail — refreshed every 5 seconds.</p>
        </div>
      </div>
    </section>
  );
}

function RiskVsMoney({ positions }: { positions: EnginePosition[] }) {
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const x = positions.map((p) => p.symbol);
      return [
        { type: "bar", name: "Share of capital", x, y: positions.map((p) => p.weight), marker: { color: t.categorical[0] }, hovertemplate: "<b>%{x}</b> capital %{y:.1%}<extra></extra>" },
        { type: "bar", name: "Share of VaR", x, y: positions.map((p) => p.risk_share), marker: { color: t.categorical[1] }, hovertemplate: "<b>%{x}</b> VaR %{y:.1%}<extra></extra>" },
      ] as Data[];
    },
    [positions],
  );
  const layout = useMemo(() => ({ barmode: "group", showlegend: true, xaxis: { type: "category", showspikes: false }, yaxis: { tickformat: ".0%", zeroline: true }, margin: { l: 44, r: 8, t: 30, b: 30 } }) as any, []);
  return <Chart data={data} layout={layout} height={280} ariaLabel="Capital share vs VaR share per position" />;
}

const LIMIT_NAME = (s: string) => s.replace(/_/g, " ").replace(/\bvar\b/i, "VaR").replace(/^\w/, (c) => c.toUpperCase());

function Limits({ limits }: { limits: EngineLimit[] }) {
  if (!limits.length) return <div className="subtle small">No limits are configured on this engine.</div>;
  const fmt = (v: number | null, unit: string) => (v == null ? "—" : unit === "USD" ? usd(v) : unit === "fraction" ? fmtPct(v, 1) : fmtNum(v, 2));
  return (
    <ul className="en-limits">
      {limits.map((l) => {
        const u = l.utilisation ?? 0;
        return (
          <li key={`${l.name}:${l.scope}`}>
            <div className="en-limit-head">
              <span>
                {LIMIT_NAME(l.name)} <span className="subtle small">· {l.scope}</span>
              </span>
              <span className={`num small ${l.breached ? "loss" : ""}`}>
                {fmt(l.observed, l.unit)} / {fmt(l.threshold, l.unit)} {l.breached && <span className="badge loss">breached</span>}
              </span>
            </div>
            <div className="en-limit-bar">
              <span className={`en-limit-fill ${l.breached ? "breach" : u > 0.8 ? "near" : ""}`} style={{ width: `${Math.min(1, u) * 100}%` }} />
            </div>
            <div className="subtle small num">{l.utilisation != null ? `${fmtPct(l.utilisation, 0)} used` : "not evaluated"}</div>
          </li>
        );
      })}
    </ul>
  );
}

function Positions({ rows }: { rows: EnginePosition[] }) {
  const cols: Column<EnginePosition>[] = [
    { key: "symbol", label: "Symbol", render: (r) => <strong className="num">{r.symbol}</strong> },
    { key: "sector", label: "Sector", hideBelow: 900 },
    { key: "qty", label: "Qty", numeric: true, format: (v) => fmtNum(v, 0) },
    { key: "price", label: "Mark", numeric: true, format: (v) => fmtNum(v, 2) },
    { key: "exposure", label: "Exposure", numeric: true, format: (v) => usd(v), color: "sign" },
    { key: "weight", label: "Weight", numeric: true, format: (v) => fmtPct(v, 1) },
    { key: "component_var", label: "Component VaR", numeric: true, format: (v) => usd(v), info: { text: "This position's additive share of the book's dollar VaR (Euler)." } },
    { key: "risk_share", label: "VaR share", numeric: true, format: (v) => fmtPct(v, 1), heat: { min: 0, max: 0.5 } },
    { key: "risk_over_money", label: "Risk / money", numeric: true, format: (v) => (v == null ? "—" : `${fmtNum(v, 2)}×`), hideBelow: 900, info: { text: "VaR share divided by capital share: above 1× the position carries more risk than its size suggests." } },
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => r.symbol} defaultSort={{ key: "exposure", dir: "desc" }} />;
}

const EQ_LAYOUT = { yaxis: { tickformat: ",.0f" } } as any; // "$" comes from yFormat="usd" (tickprefix)

function Trail({ h }: { h: HistoryOut["history"] }) {
  const x = useMemo(() => h.time.map((t) => new Date(t).toISOString()), [h.time]);
  const eq = useMemo(() => [{ name: "Equity", x, y: h.equity }], [x, h.equity]);
  const risk = useMemo(
    () => [
      { name: "VaR", x, y: h.var_notional, color: "var(--loss)" },
      { name: "Expected shortfall", x, y: h.es_notional, color: "var(--warn)" },
    ],
    [x, h.var_notional, h.es_notional],
  );
  if (!h.points) return <div className="subtle small">The trail is empty: the engine restarted recently and has not appended a point yet.</div>;
  return (
    <div className="grid-2">
      <div>
        <div className="en-mini">Equity</div>
        <TimeSeriesChart series={eq} yFormat="usd" height={220} layout={EQ_LAYOUT} />
      </div>
      <div>
        <div className="en-mini">1-day VaR & ES ($)</div>
        <TimeSeriesChart series={risk} yFormat="usd" height={220} layout={EQ_LAYOUT} />
      </div>
      <div className="subtle small span-all">
        {h.points.toLocaleString()} of {h.capacity.toLocaleString()} points held · from {fmtDate(x[0])} to {fmtDate(x[x.length - 1])}
      </div>
    </div>
  );
}

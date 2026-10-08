/**
 * The engine's live state through the read-only bridge: GET /api/engine/{status,snapshot,history}.
 * Polls every 5 s while the engine answers. A 503 renders INSUFFICIENT DATA · BRIDGE OFF
 * (OHCAMEL_QUANT_ENGINE_URL unset) or · DATA UNAVAILABLE (configured, unreachable); never a
 * stand-in number. Paper Tape: ruled Cells, caps labels, mono numbers, signal red only for a
 * breached limit or an unhealthy feed.
 */
import { useMemo, type CSSProperties } from "react";
import { XYChart } from "../../charts/XYChart";
import { DataTable, Panel, StatGrid, StatTile, type Column } from "../../components";
import { Absent } from "../../design";
import { DataUnavailableError } from "../../lib/api";
import { fmtCurrency, fmtMultiple, fmtNum, fmtPct } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import type { EngineLimit, EnginePosition, EngineStatus, HistoryOut, SnapshotOut } from "./types";

export const LIVE_HOST = "https://live.ohcamel.ajaiupadhyaya.com";
const POLL = 5000;
const usd = (x: number | null | undefined) => fmtCurrency(x, { digits: 0 });
const usdC = (x: number | null | undefined) => fmtCurrency(x, { compact: true, digits: 1 });
const asIso = (s: string | undefined) => (s ? s.replace(" ", "T") : undefined);

export function uptime(s: number | undefined): string {
  if (s == null) return "—";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d ? `${d}D ${h}H` : h ? `${h}H ${m}M` : `${m}M`;
}

export function Kv({ rows }: { rows: [string, string, string?][] }) {
  return (
    <dl className="oc-kv">
      {rows.map(([k, v, cls]) => (
        <div key={k} className="oc-kv-row">
          <dt>{k}</dt>
          <dd className={`num ${cls ?? ""}`}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function LiveEngine() {
  const status = useApiQuery<EngineStatus>("/engine/status", undefined, { refetchInterval: (q) => (q.state.status === "success" ? POLL : false), staleTime: 0 });
  const up = status.isSuccess;
  const snap = useApiQuery<SnapshotOut>("/engine/snapshot", undefined, { enabled: up, refetchInterval: up ? POLL : false, staleTime: 0 });
  const hist = useApiQuery<HistoryOut>("/engine/history", undefined, { enabled: up, refetchInterval: up ? POLL : false, staleTime: 0 });

  if (status.isLoading) return <Panel title="ENGINE · STATUS" loading skeletonHeight={120} />;
  if (status.isError) {
    const err = status.error;
    const off = err instanceof DataUnavailableError && (err.body as { configured?: boolean } | undefined)?.configured === false;
    return <BridgeOff off={off} detail={err instanceof DataUnavailableError ? err.detail : String(err)} />;
  }
  const st = status.data!;
  const s = snap.data?.snapshot;
  const asOf = asIso(s?.as_of);
  return (
    <>
      <div className="grid-2">
        <Panel<EngineStatus> title="ENGINE · STATUS" query={status} skeletonHeight={120} asOf={asOf} maxAgeSec={120}>
          {() => (
            <Kv
              rows={[
                ["MODE", st.ops?.mode ? st.ops.mode.toUpperCase() : "UNKNOWN"],
                ["FEED", st.health.healthy ? "HEALTHY" : `${fmtNum(st.health.stale.length, 0)} STALE · ${fmtNum(st.health.never_seen.length, 0)} NEVER SEEN`, st.health.healthy ? "" : "loss"],
                ["SYMBOLS", fmtNum(st.health.symbols.length, 0)],
                ["UPTIME", uptime(st.ops?.uptime_s)],
                ["ROUND TRIP", `${fmtNum(st.latency_ms, 0)} MS`],
                ["BUILD", st.ops?.build?.git_sha ? st.ops.build.git_sha.slice(0, 7) : "—"],
                ...(s ? ([["NODES RECOMPUTED", `${fmtNum(s.nodes_recomputed, 0)}${s.nodes_recomputed_delta ? ` · +${fmtNum(s.nodes_recomputed_delta, 0)}` : ""}`]] as [string, string][]) : []),
              ]}
            />
          )}
        </Panel>
        <Panel<SnapshotOut> title="BOOK · 1D" query={snap} skeletonHeight={120} asOf={asOf} maxAgeSec={120}>
          {(d) => {
            const x = d.snapshot;
            return (
              <StatGrid min={120}>
                <StatTile size="sm" label="EQUITY" value={usdC(x.equity)} caption={x.current_drawdown != null ? `DD ${fmtPct(x.current_drawdown, 2)}` : undefined} />
                <StatTile size="sm" label="GROSS" value={usdC(x.gross_exposure)} caption={x.net_exposure != null ? `NET ${usdC(x.net_exposure)}` : undefined} />
                <StatTile size="sm" label="VAR $" value={usd(x.value_at_risk_notional)} info="var" caption={x.historical_var != null ? `HIST ${fmtPct(x.historical_var, 2)}` : "WARMING UP"} />
                <StatTile size="sm" label="ES $" value={usd(x.expected_shortfall_notional)} info="es" caption={x.expected_shortfall != null ? fmtPct(x.expected_shortfall, 2) : undefined} />
                <StatTile size="sm" label="VAR · PARAM" value={x.parametric_var ?? null} format={(v) => fmtPct(v, 2)} caption={x.parametric_var_ewma != null ? `EWMA ${fmtPct(x.parametric_var_ewma, 2)} · λ ${fmtNum(x.ewma_lambda, 2)}` : undefined} />
                <StatTile size="sm" label={`BETA · ${(x.factor ?? "MKT").toUpperCase()}`} value={x.portfolio_beta ?? null} format={(v) => fmtNum(v, 2)} info="beta" />
                <StatTile size="sm" label="DIV RATIO" value={x.diversification_ratio ?? null} format={(v) => fmtMultiple(v, 2)} />
              </StatGrid>
            );
          }}
        </Panel>
      </div>

      {s && (
        <div className="grid-2">
          <Panel title="RISK VS CAPITAL · EULER" notes={[]} asOf={asOf} maxAgeSec={120}>
            <Share positions={s.positions} />
          </Panel>
          <Panel title="LIMITS" flush notes={s.unevaluated?.length ? [`Not yet evaluated: ${s.unevaluated.join(", ")}`] : []} asOf={asOf} maxAgeSec={120}>
            <Limits limits={s.limits ?? []} />
          </Panel>
        </div>
      )}

      {s && (
        <Panel<SnapshotOut> title={`POSITIONS · ${fmtNum(s.positions.length, 0)}`} query={snap} flush notes={[]} provenance={[]} asOf={asOf} maxAgeSec={120}>
          {(d) => <Positions rows={d.snapshot.positions} />}
        </Panel>
      )}

      <Panel<HistoryOut> title="TRAIL · SINCE START" query={hist} skeletonHeight={220}>
        {(d) => <Trail h={d.history} />}
      </Panel>
    </>
  );
}

function BridgeOff({ off, detail }: { off: boolean; detail: string }) {
  return (
    <Panel
      title="ENGINE · BRIDGE"
      actions={
        <a className="oc-go" href={LIVE_HOST} target="_blank" rel="noreferrer">
          LIVE DESK →
        </a>
      }
    >
      <div className="en-off">
        <Absent reason={off ? "BRIDGE OFF" : "DATA UNAVAILABLE"} source={detail} />
        <Kv
          rows={[
            ["SWITCH", "OHCAMEL_QUANT_ENGINE_URL"],
            ["READS", "HEALTH · OPS · SNAPSHOT · HISTORY"],
            ["ACCESS", "GET ONLY · 2 S CACHE"],
            ["LIVE DESK", "GATED"],
          ]}
        />
      </div>
    </Panel>
  );
}

function Share({ positions }: { positions: EnginePosition[] }) {
  const max = Math.max(1e-9, ...positions.map((p) => Math.max(Math.abs(p.weight ?? 0), Math.abs(p.risk_share ?? 0))));
  const w = (v: number | null) => `${(Math.abs(v ?? 0) / max) * 100}%`;
  return (
    <table className="en-share">
      <thead>
        <tr>
          <th scope="col">SYM</th>
          <th scope="col" className="en-share-bars">
            <span className="en-key en-key-cap" aria-hidden /> CAPITAL <span className="en-key en-key-var" aria-hidden /> VAR
          </th>
          <th scope="col" className="num">CAP</th>
          <th scope="col" className="num">VAR</th>
        </tr>
      </thead>
      <tbody>
        {positions.map((p) => (
          <tr key={p.symbol}>
            <td className="num">{p.symbol}</td>
            <td className="en-share-bars">
              <span className="en-bar-cap" style={{ "--w": w(p.weight) } as CSSProperties} />
              <span className="en-bar-var" style={{ "--w": w(p.risk_share) } as CSSProperties} />
            </td>
            <td className="num">{fmtPct(p.weight, 1)}</td>
            <td className="num">{fmtPct(p.risk_share, 1)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const LIMIT_NAME = (s: string) => s.replace(/_/g, " ").toUpperCase();

function Limits({ limits }: { limits: EngineLimit[] }) {
  if (!limits.length) return <Absent reason="NO LIMITS CONFIGURED" source="engine · book limits" />;
  const fmt = (v: number | null, unit: string) => (v == null ? "—" : unit === "USD" ? usdC(v) : unit === "fraction" ? fmtPct(v, 1) : fmtNum(v, 2));
  const cols: Column<EngineLimit>[] = [
    { key: "name", label: "LIMIT", render: (l) => <span className={l.breached ? "loss" : ""}>{LIMIT_NAME(l.name)}</span> },
    { key: "scope", label: "SCOPE", hideBelow: 900, render: (l) => <span className="num">{l.scope}</span> },
    { key: "observed", label: "OBS", numeric: true, format: (v, l) => <span className={l.breached ? "loss" : ""}>{fmt(v, l.unit)}</span> },
    { key: "threshold", label: "MAX", numeric: true, format: (v, l) => fmt(v, l.unit) },
    {
      key: "utilisation",
      label: "USED",
      width: "30%",
      render: (l) =>
        l.utilisation == null ? (
          <span className="num">NOT EVALUATED</span>
        ) : (
          <span className="en-use">
            <span className="en-use-bar" aria-hidden>
              <span className={`en-use-fill ${l.breached ? "breach" : ""}`} style={{ "--w": `${Math.min(1, l.utilisation) * 100}%` } as CSSProperties} />
            </span>
            <span className={`num ${l.breached ? "loss" : ""}`}>{l.breached ? `BREACH ${fmtPct(l.utilisation, 0)}` : fmtPct(l.utilisation, 0)}</span>
          </span>
        ),
    },
  ];
  return <DataTable<EngineLimit> compact columns={cols} rows={limits} rowKey={(l) => `${l.name}:${l.scope}`} scrollLabel="Engine limits" />;
}

function Positions({ rows }: { rows: EnginePosition[] }) {
  const cols: Column<EnginePosition>[] = [
    { key: "symbol", label: "SYM", render: (r) => <span className="num">{r.symbol}</span> },
    { key: "sector", label: "SECTOR", hideBelow: 900 },
    { key: "qty", label: "QTY", numeric: true, format: (v) => fmtNum(v, 0) },
    { key: "price", label: "MARK", numeric: true, format: (v) => fmtNum(v, 2) },
    { key: "exposure", label: "EXPOSURE", numeric: true, format: (v) => usd(v) },
    { key: "weight", label: "WEIGHT", numeric: true, hideBelow: 600, format: (v) => fmtPct(v, 1) },
    { key: "component_var", label: "VAR $ · EULER", numeric: true, hideBelow: 600, format: (v) => usd(v) },
    { key: "risk_share", label: "VAR SHARE", numeric: true, format: (v) => fmtPct(v, 1) },
    { key: "risk_over_money", label: "RISK/CAP", numeric: true, hideBelow: 900, format: (v) => fmtMultiple(v, 2) },
  ];
  return <DataTable compact columns={cols} rows={rows} rowKey={(r) => r.symbol} scrollLabel="Engine positions" defaultSort={{ key: "exposure", dir: "desc" }} />;
}

function Trail({ h }: { h: HistoryOut["history"] }) {
  // Minutes before the latest point: the trail is intraday, so a date axis would read one day.
  const x = useMemo(() => h.time.map((t) => (t - (h.time[h.time.length - 1] ?? t)) / 60_000), [h.time]);
  if (!h.points) return <Absent reason="NO POINTS SINCE RESTART" source="GET /api/engine/history" />;
  return (
    <div className="grid-2">
      <XYChart x={x} xTitle="MIN" xFormat="int" series={[{ name: "EQUITY", y: h.equity, tone: "ink" }]} yFormat="usd" digits={0} height={200} ariaLabel="Engine book equity since the engine started" />
      <XYChart
        x={x}
        xTitle="MIN"
        xFormat="int"
        series={[
          { name: "VAR", y: h.var_notional, tone: "ink" },
          { name: "ES", y: h.es_notional, tone: "ink2", dash: "dash" },
        ]}
        yFormat="usd"
        digits={0}
        zero
        height={200}
        ariaLabel="Engine 1-day VaR and expected shortfall in dollars since the engine started"
      />
      <div className="en-trail-meta num span-all">
        {fmtNum(h.points, 0)} / {fmtNum(h.capacity, 0)} POINTS · IN MEMORY
      </div>
    </div>
  );
}

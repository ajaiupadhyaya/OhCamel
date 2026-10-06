/**
 * The masthead's status cluster: DATA (newest bar on the tape), ENGINE (mode from
 * /api/ops), JOBS (the queue from /api/ops), CPU (/api/ops/host), each a mono readout
 * with a square lamp, then the active book as a text readout linking to Portfolio Lab.
 * A field the server does not send reads "—" with a hatched lamp.
 */
import { Link } from "react-router-dom";
import { fmtDate, fmtPct, parseDate } from "../lib/format";
import { usePortfolio } from "../lib/portfolio";
import { useHost, useOps } from "./ops";
import { useTape } from "./Tape";
import { Lamp, type LampState } from "../design";

function Readout({ label, value, state, title }: { label: string; value: string; state: LampState; title?: string }) {
  return (
    <span className="oc-readout" title={title}>
      <Lamp state={state} label={label} />
      <span className="oc-readout-label">{label}</span>
      <span className="oc-readout-value num">{value}</span>
    </span>
  );
}

const DAY = 86_400_000;
/** A daily bar older than four days (a long weekend) is stale. */
const DATA_MAX_AGE_DAYS = 4;

export function StatusCluster() {
  const tape = useTape();
  const ops = useOps();
  const host = useHost();
  const { portfolio } = usePortfolio();

  const asOf = tape.data?.as_of ?? null;
  const ageDays = asOf ? (Date.now() - (parseDate(asOf)?.getTime() ?? NaN)) / DAY : NaN;
  const data: [string, LampState] = tape.isError
    ? ["DOWN", "fault"]
    : asOf
      ? [fmtDate(asOf, "short").toUpperCase(), ageDays > DATA_MAX_AGE_DAYS ? "stale" : "ok"]
      : tape.isLoading
        ? ["…", "idle"]
        : ["—", "stale"];

  const mode = ops.data?.mode;
  const engine: [string, LampState] = mode ? [mode.toUpperCase(), "ok"] : ["—", "stale"];

  const j = ops.data?.jobs;
  const jobs: [string, LampState] = j
    ? [`${j.running ?? 0} RUN · ${j.queued ?? 0} Q`, (j.failed_24h ?? 0) > 0 ? "fault" : (j.running ?? 0) > 0 ? "ok" : "idle"]
    : ["—", "stale"];

  const c = host.data?.latest?.cpu;
  const cpu: [string, LampState] = typeof c === "number" && Number.isFinite(c) ? [fmtPct(c, 0), c >= 0.85 ? "fault" : "ok"] : ["—", "stale"];

  const n = portfolio.holdings.length;
  return (
    <div className="oc-status" aria-label="System status">
      <Readout label="DATA" value={data[0]} state={data[1]} title={asOf ? `Newest daily bar on the tape: ${asOf}` : "No bar date from /api/market/overview"} />
      <Readout label="ENGINE" value={engine[0]} state={engine[1]} title={mode ? "Engine mode from /api/ops" : "/api/ops does not report the engine"} />
      <Readout label="JOBS" value={jobs[0]} state={jobs[1]} title={j ? `${j.failed_24h ?? 0} failed in 24 h` : "/api/ops does not report jobs"} />
      <Readout label="CPU" value={cpu[0]} state={cpu[1]} title={typeof c === "number" ? "Host CPU from /api/ops/host" : "/api/ops/host unavailable"} />
      <Link to="/portfolio" className="oc-readout oc-book" title="Active book — open Portfolio Lab">
        <span className="oc-readout-label">BOOK</span>
        <span className="oc-readout-value">
          {(portfolio.name || "Untitled").toUpperCase()} · <span className="num">{n}</span> POS
        </span>
      </Link>
    </div>
  );
}

/**
 * The session scanner's Monte Carlo line (F3, compute plan M2): the intraday MC VaR the reading
 * carries as `mc_var`, from the latest risk.mc_intraday artifact, always with its asOf. Absent
 * reads as absent; an asOf older than one missed 15-minute run is marked STALE (live.ts).
 */
import { fmtCurrency, fmtCompact } from "../../lib/format";
import { mcIsStale, mcLabel, type McVar } from "./live";

const NY = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

export function McReadout({ mc, now }: { mc: McVar | null; now: number }) {
  if (!mc) {
    return (
      <p className="dk-mc dk-mc-absent" role="status">
        <span className="dk-mc-label">MC VAR · INTRADAY</span>
        <span className="dk-mc-value">NOT IN READING</span>
      </p>
    );
  }
  const stale = mcIsStale(mc, now);
  const t = Date.parse(mc.as_of);
  return (
    <p className={`dk-mc${stale ? " stale" : ""}`} role="status">
      <span className="dk-mc-label">{mcLabel(mc)}</span>
      <span className="dk-mc-value num">{fmtCurrency(mc.var_usd, { digits: 0 })}</span>
      <span className="dk-mc-meta num">
        ASOF {Number.isFinite(t) ? `${NY.format(t).toUpperCase()} NY` : mc.as_of}
        {mc.paths != null ? ` · ${fmtCompact(mc.paths, 0)} PATHS` : ""}
        {mc.artifact_id ? ` · ${mc.artifact_id}` : ""}
      </span>
      {stale && <span className="dk-mc-stale">STALE</span>}
    </p>
  );
}

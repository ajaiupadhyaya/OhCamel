/** A calibrated scanner: real timestamps, gaps, dollars and per-limit utilization. */
import { useState } from "react";
import { fmtCurrency, fmtNum, fmtPct } from "../../lib/format";
import { fmtStamp } from "../../design/stamp";
import { parseTs, type TapeColumns } from "./model";
import { scanner } from "./scanner";
import { useBox } from "./useBox";

/** Why a tape is empty, as labels (no sentences on the public deck). */
export const TAPE_EMPTY = {
  reference: "RECORDER IDLE · RECORDS IN SESSION",
  engine: "ENGINE RESTARTED · NO POINT YET",
  trail: "FIRST READING STARTS THE TAPE",
} as const;

const time = (s: string) => new Date(s).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });
export function SessionTape({ tape, emptyText }: { tape: TapeColumns; emptyText: string }) {
  const [picked, setPicked] = useState<number | null>(null);
  // console scale: the plot is drawn in the pixels of the scanner's own width (fit.ts, F2)
  const [box, size] = useBox<HTMLDivElement>({ width: 1000, height: 0 });
  const width = Math.max(280, Math.floor(size.width));
  const plot = scanner(tape, width);
  if (!plot) return <div className="dk-empty-tape"><span>NO RECORDED OBSERVATIONS</span><p>{emptyText}</p><div className="dk-empty-scale" aria-hidden="true" /></div>;
  const index = picked == null ? tape.ts.length - 1 : Math.min(picked, tape.ts.length - 1);
  const ts = tape.ts[index];
  return <div className="dk-scanner">
    <div className="dk-fit-row" ref={box} aria-hidden="true" />
    <div className="dk-scanner-key"><span>{tape.pnlLabel} / USD</span><span>−VaR / USD · dashed</span><span>◆ observed breach</span></div>
    <svg width={width} height={210} viewBox={`0 0 ${width} 210`} role="img" aria-label={`${tape.pnlLabel} and negative VaR over ${tape.ts.length} recorded observations. Time in New York. Gaps are not interpolated.`}>
      {[0, .5, 1].map(t => <g key={t}><line x1="70" x2={width-20} y1={20+t*145} y2={20+t*145} className="dk-scanner-grid" /><text x="60" y={24+t*145} textAnchor="end">{fmtNum(plot.max-t*(plot.max-plot.min),0)}</text></g>)}
      {plot.ticks.map(t => <g key={t.ts}><line x1={t.x} x2={t.x} y1="20" y2="165" className="dk-scanner-grid"/><text x={t.x} y="195" textAnchor="middle">{time(t.ts)}</text></g>)}
      <path d={plot.pnl} className="dk-scanner-line"/><path d={plot.varPath} className="dk-scanner-var"/>
      {plot.points.map(p => <circle key={p.i} cx={p.x} cy={p.y} r="2.5" className="dk-scanner-dot"/>)}
      {plot.events.map(p => <path key={p.i} d={`M${p.x},22 l5,5 -5,5 -5,-5 Z`} className="dk-scanner-event"/>)}
      <line x1={plot.x(parseTs(ts)!)} x2={plot.x(parseTs(ts)!)} y1="16" y2="169" className="dk-scanner-cursor"/>
    </svg>
    <div className="dk-scanner-readout"><label htmlFor="dk-tape-point">OBSERVATION</label><input id="dk-tape-point" type="range" min="0" max={tape.ts.length-1} value={index} onChange={e => setPicked(Number(e.target.value))} aria-valuetext={`${ts}: ${fmtNum(tape.day_pnl_usd[index],2)} dollars`} /><button onClick={() => setPicked(null)}>LATEST</button></div>
    <p className="dk-tape-value">{fmtStamp(ts)} ET · {tape.pnlLabel.toUpperCase()} {fmtCurrency(tape.day_pnl_usd[index], { digits: 2, signed: true })} · VAR {fmtCurrency(tape.var_usd[index], { digits: 2 })}<br/>{picked == null ? "LATEST" : "INSPECTING · INSTRUMENTS SHOW LATEST"}</p>
    <div className="dk-util-head"><span>LIMIT UTILIZATION</span><span>0% → 100% · red = breached · gaps = no reading</span></div>
    {Object.entries(tape.utilisation).map(([name, values]) => <div className="dk-raster-row" key={name}><span>{name}</span><svg viewBox={`0 0 ${width-90} 14`} preserveAspectRatio="none" role="img" aria-label={`${name}: ${fmtPct(values[index],1)} at selected observation`}>
      {values.map((v,i) => v == null || !Number.isFinite(v) ? null : <rect key={i} x={plot.x(parseTs(tape.ts[i])!)-70} y="1" width={plot.cellWidth(i)} height="12" fill={v>1 ? "#ff5348" : "#ffb64e"} opacity={.12 + .88*Math.min(1,Math.max(0,v))} />)}
    </svg><span>{fmtPct(values[index],0)}</span></div>)}
    {!Object.keys(tape.utilisation).length && <p className="dk-tape-value">NO PER-LIMIT UTILIZATION</p>}
  </div>;
}

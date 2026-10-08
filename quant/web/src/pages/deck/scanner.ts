import { fixedNum } from "../../lib/format";
import { parseTs, type TapeColumns } from "./model";
const valid = (x: number | null | undefined): x is number => x != null && Number.isFinite(x);
const MAX_GAP_MS = 180_000;
/** Map timestamps rather than sample indices; never connect across missing observations. */
export function scanner(tape: TapeColumns, width = 1000) {
  const times = tape.ts.map(parseTs);
  if (!times.length || times.some(t => t == null)) return null;
  const ts = times as number[];
  const start = Math.min(...ts), end = Math.max(...ts);
  const span = Math.max(60_000, end-start);
  const x = (t: number) => 70+(t-start)/span*(width-95);
  const negVar = tape.var_usd.map(v => valid(v) ? -v : null);
  const numbers = [...tape.day_pnl_usd, ...negVar, 0].filter(valid);
  const low = Math.min(...numbers), high = Math.max(...numbers), pad = Math.max(1,(high-low)*.1);
  const min=low-pad, max=high+pad;
  const y = (v: number) => 20+(max-v)/(max-min)*145;
  const path = (values: (number|null)[]) => {
    let d="", previous: number|null=null;
    values.forEach((v,i) => {
      if (!valid(v) || ts[i] == null) { previous=null; return; }
      const connect=previous!=null && ts[i]-previous<=MAX_GAP_MS && ts[i]>=previous;
      d+=`${connect ? "L" : "M"}${fixedNum(x(ts[i]), 2)},${fixedNum(y(v), 2)} `;
      previous=ts[i];
    });
    return d;
  };
  const events=ts.flatMap((t,i) => i>0 && t-ts[i-1]<=MAX_GAP_MS && Object.values(tape.utilisation).some(v => valid(v[i]) && valid(v[i-1]) && v[i]!>1 && v[i-1]!<=1) ? [{i,x:x(t)}] : []);
  const indices=[...new Set([0, Math.floor((ts.length-1)/2),ts.length-1])];
  return {min,max,x,pnl:path(tape.day_pnl_usd),varPath:path(negVar),events,
    ticks:indices.map(i=>({ts:tape.ts[i],x:x(ts[i])})),
    points:tape.day_pnl_usd.flatMap((v,i)=>valid(v) && ts[i]!=null ? [{i,x:x(ts[i]),y:y(v)}] : []),
    cellWidth:(i:number)=>Math.max(1,Math.min(60_000, i<ts.length-1 ? ts[i+1]-ts[i] : 60_000)/span*(width-95)-1),
  };
}

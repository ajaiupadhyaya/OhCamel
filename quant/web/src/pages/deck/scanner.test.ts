import { describe, expect, it } from "vitest";
import { scanner } from "./scanner";
import type { TapeColumns } from "./model";
const tape: TapeColumns = {ts:["2026-09-24T14:00:00Z","2026-09-24T14:01:00Z","2026-09-24T14:10:00Z"],day_pnl_usd:[10,20,30],var_usd:[100,100,100],es_usd:[150,150,150],utilisation:{cap:[.9,1.1,1.2]},pnlLabel:"Day P&L"};
describe("session scanner",()=>{
  it("spaces actual time and breaks across a recording outage",()=>{
    const m=scanner(tape)!;
    expect(m.pnl.match(/M/g)).toHaveLength(2);
    expect(m.ticks[1].x-m.ticks[0].x).toBeCloseTo(90.5);
    expect(m.events).toHaveLength(1);
    expect(m.events[0].i).toBe(1);
  });
  it("never fills missing data or invents crossings across gaps",()=>{
    const m=scanner({...tape,day_pnl_usd:[10,null,30],utilisation:{cap:[.9,null,1.2]}})!;
    expect(m.points).toHaveLength(2);
    expect(m.events).toHaveLength(0);
    expect(m.pnl).not.toContain("L");
  });
  it("rejects missing timestamps and an empty tape",()=>{
    expect(scanner({...tape,ts:[]})).toBeNull();
    expect(scanner({...tape,ts:["bad"]})).toBeNull();
  });
});

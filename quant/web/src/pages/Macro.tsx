/**
 * Rates & Macro (/macro) — the Treasury curve and the economy behind it.
 *
 * Tabs (state in the URL: ?tab=, plus ?ids=&tr=&from= for the explorer):
 *  - Dashboard      GET  /api/macro/dashboard        (partial offline: DGS10, DGS2, VIXCLS)
 *  - Yield curve    GET  /api/macro/curve            (full Treasury curve from FRED; 503 offline)
 *  - Curve history  GET  /api/macro/curve/history    (503 offline)
 *  - Recession      GET  /api/macro/recession        (503 offline)
 *  - Regimes        GET  /api/macro/regimes          (offline OK: committed ETF bars + FRED fixtures)
 *  - Policy         GET  /api/macro/taylor           (503 offline)
 *  - Bonds          POST /api/macro/bond on "Run"    (offline OK from a yield or price; curve analytics online)
 *  - Explorer       GET  /api/macro/series           (offline OK for DGS10, DGS2, VIXCLS)
 * Tabs whose data is unavailable show the server's reason through <Panel> next to a
 * "How it works" card, so the page reads as intentional offline — never as fake data.
 * Page-local components live in ./macro/ (classes prefixed `mc-`).
 */
import { useSearchParams } from "react-router-dom";
import { Page, Tabs, useTabParam, type TabItem } from "../components";
import { fmtDate } from "../lib/format";
import { useApiQuery } from "../lib/query";
import { BondsTab } from "./macro/BondsTab";
import { CurveTab } from "./macro/CurveTab";
import { DashboardTab } from "./macro/DashboardTab";
import { ExplorerTab } from "./macro/ExplorerTab";
import { HistoryTab } from "./macro/HistoryTab";
import { PolicyTab } from "./macro/PolicyTab";
import { RecessionTab } from "./macro/RecessionTab";
import { RegimesTab } from "./macro/RegimesTab";
import type { Dashboard } from "./macro/types";
import "./macro/macro.css";

type Tab = "dashboard" | "curve" | "history" | "recession" | "regimes" | "policy" | "bonds" | "explorer";
/** Tabs that need FRED series beyond the offline fixtures (full Treasury curve, USREC, PCE, GDP…). */
const LIVE_TABS: Tab[] = ["curve", "history", "recession", "policy"];

const TAB_BLURB: Record<Tab, string> = {
  dashboard: "The headline indicators — inflation, jobs, growth, rates, credit and markets — and how unusual each one is today.",
  curve: "Today's Treasury curve three ways (par, zero, forward), fitted with the models central banks use, against where it was.",
  history: "How the curve has moved: yields over decades, slopes and inversions, and the three factors behind almost every move.",
  recession: "What the yield curve says about the odds of a recession a year from now, and whether unemployment has started to turn.",
  regimes: "Is the market calm or stressed? A regime-switching model reads it from returns alone; a simple risk panel cross-checks it.",
  policy: "Where standard policy rules would set the fed funds rate, against where the Fed actually has it.",
  bonds: "Price any fixed-coupon bond, measure its rate risk, and compare it with the Treasury curve.",
  explorer: "Chart any FRED series — hundreds of thousands of official U.S. economic time series — with one-click transforms.",
};

export default function Macro() {
  const [tab, setTab] = useTabParam<Tab>("tab", "dashboard");
  const [, setSp] = useSearchParams();
  const health = useApiQuery<{ offline?: boolean }>("/health", undefined, { staleTime: Infinity });
  const dash = useApiQuery<Dashboard>("/macro/dashboard");
  const offline = !!health.data?.offline;

  const asOf = dash.data?.series
    .map((r) => r.date)
    .filter((d): d is string => !!d)
    .sort()
    .pop();

  const items: TabItem<Tab>[] = [
    { id: "dashboard", label: "Dashboard" },
    { id: "curve", label: "Yield curve" },
    { id: "history", label: "Curve history" },
    { id: "recession", label: "Recession" },
    { id: "regimes", label: "Regimes" },
    { id: "policy", label: "Policy" },
    { id: "bonds", label: "Bonds" },
    { id: "explorer", label: "Explorer" },
  ].map((t) => ({ ...t, id: t.id as Tab, badge: offline && LIVE_TABS.includes(t.id as Tab) ? "online" : undefined }));

  const explore = (id: string, transform: string) =>
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        n.set("tab", "explorer");
        n.set("ids", id);
        if (transform && transform !== "level") n.set("tr", transform);
        else n.delete("tr");
        return n;
      },
      { replace: false },
    );

  return (
    <Page
      eyebrow={asOf ? `FRED · latest observation ${fmtDate(asOf)}` : "Rates & Macro"}
      title="Rates & Macro"
      subtitle="The Treasury curve and the economy behind it — level, slope and curvature of rates, recession odds, market regimes and monetary policy, each explained in plain English."
      meta={
        offline ? (
          <span className="badge unknown mc-offline-badge" title="The backend runs offline: only committed FRED series (DGS10, DGS2, VIXCLS) and daily ETF bars are available.">
            Offline dataset — dashboard (partial), regimes, bonds and the explorer work; curve, recession and policy need live FRED
          </span>
        ) : undefined
      }
    >
      <div className="mc-tabs">
        <Tabs items={items} value={tab} onChange={setTab} />
        <p className="mc-tab-blurb subtle">{TAB_BLURB[tab]}</p>
      </div>
      {tab === "dashboard" && <DashboardTab q={dash} onExplore={explore} />}
      {tab === "curve" && <CurveTab />}
      {tab === "history" && <HistoryTab />}
      {tab === "recession" && <RecessionTab />}
      {tab === "regimes" && <RegimesTab />}
      {tab === "policy" && <PolicyTab />}
      {tab === "bonds" && <BondsTab />}
      {tab === "explorer" && <ExplorerTab dashboard={dash.data} />}
    </Page>
  );
}

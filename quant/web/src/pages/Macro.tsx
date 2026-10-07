/**
 * Rates & Macro (/macro): the Treasury curve and the economy behind it, plus the P6 regime product.
 *
 * Tabs (state in the URL: ?tab=, plus ?ids=&tr=&from= for the explorer):
 *  - DASH      GET  /api/macro/dashboard        (partial offline: DGS10, DGS2, VIXCLS)
 *  - CURVE     GET  /api/macro/curve            (full Treasury curve from FRED; 503 offline)
 *  - HISTORY   GET  /api/macro/curve/history    (503 offline)
 *  - RECESSION GET  /api/macro/recession        (503 offline)
 *  - REGIMES   P6   GET /api/artifacts/regime.hmm/latest (+ tables); descriptive Markov on
 *              GET /api/macro/regimes (offline OK: committed ETF bars + FRED fixtures)
 *  - POLICY    GET  /api/macro/taylor           (503 offline)
 *  - BONDS     POST /api/macro/bond on RUN      (offline OK from a yield or price)
 *  - EXPLORER  GET  /api/macro/series           (offline OK for DGS10, DGS2, VIXCLS)
 * A tab whose FRED series are unavailable reads INSUFFICIENT DATA with the server's reason and a
 * ruled list of what it computes; never placeholder numbers. Page-local code in ./macro/ (`mc-`).
 */
import { useSearchParams } from "react-router-dom";
import { Page, Tabs, useTabParam, type TabItem } from "../components";
import { fmtStamp } from "../design/stamp";
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

  const items: TabItem<Tab>[] = (
    [
      { id: "dashboard", label: "Dash" },
      { id: "curve", label: "Curve" },
      { id: "history", label: "History" },
      { id: "recession", label: "Recession" },
      { id: "regimes", label: "Regimes · P6" },
      { id: "policy", label: "Policy" },
      { id: "bonds", label: "Bonds" },
      { id: "explorer", label: "Explorer" },
    ] as { id: Tab; label: string }[]
  ).map((t) => ({ ...t, badge: offline && LIVE_TABS.includes(t.id) ? "OFFLINE" : undefined }));

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
      title="Rates & Macro"
      docTitle="Rates & Macro"
      meta={
        asOf || offline ? (
          <>
            {asOf && <span>FRED · LAST OBS {fmtStamp(asOf)}</span>}
            {offline && <span>OFFLINE DATASET · DGS2 DGS10 VIXCLS ONLY</span>}
          </>
        ) : undefined
      }
    >
      <div className="mc-tabbar">
        <Tabs items={items} value={tab} onChange={setTab} />
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

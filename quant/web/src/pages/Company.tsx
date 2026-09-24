/**
 * Company (/company/:ticker) — fundamentals from SEC EDGAR XBRL filings.
 *
 * Data (all online-only; offline the server answers 503 and every panel shows the
 * "data source unavailable" state with the server's reason):
 *   GET  /api/fundamentals/{t}/profile      identity, price, cap, EV, multiples, beta, 52w range
 *   GET  /api/fundamentals/{t}/statements   ?period=annual|quarterly|ttm (+ YoY growth)
 *   GET  /api/fundamentals/{t}/ratios       annual + rolling-TTM ratio history, CAGRs
 *   GET  /api/fundamentals/{t}/scores       Piotroski, Altman Z/Z'', Beneish, Sloan, Ohlson
 *   POST /api/fundamentals/{t}/dcf          two-stage FCFF DCF, sensitivity, reverse DCF
 *   GET  /api/market/history/{t}            price panel
 * Nothing on this page is computed in the browser except display transforms.
 */
import { useNavigate, useParams } from "react-router-dom";
import { Page, Tabs, TickerInput, useTabParam } from "../components";
import { Icon } from "../components/Icon";
import { DataUnavailableError } from "../lib/api";
import { fmtDate, fmtNum } from "../lib/format";
import { useApiQuery } from "../lib/query";
import { DcfTab } from "./company/DcfTab";
import { Landing, SuggestRow } from "./company/Landing";
import { PriceHistory, ProfileDetails, ProfileSnapshot } from "./company/ProfileSection";
import { QualityTab } from "./company/QualityTab";
import { RatiosTab } from "./company/RatiosTab";
import { StatementsTab } from "./company/StatementsTab";
import { useProfile } from "./company/shared";
import "./company/company.css";

type Tab = "statements" | "ratios" | "quality" | "dcf";
const TABS: { id: Tab; label: string }[] = [
  { id: "statements", label: "Statements" },
  { id: "ratios", label: "Ratios" },
  { id: "quality", label: "Quality scores" },
  { id: "dcf", label: "Valuation (DCF)" },
];

export default function Company() {
  const { ticker: raw = "" } = useParams();
  const t = raw.trim().toUpperCase();
  if (!t) return <Landing />;
  return <CompanyView key={t} ticker={t} />;
}

function CompanyView({ ticker }: { ticker: string }) {
  const nav = useNavigate();
  const [tab, setTab] = useTabParam<Tab>("tab", "statements");
  const profile = useProfile(ticker);
  const health = useApiQuery<{ offline?: boolean }>("/health", undefined, { staleTime: Infinity });
  const p = profile.data;
  const unavailable = profile.error instanceof DataUnavailableError;

  return (
    <Page
      eyebrow="Company · SEC EDGAR fundamentals"
      title={
        <span className="co-title">
          <span className="num co-symbol">{ticker}</span>
          {p?.name && <span className="co-name">{p.name}</span>}
        </span>
      }
      docTitle={`${ticker} · Company`}
      subtitle={
        p ? (
          <span className="co-quote">
            <span className="num co-last">${fmtNum(p.price, 2)}</span>
            <span className="subtle small">
              {p.price_as_of ? `price · ${fmtDate(p.price_as_of)}` : "latest price"} · fiscal year ends {fmtDate(p.latest_fiscal_year_end, "month")}
            </span>
          </span>
        ) : (
          "Statements, ratios, accounting-quality scores and an interactive DCF, read from the company's own SEC filings."
        )
      }
      meta={health.data?.offline ? <span className="badge unknown" title="The backend is running in offline mode: only committed daily prices are available.">Offline dataset — SEC filings need a live connection</span> : undefined}
      actions={<TickerInput placeholder="Another company…" onSelect={(t) => nav(`/company/${encodeURIComponent(t)}`)} className="co-head-search" />}
    >
      {unavailable ? (
        <div className="grid-3">
          <FundamentalsUnavailable ticker={ticker} detail={(profile.error as DataUnavailableError).detail} offline={!!health.data?.offline} />
          <PriceHistory ticker={ticker} compact />
        </div>
      ) : (
        <>
          <ProfileSnapshot q={profile} />
          <div className="grid-3">
            <div className="span-2">
              <PriceHistory ticker={ticker} />
            </div>
            <ProfileDetails q={profile} ticker={ticker} />
          </div>
        </>
      )}

      <div className="co-tabs">
        <Tabs items={TABS} value={tab} onChange={setTab} />
      </div>
      {tab === "statements" && <StatementsTab ticker={ticker} compact={unavailable} />}
      {tab === "ratios" && <RatiosTab ticker={ticker} compact={unavailable} />}
      {tab === "quality" && <QualityTab ticker={ticker} compact={unavailable} />}
      {tab === "dcf" && <DcfTab ticker={ticker} compact={unavailable} />}

      <SuggestRow exclude={ticker} />
    </Page>
  );
}

/**
 * One calm explanation instead of a wall of empty panels when SEC EDGAR cannot be read
 * (offline build, or the ticker is not an SEC filer, e.g. an ETF).
 */
function FundamentalsUnavailable({ ticker, detail, offline }: { ticker: string; detail: string; offline: boolean }) {
  const items: [string, string][] = [
    ["Statements", "Ten years of income statement, balance sheet and cash flow with YoY growth and trend lines."],
    ["Ratios", "Margins, returns on capital, leverage, liquidity, efficiency and a DuPont breakdown of ROE."],
    ["Quality scores", "Piotroski F checklist, Altman Z/Z″ zones, Beneish M indices, Sloan accruals, Ohlson O."],
    ["Valuation", "A two-stage DCF pre-filled from data, sliders to override, sensitivity grid and reverse DCF."],
  ];
  return (
    <section className="oc-panel span-2 co-unavail">
      <div className="co-unavail-head">
        <div className="oc-state-icon co-unavail-icon">
          <Icon name="cloud-off" size={20} />
        </div>
        <div>
          <h3 className="co-unavail-title">{offline ? `${ticker}'s filings need a live connection to SEC EDGAR` : `No SEC fundamentals for ${ticker}`}</h3>
          <p className="subtle small">
            {offline
              ? "This build runs on the committed offline dataset, which holds prices but no company filings. Nothing is shown rather than an approximation."
              : "The server could not build statements for this ticker. Funds and ETFs do not file company financials; foreign issuers may file under a different form."}
          </p>
          <div className="oc-state-detail num co-unavail-detail">{detail}</div>
        </div>
      </div>
      <div className="co-mini-label">What this page shows when filings are available</div>
      <ul className="co-unavail-list">
        {items.map(([t, d]) => (
          <li key={t}>
            <span className="co-unavail-t">{t}</span>
            <span className="subtle small">{d}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

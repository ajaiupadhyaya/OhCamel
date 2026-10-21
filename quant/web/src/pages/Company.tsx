/**
 * Company (/company/:ticker) — fundamentals from SEC EDGAR XBRL filings.
 *
 * Data (online only; offline the server answers 503 and the page reads INSUFFICIENT DATA with the
 * server's reason and a ruled list of what it computes — never placeholder numbers):
 *   GET  /api/fundamentals/{t}/profile      identity, price, cap, EV, multiples, beta, 52W range
 *   GET  /api/fundamentals/{t}/statements   ?period=annual|quarterly|ttm (+ YoY growth)
 *   GET  /api/fundamentals/{t}/ratios       annual + rolling-TTM ratio history, CAGRs
 *   GET  /api/fundamentals/{t}/scores       Piotroski, Altman Z/Z'', Beneish, Sloan, Ohlson
 *   POST /api/fundamentals/{t}/dcf          two-stage FCFF DCF, sensitivity, reverse DCF
 *   GET  /api/market/history/{t}            price cell (offline OK for committed tickers)
 * Nothing on this page is computed in the browser except display transforms. Page-local code
 * in ./company/ (`co-`).
 */
import { Link, useNavigate, useParams } from "react-router-dom";
import { Page, Tabs, TickerInput, useTabParam, type TabItem } from "../components";
import { DataUnavailableError } from "../lib/api";
import { fmtDate, fmtNum } from "../lib/format";
import { useApiQuery } from "../lib/query";
import { DcfTab } from "./company/DcfTab";
import { Landing, SuggestRow } from "./company/Landing";
import { PriceHistory, ProfileDetails, ProfileSnapshot } from "./company/ProfileSection";
import { QualityTab } from "./company/QualityTab";
import { RatiosTab } from "./company/RatiosTab";
import { StatementsTab } from "./company/StatementsTab";
import { LiveOnly, useProfile } from "./company/shared";
import "./company/company.css";

type Tab = "statements" | "ratios" | "quality" | "dcf";
const TABS: TabItem<Tab>[] = [
  { id: "statements", label: "Statements" },
  { id: "ratios", label: "Ratios" },
  { id: "quality", label: "Scores" },
  { id: "dcf", label: "DCF" },
];

/** What the page computes when filings are available (shown in place of it when they are not). */
export const COMPUTES: { k: string; note: string }[] = [
  { k: "STATEMENTS · IS BS CF · 10Y · YOY", note: "statements" },
  { k: "RATIOS · MARGINS ROIC LEVERAGE DUPONT CAGR", note: "ratios" },
  { k: "PIOTROSKI F · 9 TESTS", note: "piotroski" },
  { k: "ALTMAN Z · Z″ · ZONES", note: "altman" },
  { k: "BENEISH M · 8 INDICES", note: "beneish" },
  { k: "SLOAN ACCRUALS · OHLSON O", note: "accruals-ohlson" },
  { k: "DCF · FCFF TWO-STAGE · SENSITIVITY", note: "dcf" },
  { k: "REVERSE DCF · IMPLIED GROWTH", note: "reverse-dcf" },
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
  const offline = !!health.data?.offline;
  const unavailable = profile.error instanceof DataUnavailableError;

  return (
    <Page
      eyebrow={p?.name ? p.name.toUpperCase() : "SEC EDGAR"}
      title={<span className="num co-symbol">{ticker}</span>}
      docTitle={`${ticker} · Company`}
      meta={
        <>
          {p && (
            <>
              <span>
                PRICE <b className="num">${fmtNum(p.price, 2)}</b>
              </span>
              {p.price_as_of && <span>AS OF {fmtDate(p.price_as_of, "short-year").toUpperCase()}</span>}
              <span>FY END {fmtDate(p.latest_fiscal_year_end, "short-year").toUpperCase()}</span>
              {p.cik && <span>CIK {p.cik}</span>}
            </>
          )}
          {offline && <span>OFFLINE DATASET · NO SEC FILINGS</span>}
        </>
      }
      actions={
        <div className="co-head-actions">
          <TickerInput placeholder="TICKER" onSelect={(t) => nav(`/company/${encodeURIComponent(t)}`)} className="co-head-search" />
          <Link className="btn btn-sm" to={`/ticker/${encodeURIComponent(ticker)}`}>
            GP
          </Link>
          <Link className="btn btn-sm" to={`/options?ticker=${encodeURIComponent(ticker)}`}>
            VOL
          </Link>
        </div>
      }
    >
      {unavailable ? (
        <div className="grid-2">
          <LiveOnly title={`FUNDAMENTALS · ${ticker} · SEC EDGAR`} error={profile.error} items={COMPUTES} source={`/api/fundamentals/${ticker}/profile`} />
          <PriceHistory ticker={ticker} />
        </div>
      ) : (
        <>
          <ProfileSnapshot q={profile} />
          <div className="grid-3">
            <div className="span-2">
              <PriceHistory ticker={ticker} />
            </div>
            <ProfileDetails q={profile} />
          </div>
          <div className="co-tabbar">
            <Tabs items={TABS} value={tab} onChange={setTab} />
          </div>
          {tab === "statements" && <StatementsTab ticker={ticker} />}
          {tab === "ratios" && <RatiosTab ticker={ticker} />}
          {tab === "quality" && <QualityTab ticker={ticker} />}
          {tab === "dcf" && <DcfTab ticker={ticker} />}
        </>
      )}
      <SuggestRow exclude={ticker} />
    </Page>
  );
}

/** /company with no ticker: a search prompt and a few large caps to start from. */
import { Link, useNavigate } from "react-router-dom";
import { Page, TickerInput } from "../../components";
import { Icon } from "../../components/Icon";

export const SUGGESTED: { ticker: string; name: string; sector: string }[] = [
  { ticker: "AAPL", name: "Apple", sector: "Technology hardware" },
  { ticker: "MSFT", name: "Microsoft", sector: "Software" },
  { ticker: "NVDA", name: "NVIDIA", sector: "Semiconductors" },
  { ticker: "AMZN", name: "Amazon", sector: "Retail & cloud" },
  { ticker: "GOOGL", name: "Alphabet", sector: "Internet services" },
  { ticker: "META", name: "Meta Platforms", sector: "Internet services" },
  { ticker: "JPM", name: "JPMorgan Chase", sector: "Banks" },
  { ticker: "XOM", name: "Exxon Mobil", sector: "Energy" },
  { ticker: "JNJ", name: "Johnson & Johnson", sector: "Health care" },
  { ticker: "COST", name: "Costco", sector: "Consumer staples" },
];

export function Landing() {
  const nav = useNavigate();
  return (
    <Page eyebrow="Company research" title="Look under the hood" subtitle="Financial statements, ratios, accounting-quality scores and an interactive DCF for any SEC-filing company — every figure read from its 10-K and 10-Q filings." docTitle="Company">
      <div className="co-landing">
        <TickerInput autoFocus placeholder="Search a company or ticker…" onSelect={(t) => nav(`/company/${encodeURIComponent(t)}`)} className="co-landing-search" />
        <div className="co-mini-label">Or start with a large cap</div>
        <div className="co-suggest">
          {SUGGESTED.map((s) => (
            <Link key={s.ticker} to={`/company/${s.ticker}`} className="co-suggest-card">
              <span className="num co-suggest-t">{s.ticker}</span>
              <span className="co-suggest-n">{s.name}</span>
              <span className="subtle small">{s.sector}</span>
              <Icon name="arrow-up-right" size={14} className="co-suggest-arrow" />
            </Link>
          ))}
        </div>
      </div>
    </Page>
  );
}

/** A compact row of suggestion links (used at the foot of a company page). */
export function SuggestRow({ exclude }: { exclude: string }) {
  return (
    <div className="co-suggest-row">
      <span className="subtle small">Other companies:</span>
      {SUGGESTED.filter((s) => s.ticker !== exclude).map((s) => (
        <Link key={s.ticker} to={`/company/${s.ticker}`} className="badge" title={s.name}>
          {s.ticker}
        </Link>
      ))}
    </div>
  );
}

/** /company with no ticker: the search and a ruled list of large caps to start from. */
import { Link, useNavigate } from "react-router-dom";
import { Page, Panel, TickerInput } from "../../components";

export const SUGGESTED: { ticker: string; name: string; sector: string }[] = [
  { ticker: "AAPL", name: "Apple", sector: "TECH HARDWARE" },
  { ticker: "MSFT", name: "Microsoft", sector: "SOFTWARE" },
  { ticker: "NVDA", name: "NVIDIA", sector: "SEMICONDUCTORS" },
  { ticker: "AMZN", name: "Amazon", sector: "RETAIL · CLOUD" },
  { ticker: "GOOGL", name: "Alphabet", sector: "INTERNET" },
  { ticker: "META", name: "Meta Platforms", sector: "INTERNET" },
  { ticker: "JPM", name: "JPMorgan Chase", sector: "BANKS" },
  { ticker: "XOM", name: "Exxon Mobil", sector: "ENERGY" },
  { ticker: "JNJ", name: "Johnson & Johnson", sector: "HEALTH CARE" },
  { ticker: "COST", name: "Costco", sector: "STAPLES" },
];

export function Landing() {
  const nav = useNavigate();
  return (
    <Page title="Company" docTitle="Company" meta={<span>SEC EDGAR XBRL · 10-K 10-Q</span>}>
      <div className="co-landing">
        <TickerInput autoFocus placeholder="TICKER" onSelect={(t) => nav(`/company/${encodeURIComponent(t)}`)} className="co-landing-search" />
        <Panel title="LARGE CAPS" flush notes={[]} provenance={[]}>
            <table className="oc-table oc-table-compact co-suggest">
              <thead>
                <tr>
                  <th>TICKER</th>
                  <th>NAME</th>
                  <th>SECTOR</th>
                </tr>
              </thead>
              <tbody>
                {SUGGESTED.map((s) => (
                  <tr key={s.ticker}>
                    <td className="num">
                      <Link to={`/company/${s.ticker}`}>{s.ticker}</Link>
                    </td>
                    <td>{s.name}</td>
                    <td className="co-dim">{s.sector}</td>
                  </tr>
                ))}
              </tbody>
            </table>
        </Panel>
      </div>
    </Page>
  );
}

/** A compact row of company links at the foot of a company page. */
export function SuggestRow({ exclude }: { exclude: string }) {
  return (
    <nav className="co-suggest-row num" aria-label="Other companies">
      <span className="co-ctl-k">OTHER</span>
      {SUGGESTED.filter((s) => s.ticker !== exclude).map((s) => (
        <Link key={s.ticker} to={`/company/${s.ticker}`} title={s.name}>
          {s.ticker}
        </Link>
      ))}
    </nav>
  );
}

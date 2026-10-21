/** The footer: a double rule, the build, the data sources, Methodology and Ledger. */
import { Link } from "react-router-dom";
import { useOps } from "./ops";

const SOURCES = "YAHOO · ALPACA · FRED · SEC EDGAR · CBOE · K. FRENCH · OPENFIGI";

export function Footer() {
  const ops = useOps();
  const sha = ops.data?.build?.git_sha;
  return (
    <footer className="oc-footer num">
      <span className="oc-footer-build" title={sha ?? "/api/ops does not report a build"}>
        BUILD {sha ? sha.slice(0, 7) : "—"}
      </span>
      <span className="oc-footer-sources">SOURCES {SOURCES}</span>
      <span className="oc-footer-links">
        <Link to="/methodology">METHODOLOGY</Link>
        <Link to="/ledger">LEDGER</Link>
      </span>
    </footer>
  );
}

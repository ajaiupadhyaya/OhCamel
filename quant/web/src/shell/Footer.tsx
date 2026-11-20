/** The footer: a double rule, the build (only where the API reports one), the data sources, Methodology and Ledger. */
import { Link } from "react-router-dom";
import { useOps } from "./ops";

const SOURCES = "YAHOO · ALPACA · FRED · SEC EDGAR · CBOE · K. FRENCH · OPENFIGI";

export function Footer() {
  const ops = useOps();
  const sha = ops.data?.build?.git_sha;
  return (
    <footer className="oc-footer num">
      {/* The public app sends no commit SHA (the owner's ruling, 2026-11-20; api/redact.py): shown only when present */}
      {sha && (
        <span className="oc-footer-build" title={sha}>
          BUILD {sha.slice(0, 7)}
        </span>
      )}
      <span className="oc-footer-sources">SOURCES {SOURCES}</span>
      <span className="oc-footer-links">
        <Link to="/methodology">METHODOLOGY</Link>
        <Link to="/ledger">LEDGER</Link>
      </span>
    </footer>
  );
}

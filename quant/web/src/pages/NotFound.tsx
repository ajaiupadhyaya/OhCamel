import { Link } from "react-router-dom";
import { Page } from "../components";

export default function NotFound() {
  return (
    <Page title="Not found">
      <div className="oc-state oc-state-empty">
        <div className="oc-state-title">404 · NO PAGE</div>
        <div className="oc-state-reason num">/ · ⌘K · TICKER OR PAGE</div>
        <div className="oc-state-action">
          <Link className="btn" to="/">
            FRONT
          </Link>
        </div>
      </div>
    </Page>
  );
}

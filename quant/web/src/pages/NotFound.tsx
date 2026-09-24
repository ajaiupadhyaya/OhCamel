import { Link } from "react-router-dom";
import { EmptyState, Page } from "../components";

export default function NotFound() {
  return (
    <Page title="Not found">
      <EmptyState icon="search" title="There's no page at this address" action={<Link className="btn" to="/">Back to Markets</Link>}>
        Try the search (⌘K / Ctrl-K) to jump to a ticker or page.
      </EmptyState>
    </Page>
  );
}

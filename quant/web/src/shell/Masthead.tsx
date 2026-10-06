/**
 * The masthead: the wordmark, the New York dateline with the session as the clock on
 * the wire states it, and the status cluster. A double rule closes it.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { formatDateline } from "./dateline";
import { sessionOf, useOps } from "./ops";
import { StatusCluster } from "./StatusCluster";

function useNow(ms: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

export function Masthead() {
  const now = useNow(15_000);
  const ops = useOps();
  return (
    <header className="oc-masthead">
      <Link to="/" className="oc-wordmark" aria-label="OhCamel Quant — front page">
        OHCAMEL<span className="oc-wordmark-slash">/</span>QUANT
      </Link>
      <p className="oc-dateline num">
        <time dateTime={now.toISOString()}>{formatDateline(now, sessionOf(ops.data))}</time>
      </p>
      <StatusCluster />
    </header>
  );
}

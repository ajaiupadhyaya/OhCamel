/**
 * The Compute page's one EventSource on GET /api/jobs/events (contract II.2). Calls `onEvent`
 * per parsed `event: job` and reports the link: LIVE when open, CONNECTING while the browser
 * reconnects (the server closes each stream after its lifetime; Last-Event-ID resumes), DOWN
 * when closed (reopened after a backoff). The page also polls, so DOWN costs freshness only.
 */
import { useEffect, useRef, useState } from "react";
import { parseJobEvent, type JobEvent } from "./model";

export type Link = "LIVE" | "CONNECTING" | "DOWN";

export function useJobEvents(onEvent: (e: JobEvent) => void): Link {
  const [link, setLink] = useState<Link>(() => (typeof EventSource === "undefined" ? "DOWN" : "CONNECTING"));
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let stopped = false;
    const open = () => {
      if (stopped) return;
      setLink("CONNECTING");
      es = new EventSource("/api/jobs/events");
      es.onopen = () => {
        attempt = 0;
        setLink("LIVE");
      };
      es.addEventListener("job", (m) => {
        const e = parseJobEvent((m as MessageEvent<string>).data);
        if (e) handler.current(e);
      });
      es.onerror = () => {
        if (!es) return;
        if (es.readyState === EventSource.CLOSED) {
          es.close();
          es = null;
          setLink("DOWN");
          timer = setTimeout(open, Math.min(60_000, 2_000 * 2 ** attempt++));
        } else setLink("CONNECTING");
      };
    };
    open();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      es?.close();
    };
  }, []);
  return link;
}

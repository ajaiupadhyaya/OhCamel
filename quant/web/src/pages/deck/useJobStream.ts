/**
 * The deck's one EventSource: GET /api/jobs/events (Lane B's job stream). Calls `onEvent` with
 * each parsed `event: job`, and reports the link: "connecting", "live" (open), "down" (closed,
 * reopening with backoff from live.ts). The server ends each stream after its lifetime; the
 * browser then reconnects by itself with Last-Event-ID, which shows here as a brief "connecting".
 * A browser without EventSource is simply "down", and the caller polls.
 */
import { useEffect, useRef, useState } from "react";
import { parseJobEvent, reconnectDelay, type JobEvent, type LinkState } from "./live";

const URL = "/api/jobs/events";

export function useJobStream(onEvent: (e: JobEvent) => void): LinkState {
  const [link, setLink] = useState<LinkState>(() => (typeof EventSource === "undefined" ? "down" : "connecting"));
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
      setLink("connecting");
      es = new EventSource(URL);
      es.onopen = () => {
        attempt = 0;
        setLink("live");
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
          setLink("down");
          timer = setTimeout(open, reconnectDelay(attempt++));
        } else {
          setLink("connecting");
        }
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

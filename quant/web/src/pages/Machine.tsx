/**
 * The Machine (/): OhCamel's real dependency graph, drawn as an instrument or written out as a list (spec section 6).
 * This page reads the graph and decides which: the list when the address asks for it, or when this browser cannot draw
 * (no WebGL, or a context that went and did not come back). The instrument is a lazy chunk of its own (machine/Instrument.tsx),
 * because it is the only thing here that needs three.js, and a browser that shows the list must not download it.
 *
 * Page-local code lives in ./machine/ (CSS prefix `mx-`).
 */
import { lazy, Suspense, useEffect, useMemo, useState, type ComponentType } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { InstrumentProps } from "./machine/Instrument";
import { ListView } from "./machine/ListView";
import type { Run } from "./machine/types";
import { listSearch, parseView } from "./machine/url";
import { useMachine } from "./machine/useMachine";
import { webglAvailable } from "./machine/webgl";
import { indexGraph } from "./machine/web";
import type { GraphIndex } from "./machine/web";
import "./machine/machine.css";

/** Stands in for an instrument that did not arrive: it draws nothing and says so, and the page shows the list. */
function NoInstrument({ onLost }: InstrumentProps) {
  useEffect(onLost, [onLost]);
  return null;
}

let loading: Promise<{ default: ComponentType<InstrumentProps> }> | undefined;

/**
 * The instrument's chunk, fetched once. It never rejects: a chunk that fails to arrive is the same as a browser that
 * cannot draw, and must not reach the page's error boundary, whose retry would only render the same failed import.
 */
function loadInstrument(): NonNullable<typeof loading> {
  loading ??= import("./machine/Instrument").catch((e: unknown) => {
    console.error(e);
    return { default: NoInstrument };
  });
  return loading;
}

const Instrument = lazy(loadInstrument);

/** How often the page's clock moves: readings and the day list say "the last day", and a sitting page must not go stale. */
const NOW_EVERY_MS = 30_000;
const NO_WEBGL = "This browser cannot draw the machine: WebGL is off or unavailable.";
const LOST = "The machine stopped drawing, so here it is as a list. Reload to try again.";

function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), NOW_EVERY_MS);
    return () => clearInterval(t);
  }, []);
  return now;
}

export default function Machine() {
  const { data, error, runs, retry } = useMachine();
  const gi = useMemo(() => (data ? indexGraph(data) : null), [data]);
  const { search: address } = useLocation();
  const [webgl] = useState(webglAvailable);
  useEffect(() => {
    document.title = "The machine · OhCamel Quant";
  }, []);

  // Three is the slowest part of the first paint and needs nothing from the read of the graph, so the two start together.
  const draws = webgl && !parseView(address, () => false).list;
  useEffect(() => {
    if (draws) void loadInstrument();
  }, [draws]);

  return (
    <div className="mx-root">
      {gi ? <Page gi={gi} runs={runs} address={address} webgl={webgl} /> : <h1 className="mx-vh">The machine</h1>}
      {!gi && !error && <p className="mx-wait">Reading the machine…</p>}
      {Boolean(error) && (
        <p className={gi ? "mx-wait mx-wait-low" : "mx-wait"} role="alert">
          {gi ? "The latest read of the machine failed, so this is the last one that worked: " : "The machine could not be read: "}
          {error instanceof Error ? error.message : String(error)}{" "}
          <button type="button" onClick={retry}>
            retry
          </button>
        </p>
      )}
    </div>
  );
}

function Page({ gi, runs, address, webgl }: { gi: GraphIndex; runs: Run[]; address: string; webgl: boolean }) {
  const nav = useNavigate();
  const view = useMemo(() => parseView(address, (id) => gi.byId.has(id)), [address, gi]);
  const now = useNow();
  const [lost, setLost] = useState(false);
  const drawable = webgl && !lost;

  if (!drawable || view.list) {
    return <ListView gi={gi} view={view} runs={runs} now={now} drawable={drawable} note={!webgl ? NO_WEBGL : lost ? LOST : null} onSelect={(id, how) => nav({ search: listSearch(view, id) }, how)} />;
  }
  return (
    <>
      <h1 className="mx-vh">The machine</h1>
      <Suspense fallback={<p className="mx-wait">Reading the machine…</p>}>
        <Instrument gi={gi} runs={runs} view={view} now={now} onLost={() => setLost(true)} />
      </Suspense>
    </>
  );
}

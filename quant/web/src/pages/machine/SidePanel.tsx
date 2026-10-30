/**
 * The right-hand panel (spec section 6.4): the Tour, the Reading of the selection, and The day with its replay. The
 * text is written from live data at render time by tour.ts and readings.ts and arrives here as segments.
 */
import { useEffect, useMemo, useRef, type CSSProperties, type RefObject } from "react";
import { dayRuns } from "./day";
import { dur, instant, num, nyTime, pct, plural } from "./fmt";
import { cpuTotal, fin } from "./prose";
import { reading } from "./readings";
import { Paras } from "./Segs";
import { STEPS } from "./tour";
import type { Run } from "./types";
import type { Tab, View } from "./url";
import { REDUCED_MOTION } from "./useMedia";
import type { GraphIndex } from "./web";

const TABS: [Tab, string][] = [["tour", "Tour"], ["read", "Reading"], ["day", "The day"]];
const HINT: Record<Tab, string> = { tour: "scroll ↓", read: "scroll to zoom", day: "" };
/** How long a scroll the page started may take; what the sheet reports until then is that scroll passing, not the visitor reading. */
const GLIDE_MS = 900;

export interface SidePanelProps {
  gi: GraphIndex;
  view: View;
  runs: Run[];
  now: Date;
  replay: { playing: boolean; held: string | null; clock: RefObject<HTMLSpanElement> };
  onTab(tab: Tab): void;
  onStep(step: number): void;
  onSelect(id: string): void;
  onReplay(): void;
  onStopReplay(): void;
  /** The panel is the phone's sheet: the tour is one long read, and the words are for a finger. */
  phone?: boolean;
}

export function SidePanel(p: SidePanelProps) {
  const { view, onTab } = p;
  return (
    <aside className="mx-panel mx-side" aria-label="The machine in words">
      <div className="mx-tabs" role="tablist">
        {TABS.map(([tab, word]) => (
          <button key={tab} type="button" role="tab" id={`mx-tab-${tab}`} aria-selected={view.tab === tab} aria-controls="mx-pane" className={view.tab === tab ? "on" : undefined} onClick={() => onTab(tab)}>
            {word}
          </button>
        ))}
        <span className="mx-hint">{HINT[view.tab]}</span>
      </div>
      <div className="mx-pane" id="mx-pane" role="tabpanel" aria-labelledby={`mx-tab-${view.tab}`}>
        {view.tab === "tour" && (p.phone ? <TourStack {...p} /> : <TourPane {...p} />)}
        {view.tab === "read" && <ReadingPane {...p} />}
        {view.tab === "day" && <DayPane {...p} />}
      </div>
    </aside>
  );
}

function TourPane({ gi, view, runs, now, onStep, onSelect }: SidePanelProps) {
  const has = (id: string) => gi.byId.has(id);
  const step = STEPS[view.step - 1];
  const ctx = { runs, now };
  return (
    <>
      <div className="mx-tstep">
        <span>
          {view.step} / {STEPS.length}
        </span>
        <span className="mx-dots">
          {STEPS.map((s, k) => (
            <button key={s.key} type="button" className={k + 1 === view.step ? "on" : undefined} aria-label={`Step ${k + 1}`} aria-current={k + 1 === view.step ? "step" : undefined} onClick={() => onStep(k + 1)} />
          ))}
        </span>
      </div>
      <div aria-live="polite">
        <h2>{step.title(gi, ctx)}</h2>
        <Paras paras={step.body(gi, ctx)} has={has} onSelect={onSelect} />
      </div>
      <div className="mx-tnav">
        <button type="button" disabled={view.step === 1} onClick={() => onStep(view.step - 1)}>
          ↑ back
        </button>
        <span>scroll or use the arrow keys</span>
        <button type="button" disabled={view.step === STEPS.length} onClick={() => onStep(view.step + 1)}>
          next ↓
        </button>
      </div>
    </>
  );
}

/**
 * The tour on a phone: the eight steps one after another, in one scroll. The step at the sheet's reading line is the
 * step, so reading on is what moves the machine, and back and next (or a key, or a deep link) scroll to the step they
 * name. A step's selection is not shown above it here: the step's own words are about it, and Reading has the rest.
 */
function TourStack({ gi, view, runs, now, onStep, onSelect }: SidePanelProps) {
  const has = (id: string) => gi.byId.has(id);
  const written = useMemo(() => {
    const ctx = { runs, now };
    return STEPS.map((s) => ({ key: s.key, title: s.title(gi, ctx), body: s.body(gi, ctx) }));
  }, [gi, runs, now]);
  const box = useRef<HTMLDivElement>(null);
  /** The step the sheet stands at; 0 until it has been placed. */
  const at = useRef(0);
  /** Steps the sheet has passed and the address has yet to catch up with: a fast scroll is ahead of the page's renders. */
  const passed = useRef(new Set<number>());
  /** Until this time the sheet is moving because the page moved it. */
  const glidingUntil = useRef(0);
  const report = useRef(onStep);
  useEffect(() => {
    report.current = onStep;
  });

  const { step } = view;
  useEffect(() => {
    if (step === at.current) {
      passed.current.clear();
      return;
    }
    if (passed.current.delete(step)) return;
    const smooth = at.current !== 0 && !window.matchMedia(REDUCED_MOTION).matches;
    at.current = step;
    if (smooth) glidingUntil.current = performance.now() + GLIDE_MS;
    box.current?.children[step - 1]?.scrollIntoView({ block: "start", behavior: smooth ? "smooth" : "auto" });
  }, [step]);

  useEffect(() => {
    const steps = box.current;
    if (!steps) return;
    const seen = new IntersectionObserver(
      (entries) => {
        if (performance.now() < glidingUntil.current) return;
        for (const e of entries) {
          const k = [...steps.children].indexOf(e.target) + 1;
          if (!e.isIntersecting || k === at.current) continue;
          at.current = k;
          passed.current.add(k);
          report.current(k);
        }
      },
      { root: steps.closest(".mx-panels"), rootMargin: "-40% 0px -59% 0px" }, // the reading line, two fifths down the sheet
    );
    for (const s of steps.children) seen.observe(s);
    return () => seen.disconnect();
  }, []);

  return (
    <>
      <div ref={box} className="mx-tstack">
        {written.map((s, k) => (
          <section key={s.key} className="mx-tsec" aria-current={k + 1 === step ? "step" : undefined}>
            <h2>{s.title}</h2>
            <Paras paras={s.body} has={has} onSelect={onSelect} />
          </section>
        ))}
      </div>
      <div className="mx-tnav">
        <button type="button" disabled={step === 1} onClick={() => onStep(step - 1)}>
          ↑ back
        </button>
        <span aria-live="polite">
          {step} / {STEPS.length}
        </span>
        <button type="button" disabled={step === STEPS.length} onClick={() => onStep(step + 1)}>
          next ↓
        </button>
      </div>
    </>
  );
}

function ReadingPane({ gi, view, runs, now, phone, onSelect }: SidePanelProps) {
  const i = view.sel === null ? undefined : gi.byId.get(view.sel);
  const read = useMemo(() => (i === undefined ? null : reading(gi, i, { runs, now })), [gi, i, runs, now]);
  if (!read) return <p className="mx-dim">{phone ? "Tap" : "Click"} anything in the machine, or search for it, to read what it is and what it did.</p>;
  return (
    <div aria-live="polite">
      <h2>{read.title}</h2>
      <Paras paras={read.paras} has={(id) => gi.byId.has(id)} onSelect={onSelect} />
      {read.caveats.length > 0 && (
        <>
          <h3 className="mx-h3">notes it writes about itself</h3>
          <ul className="mx-notes">
            {read.caveats.map((c, k) => (
              <li key={k}>{c}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** How long a run took, in words, when it has both ends. */
function took(r: Run): string | null {
  const a = instant(r.start), b = instant(r.end);
  return a === null || b === null ? null : dur((b - a) / 1000);
}

function DayPane({ gi, runs, now, replay, onSelect, onReplay, onStopReplay }: SidePanelProps) {
  const rows = useMemo(() => dayRuns(runs, now), [runs, now]);
  const cpu = cpuTotal(rows);
  const list = useRef<HTMLDivElement>(null);

  // the held run's row stays in sight while the sweep walks the list
  const { held } = replay;
  useEffect(() => {
    if (held) list.current?.querySelector(".on")?.scrollIntoView({ block: "nearest" });
  }, [held]);

  return (
    <>
      <div className="mx-play">
        <button type="button" disabled={!replay.playing && rows.length === 0} onClick={replay.playing ? onStopReplay : onReplay}>
          {replay.playing ? "■ stop" : "▶ replay the day"}
        </button>
        {replay.playing ? (
          <span className="mx-play-t" ref={replay.clock} />
        ) : (
          <span className="mx-play-t">{[plural(rows.length, "run"), cpu !== null && `${num(cpu)} cpu-s`].filter(Boolean).join(" · ")}</span>
        )}
      </div>
      {rows.length === 0 ? (
        <p className="mx-dim">No runs in the last day.</p>
      ) : (
        <div className="mx-runs" ref={list}>
          {rows.map((r) => (
            <RunRow key={r.id} run={r} gi={gi} on={r.id === replay.held} onSelect={onSelect} />
          ))}
        </div>
      )}
    </>
  );
}

function RunRow({ run: r, gi, on, onSelect }: { run: Run; gi: GraphIndex; on: boolean; onSelect(id: string): void }) {
  const id = `j:${r.kind}`;
  const known = gi.byId.get(id);
  const name = known === undefined ? r.kind : gi.out.nodes[known].n || gi.out.nodes[known].l;
  const progress = fin(r.progress);
  const running = r.state === "running";
  const tone = [on && "on", running && "running", r.state === "failed" && "fail"].filter(Boolean).join(" ");
  const cpu = fin(r.cpu);
  const last =
    running ? (progress === null ? "running" : pct(progress, 0)) :
    r.state === "queued" ? "queued" :
    r.state === "failed" ? "failed" :
    r.state === "cancelled" ? "cancelled" :
    cpu !== null ? `${num(cpu)} cpu-s` : "";
  return (
    <button type="button" className={`mx-run ${tone}`} disabled={known === undefined} onClick={() => onSelect(id)}>
      <span>{r.start ? nyTime(r.start) : ""}</span>
      <span className="mx-run-name">{name}</span>
      <span>{took(r) ?? ""}</span>
      <span>{last}</span>
      {running && progress !== null && (
        <i className="mx-run-bar" style={{ "--p": String(Math.min(1, Math.max(0, progress))) } as CSSProperties} aria-hidden="true" />
      )}
    </button>
  );
}

/**
 * Live Engine (/engine) -- the repository's original project, the OCaml real-time risk engine.
 *
 * SPEC: what the engine reads and computes. BENCH and GRAPH read one recording of the real
 * graph, GET /api/engine/propagation (`make propagation`), so no figure here is typed. Then the
 * engine's live state through the read-only bridge: GET /api/engine/vitals, which carries no
 * book data, and the book itself only where the deployment publishes it (503 → INSUFFICIENT
 * DATA, never stand-in numbers). The narrative lives in Methodology (#engine-incremental),
 * marked with a Note.
 */
import { Page, Section, StatGrid, StatTile, Panel } from "../components";
import { Note } from "../design";
import { fmtMultiple, fmtNum } from "../lib/format";
import { useApiQuery } from "../lib/query";
import { Kv, LIVE_HOST, LiveEngine } from "./engine/Live";
import { Propagation, Scaling } from "./engine/Propagation";
import { fmtNs } from "./engine/replay";
import type { Recording } from "./engine/types";
import "./engine/engine.css";

const REPO = "https://github.com/ajaiupadhyaya/OhCamel";
const OVERVIEW = `${REPO}/blob/main/docs/overview.md`;
const RECORDER = `${REPO}/blob/main/bench/propagation.ml`;

export default function Engine() {
  const recording = useApiQuery<Recording>("/engine/propagation", undefined, { staleTime: Infinity });
  const rec = recording.data;
  const largest = rec?.scaling[rec.scaling.length - 1];
  return (
    <Page
      eyebrow="OCAML 5 · INCREMENTAL"
      title="Live Engine"
      actions={
        <>
          <a className="btn btn-sm" href={OVERVIEW} target="_blank" rel="noreferrer">
            OVERVIEW
          </a>
          <a className="btn btn-sm btn-primary" href={LIVE_HOST} target="_blank" rel="noreferrer">
            LIVE DESK
          </a>
        </>
      }
    >
      <div className="grid-2">
        <Panel
          title={
            <>
              ENGINE · SPEC
              <Note n={1} to="engine-incremental" />
            </>
          }
        >
          <Kv
            rows={[
              ["LANGUAGE", "OCAML 5 · JANE STREET INCREMENTAL"],
              ["PRICES", "ALPACA IEX WEBSOCKET · REST BACKFILL"],
              ["FACTOR", "FRED DGS10 · DAILY CHANGE"],
              ["RISK", "HIST · PARAM · EWMA VAR/ES · EULER"],
              ["CHECKS", "VAR COVERAGE BACKTESTS · LIMITS"],
              ["BOOK", "EXAMPLE FILE · PRICES REAL"],
              ["PROCESS", "OHCAMEL-LIVE · 24/7 · GATED"],
            ]}
          />
        </Panel>
        <Panel<Recording> title="BENCH · ONE TICK" query={recording} skeletonHeight={120} notes={[]} provenance={[]}>
          {() =>
            largest ? (
              <>
                <StatGrid min={120}>
                  <StatTile size="md" label="RAN / TICK" value={fmtNum(largest.nodes_per_tick, 0)} caption={`OF ${fmtNum(largest.named_nodes, 0)} BODIES`} />
                  <StatTile size="md" label={`TICK · ${fmtNum(largest.instruments, 0)} NAMES`} value={fmtNs(largest.incremental_ns)} caption="INCREMENTAL" />
                  <StatTile size="md" label={`FULL · ${fmtNum(largest.instruments, 0)} NAMES`} value={fmtNs(largest.full_ns)} caption={`${fmtMultiple(largest.full_ns / largest.incremental_ns, 0)} A TICK`} />
                </StatGrid>
                <div className="oc-provenance num en-bench-src">
                  <span className="oc-provenance-label">SOURCE</span>
                  <a href={RECORDER} target="_blank" rel="noreferrer">
                    make propagation
                  </a>
                  <span>
                    · {rec!.sha.slice(0, 7)} · {rec!.machine.toUpperCase()}
                  </span>
                </div>
              </>
            ) : null
          }
        </Panel>
      </div>

      <Section
        title={
          <>
            GRAPH · WHAT RAN
            <Note n={2} to="engine-incremental" />
          </>
        }
      >
        <Panel<Recording>
          title={rec ? `RECORDED · ${rec.book.label.toUpperCase()} · CLOSES TO ${rec.book.as_of}` : "RECORDED"}
          query={recording}
          skeletonHeight={420}
        >
          {(d) => <Propagation rec={d} />}
        </Panel>
      </Section>

      {rec && (
        <Section title="COST · AS THE BOOK GROWS">
          <Scaling rec={rec} title="ONE TICK AGAINST RECOMPUTING EVERYTHING" />
        </Section>
      )}

      <Section title="ENGINE · NOW">
        <LiveEngine />
      </Section>
    </Page>
  );
}

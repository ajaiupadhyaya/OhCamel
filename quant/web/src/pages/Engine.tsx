/**
 * Live Engine (/engine) -- the repository's original project, the OCaml real-time risk engine.
 *
 * SPEC: what the engine reads and computes. BENCH: the repository's own benchmark, quoted from
 * docs/overview.md with its machine. GRAPH: an explanatory picture of a tick propagating through
 * the Incremental dependency graph (not live). Then the engine's live state via the read-only
 * bridge, GET /api/engine/{status,snapshot,history} (503 → INSUFFICIENT DATA, never stand-in
 * numbers). The narrative lives in Methodology (#engine-incremental), marked with a Note.
 */
import { Page, Section, StatGrid, StatTile, Panel } from "../components";
import { Note } from "../design";
import { GraphDiagram } from "./engine/GraphDiagram";
import { Kv, LIVE_HOST, LiveEngine } from "./engine/Live";
import "./engine/engine.css";

const REPO = "https://github.com/ajaiupadhyaya/OhCamel";
const OVERVIEW = `${REPO}/blob/main/docs/overview.md`;

export default function Engine() {
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
        <Panel title="BENCH · MAKE BENCH">
          <StatGrid min={120}>
            <StatTile size="md" label="NODES / TICK" value="≈25" caption="10 OR 400 NAMES" />
            <StatTile size="md" label="TICK · 400 NAMES" value="≈0.5 MS" caption="INCREMENTAL" />
            <StatTile size="md" label="FULL · 400 NAMES" value="53 MS" caption="RECOMPUTE ALL" />
          </StatGrid>
          <div className="oc-provenance num en-bench-src">
            <span className="oc-provenance-label">SOURCE</span>
            <a href={OVERVIEW} target="_blank" rel="noreferrer">
              docs/overview.md
            </a>
            <span>· M2 PRO · UNDATED · NOT THE DROPLET</span>
          </div>
        </Panel>
      </div>

      <Section
        title={
          <>
            GRAPH · ONE TICK
            <Note n={2} to="engine-incremental" />
          </>
        }
      >
        <GraphDiagram />
      </Section>

      <Section title="ENGINE · NOW">
        <LiveEngine />
      </Section>
    </Page>
  );
}

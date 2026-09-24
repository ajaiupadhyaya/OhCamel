/**
 * Live Engine (/engine) — the repository's original project, the OCaml real-time risk engine.
 *
 * Explains what it is and why an incremental dependency graph suits live risk (with an
 * interactive illustration of a tick propagating), then shows the engine's live state via
 * the read-only bridge: GET /api/engine/status, /snapshot, /history (503 → "bridge not
 * enabled" / "unreachable", never stand-in numbers).
 */
import { Formula, Page, Section } from "../components";
import { Icon } from "../components/Icon";
import { GraphDiagram } from "./engine/GraphDiagram";
import { LiveEngine } from "./engine/Live";
import "./engine/engine.css";

const LIVE_HOST = "https://live.ohcamel.ajaiupadhyaya.com";
const REPO = "https://github.com/ajaiupadhyaya/OhCamel";
const OVERVIEW = `${REPO}/blob/main/docs/overview.md`;

export default function Engine() {
  return (
    <Page
      eyebrow="The original OhCamel · OCaml"
      title="Live Engine"
      subtitle="A real-time risk and limits engine that treats a book's risk as a dependency graph: when one price ticks, only the numbers that depend on it are recomputed."
      actions={
        <>
          <a className="btn" href={OVERVIEW} target="_blank" rel="noreferrer">
            <Icon name="book" size={15} /> Read the overview
          </a>
          <a className="btn btn-primary" href={LIVE_HOST} target="_blank" rel="noreferrer">
            Live desk <Icon name="external" size={14} />
          </a>
        </>
      }
    >
      <div className="en-intro">
        <div className="en-intro-text">
          <p className="en-lede">
            Give it a book — positions, cash and a set of limits — and a market-data feed. It gives back what a risk desk watches: exposure by instrument and sector, value at risk and expected shortfall, beta to a macro factor, drawdown, and which limits are breached. The numbers stay current as prices move.
          </p>
          <p>
            The unusual part is <em>how</em> they stay current. Most risk systems poll: a timer fires and the whole book is recomputed. OhCamel builds the computation once as a graph with Jane Street's <a href="https://github.com/janestreet/incremental" target="_blank" rel="noreferrer">Incremental</a> library. A tick sets one input
            node; <code className="num">stabilize</code> then re-runs exactly the nodes downstream of it, in dependency order, and hands every observer one consistent snapshot.
          </p>
          <p className="subtle small">
            The repository's benchmark (<code className="num">make bench</code>, M2 Pro) puts a tick at about 25 recomputed nodes whether the book holds 10 names or 400 — roughly half a millisecond at 400 names, against 53 ms to recompute everything. Source:{" "}
            <a href={OVERVIEW} target="_blank" rel="noreferrer">
              docs/overview.md
            </a>
            .
          </p>
        </div>
        <dl className="en-facts">
          <dt>Language</dt>
          <dd>OCaml 5, Jane Street Incremental</dd>
          <dt>Prices</dt>
          <dd>Alpaca real-time websocket (IEX), REST backfill</dd>
          <dt>Macro factor</dt>
          <dd>FRED DGS10 daily change</dd>
          <dt>Risk</dt>
          <dd>Historical, parametric & EWMA VaR/ES; Euler attribution; coverage backtests</dd>
          <dt>Book</dt>
          <dd>An example book from a file (or an Alpaca paper account) — the prices are real, the portfolio is illustrative</dd>
          <dt>Runs as</dt>
          <dd>
            <code className="num">ohcamel-live</code>, 24/7, behind a password
          </dd>
        </dl>
      </div>

      <Section title="How a tick moves through the graph" description="Pick an input to change. Highlighted nodes are recomputed, in the order shown; grey ones keep their previous value because nothing they depend on changed.">
        <div className="oc-panel en-graph-panel">
          <GraphDiagram />
        </div>
        <div className="grid-3">
          <div className="en-card">
            <div className="en-mini">Cost follows the change, not the book</div>
            <p className="small">A tick in one name touches that name's chain and the book-level aggregates. The other names' returns windows, marks and exposures are reused, so latency stays flat as the book grows.</p>
          </div>
          <div className="en-card">
            <div className="en-mini">One consistent snapshot</div>
            <p className="small">Observers only read after <code className="num">stabilize</code> finishes, so VaR, exposures and limits always describe the same instant — never a half-updated book.</p>
          </div>
          <div className="en-card">
            <div className="en-mini">Cut-offs stop useless work</div>
            <p className="small">If a recomputed node's value is unchanged, propagation stops there. Self-adjusting computation (Acar 2005) is the theory; Incremental is Jane Street's production implementation.</p>
          </div>
        </div>
        <div className="en-formula">
          <span className="subtle small">The quantities at the end of the graph, e.g. parametric VaR and its Euler split:</span>
          <Formula tex="\text{VaR}_\alpha = z_\alpha \sqrt{w^\top \Sigma w}, \qquad \text{CVaR}_i = w_i \frac{(\Sigma w)_i}{\sqrt{w^\top \Sigma w}}\, z_\alpha, \qquad \sum_i \text{CVaR}_i = \text{VaR}_\alpha" />
        </div>
      </Section>

      <Section title="The engine right now" description="Read through a narrow, GET-only bridge from this app's server: four fixed engine paths, short timeouts, a 2-second cache. No order or desk route can be reached through it.">
        <LiveEngine />
      </Section>

      <Section title="Further reading">
        <ul className="en-refs small">
          <li>
            <a href={OVERVIEW} target="_blank" rel="noreferrer">
              docs/overview.md
            </a>{" "}
            — what the engine is, what is real and where its edges are; <a href={`${REPO}/blob/main/docs/engine.md`} target="_blank" rel="noreferrer">docs/engine.md</a> argues the design at length.
          </li>
          <li>Minsky, Y. et al. (2015), <em>Incremental: a library for incremental computations</em>, Jane Street.</li>
          <li>Acar, U. A. (2005), <em>Self-Adjusting Computation</em>, PhD thesis, Carnegie Mellon University.</li>
          <li>Tasche, D. (2000), “Risk contributions and performance measurement”, TU München working paper — the Euler allocation.</li>
        </ul>
      </Section>
    </Page>
  );
}

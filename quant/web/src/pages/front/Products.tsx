/**
 * The product cells, each reading its job kind's latest artifact (compute plan Lane M):
 * REGIME (P6, EXP-Q02), RISK ATLAS (P1), STRATEGY FARM (P4), MODELS (P5, EXP-Q01).
 * Lane P2 (P2-8) wires the tables; until then a present artifact shows its headline.
 */
import { KINDS, type Manifest } from "../../lib/artifacts";
import { fmtNum } from "../../lib/format";
import { ArtifactCell, Headline } from "../../components";

export function RegimeCell() {
  return <ArtifactCell title="REGIME · HMM" kind={KINDS.regimes} experiment="EXP-Q02" to="/macro?tab=regimes" go="RATES" />;
}

export function AtlasCell() {
  return <ArtifactCell title="RISK ATLAS · VAR/ES 99 · 1D" kind={KINDS.atlas} to="/risk" go="RISK" />;
}

export function ModelsCell() {
  return <ArtifactCell title="MODELS · RANK-IC" kind={KINDS.models} experiment="EXP-Q01" to="/research?tab=models" go="RSCH" />;
}

/** Failures first and largest: most strategies fail, and the page says so. */
export function FarmCell() {
  return (
    <ArtifactCell title="STRATEGY FARM" kind={KINDS.farm} to="/research?tab=farm" go="RSCH">
      {(m: Manifest) => {
        const fail = m.headline?.fail;
        const pass = m.headline?.pass;
        if (typeof fail !== "number" || typeof pass !== "number") return <Headline m={m} />;
        return (
          <>
            <div className="fp-farm">
              <span className="fp-farm-fail num">{fmtNum(fail, 0)}</span>
              <span className="fp-farm-word">FAIL</span>
              <span className="fp-farm-pass num">{fmtNum(pass, 0)}</span>
              <span className="fp-farm-word fp-dim">PASS</span>
            </div>
            <Headline m={m} omit={["fail", "pass"]} />
          </>
        );
      }}
    </ArtifactCell>
  );
}

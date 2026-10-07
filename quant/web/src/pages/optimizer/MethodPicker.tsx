/**
 * Method picker: the ten methods from GET /api/portfolio/methods as one ruled radio table
 * (code, name, family, and what each needs: expected returns, a risk-free rate, whether it
 * honours constraints), then the selected method's objective and its own parameters.
 * Descriptions live in Methodology, reached through the Note marker.
 */
import type { ReactNode } from "react";
import { Formula, NumberField, SegmentedControl, Select, Slider } from "../../components";
import { Note } from "../../design";
import { fmtNum, fmtPct } from "../../lib/format";
import type { MvTarget, OptConfig } from "./config";
import { METHOD_CODE, METHOD_DOC, METHOD_FAMILY, METHOD_FORMULA } from "./info";
import { sym } from "./shared";
import type { LinkageName, MethodName, MethodSpec } from "./types";

type Patch = (p: Partial<OptConfig>) => void;

export function MethodPicker({ methods, cfg, set, rfMissing }: { methods: MethodSpec[]; cfg: OptConfig; set: Patch; rfMissing: boolean }) {
  return (
    <div className="op-methods">
      <div className="op-method op-method-headrow" aria-hidden>
        <span>CODE</span>
        <span>METHOD</span>
        <span className="op-method-family">FAMILY</span>
        <span className="op-method-flag op-sym">μ</span>
        <span className="op-method-flag">RF</span>
        <span className="op-method-flag">BOUNDS</span>
      </div>
      <div role="radiogroup" aria-label="Allocation method">
        {methods.map((m) => {
          const active = m.name === cfg.method;
          return (
            <button key={m.name} type="button" role="radio" aria-checked={active} className={`op-method ${active ? "active" : ""}`} onClick={() => set({ method: m.name })}>
              <span className="op-method-code num">{METHOD_CODE[m.name]}</span>
              <span className="op-method-label">{m.label}</span>
              <span className="op-method-family">{METHOD_FAMILY[m.name]}</span>
              <span className="op-method-flag num" title={m.needs_expected_returns ? "Uses expected returns" : undefined}>
                {m.needs_expected_returns ? "μ" : "·"}
              </span>
              <span className={`op-method-flag num ${m.needs_risk_free && rfMissing ? "loss" : ""}`} title={m.needs_risk_free ? (rfMissing ? "Risk-free series unavailable" : "Needs a risk-free rate") : undefined}>
                {m.needs_risk_free ? "RF" : "·"}
              </span>
              <span className="op-method-flag num" title={m.honours_constraints ? "Honours min/max and turnover" : "Heuristic: ignores min/max and turnover"}>
                {m.honours_constraints ? "YES" : "NO"}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function methodInfo(m: MethodSpec | undefined) {
  if (!m) return undefined;
  return { title: m.label, text: m.description.replace(/--/g, "–"), formula: METHOD_FORMULA[m.name], reference: m.reference };
}

/** The selected method's objective in TeX, with its Methodology note. */
export function MethodHeader({ spec }: { spec: MethodSpec | undefined }) {
  if (!spec) return null;
  return (
    <div className="op-method-head">
      <span className="op-method-head-k">OBJECTIVE</span>
      <span className="op-method-head-formula">
        <Formula tex={METHOD_FORMULA[spec.name]} />
      </span>
      <Note n={1} to={METHOD_DOC[spec.name]} />
    </div>
  );
}

const MV_OPTS: { value: MvTarget; label: ReactNode }[] = [
  { value: "target_vol", label: "VOL" },
  { value: "target_return", label: "RETURN" },
  { value: "risk_aversion", label: sym("γ") },
];

/** Parameters specific to the selected method (null when it has none). */
export function MethodParams({ cfg, set }: { cfg: OptConfig; set: Patch }) {
  const m: MethodName = cfg.method;
  if (m === "mean_variance") {
    const v = cfg.mvValue[cfg.mvTarget];
    const setV = (x: number) => set({ mvValue: { ...cfg.mvValue, [cfg.mvTarget]: x } });
    return (
      <div className="op-params">
        <div className="oc-field">
          <span className="oc-field-label">TARGET</span>
          <SegmentedControl size="sm" ariaLabel="Mean-variance target" options={MV_OPTS} value={cfg.mvTarget} onChange={(mvTarget) => set({ mvTarget })} />
        </div>
        {cfg.mvTarget === "risk_aversion" ? (
          <NumberField label={sym("γ")} ariaLabel="Risk aversion" value={v} min={0.1} max={1000} step={0.5} width={100} onChange={setV} />
        ) : (
          <NumberField
            label={cfg.mvTarget === "target_vol" ? "VOL ≤ · ANN" : "RET ≥ · ANN"}
            ariaLabel={cfg.mvTarget === "target_vol" ? "Target annual volatility" : "Target annual return"}
            value={v}
            percent
            min={cfg.mvTarget === "target_vol" ? 0.005 : -1}
            max={5}
            step={0.01}
            width={110}
            onChange={setV}
          />
        )}
      </div>
    );
  }
  if (m === "hrp" || m === "herc") {
    const def: LinkageName = m === "hrp" ? "single" : "ward";
    return (
      <div className="op-params">
        <div className="oc-field">
          <span className="oc-field-label">LINKAGE</span>
          <Select<LinkageName | "default">
            ariaLabel="Linkage"
            value={cfg.linkage ?? "default"}
            onChange={(v) => set({ linkage: v === "default" ? null : v })}
            options={[{ value: "default", label: `DEFAULT · ${def.toUpperCase()}` }, ...(["single", "ward", "average", "complete"] as LinkageName[]).map((l) => ({ value: l, label: l.toUpperCase() }))]}
          />
        </div>
        {m === "herc" && (
          <NumberField label="CLUSTERS" ariaLabel="Clusters" value={cfg.nClusters} min={1} max={60} step={1} width={90} hint="BLANK · LARGEST GAP" onChange={(v) => set({ nClusters: Math.round(v) || null })} />
        )}
      </div>
    );
  }
  if (m === "min_cvar") {
    return (
      <div className="op-params op-params-wide">
        <Slider label={sym("CVAR α")} value={cfg.cvarAlpha} min={0.8} max={0.99} step={0.01} format={(v) => `${fmtPct(v, 0)} · TAIL ${fmtPct(1 - v, 0)} · ${fmtNum(1 / (1 - v), 0)}D IN 1`} onChange={(cvarAlpha) => set({ cvarAlpha })} />
      </div>
    );
  }
  return null;
}

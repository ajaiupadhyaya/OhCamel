/**
 * Method picker — one card per method from GET /api/portfolio/methods (label, description,
 * reference), grouped by family, plus the selected method's own parameters.
 */
import {
  Formula,
  InfoTip,
  NumberField,
  SegmentedControl,
  Select,
  Slider,
} from "../../components";
import { fmtPct } from "../../lib/format";
import type { MvTarget, OptConfig } from "./config";
import { LINKAGE_INFO, METHOD_FAMILY, METHOD_FORMULA } from "./info";
import type { LinkageName, MethodName, MethodSpec } from "./types";

type Patch = (p: Partial<OptConfig>) => void;

const tidy = (s: string) =>
  s
    .replace(/--/g, "–")
    .replace(/\bmu'w\b/g, "μ'w")
    .replace(/\bsigma_i\b/g, "σᵢ")
    .replace(/\bSigma\b/g, "Σ")
    .replace(/\bsigma\b/g, "σ")
    .replace(/\bkappa\b/g, "κ");

export function MethodPicker({
  methods,
  cfg,
  set,
  rfMissing,
}: {
  methods: MethodSpec[];
  cfg: OptConfig;
  set: Patch;
  rfMissing: boolean;
}) {
  return (
    <div
      className="op-methods"
      role="radiogroup"
      aria-label="Allocation method"
    >
      {methods.map((m) => {
        const active = m.name === cfg.method;
        return (
          <button
            key={m.name}
            type="button"
            role="radio"
            aria-checked={active}
            className={`op-method ${active ? "active" : ""}`}
            onClick={() => set({ method: m.name })}
          >
            <span className="op-method-family eyebrow">
              {METHOD_FAMILY[m.name]}
            </span>
            <span className="op-method-label">{m.label}</span>
            <span className="op-method-desc">{tidy(m.description)}</span>
            <span className="op-method-badges">
              {m.needs_expected_returns && (
                <span
                  className="badge"
                  title="Uses expected returns — the noisiest input"
                >
                  uses μ
                </span>
              )}
              {m.needs_risk_free && (
                <span
                  className={`badge ${rfMissing ? "unknown" : ""}`}
                  title={
                    rfMissing
                      ? "The market risk-free series is unavailable right now — set your own rate in the rail"
                      : "Needs a risk-free rate"
                  }
                >
                  needs r<sub>f</sub>
                </span>
              )}
              {!m.honours_constraints && (
                <span
                  className="badge"
                  title="Heuristic: ignores min/max/turnover constraints"
                >
                  heuristic
                </span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function methodInfo(m: MethodSpec | undefined) {
  if (!m) return undefined;
  return {
    title: m.label,
    text: tidy(m.description),
    formula: METHOD_FORMULA[m.name],
    reference: m.reference,
  };
}

export function MethodHeader({ spec }: { spec: MethodSpec | undefined }) {
  if (!spec) return null;
  return (
    <div className="op-method-head">
      <div className="op-method-head-formula">
        <Formula tex={METHOD_FORMULA[spec.name]} />
      </div>
      <div className="small subtle op-method-ref">{spec.reference}</div>
    </div>
  );
}

const MV_OPTS: { value: MvTarget; label: string }[] = [
  { value: "target_vol", label: "Target vol" },
  { value: "target_return", label: "Target return" },
  { value: "risk_aversion", label: "Risk aversion γ" },
];

/** Parameters specific to the selected method (null when it has none). */
export function MethodParams({ cfg, set }: { cfg: OptConfig; set: Patch }) {
  const m: MethodName = cfg.method;
  if (m === "mean_variance") {
    const v = cfg.mvValue[cfg.mvTarget];
    const setV = (x: number) =>
      set({ mvValue: { ...cfg.mvValue, [cfg.mvTarget]: x } });
    return (
      <div className="op-params">
        <SegmentedControl
          size="sm"
          ariaLabel="Mean-variance target"
          options={MV_OPTS}
          value={cfg.mvTarget}
          onChange={(mvTarget) => set({ mvTarget })}
        />
        {cfg.mvTarget === "risk_aversion" ? (
          <NumberField
            label="γ"
            value={v}
            min={0.1}
            max={1000}
            step={0.5}
            width={100}
            onChange={setV}
            info={{
              title: "Risk aversion γ",
              text: "How much expected return you would give up per unit of variance. Higher γ → a safer portfolio.",
              formula:
                "\\max_w\\; \\mu^\\top w - \\tfrac{\\gamma}{2} w^\\top\\Sigma w",
            }}
          />
        ) : (
          <NumberField
            label={
              cfg.mvTarget === "target_vol"
                ? "Annual volatility"
                : "Annual return"
            }
            value={v}
            percent
            min={cfg.mvTarget === "target_vol" ? 0.005 : -1}
            max={cfg.mvTarget === "target_vol" ? 5 : 5}
            step={0.01}
            width={110}
            onChange={setV}
            info={
              cfg.mvTarget === "target_vol"
                ? {
                    text: "Maximise expected return subject to ex-ante volatility at or below this level.",
                  }
                : {
                    text: "Minimise variance subject to expected return at or above this level.",
                  }
            }
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
          <span className="oc-field-label">
            Tree linkage
            <InfoTip
              size={12}
              info={{
                title: "Linkage",
                text: Object.values(LINKAGE_INFO).join(" "),
              }}
            />
          </span>
          <Select<LinkageName | "default">
            ariaLabel="Linkage"
            value={cfg.linkage ?? "default"}
            onChange={(v) => set({ linkage: v === "default" ? null : v })}
            options={[
              { value: "default", label: `Default (${def})` },
              ...(
                ["single", "ward", "average", "complete"] as LinkageName[]
              ).map((l) => ({
                value: l,
                label: l[0].toUpperCase() + l.slice(1),
              })),
            ]}
          />
        </div>
        {m === "herc" && (
          <NumberField
            label="Clusters"
            value={cfg.nClusters}
            min={1}
            max={60}
            step={1}
            width={90}
            hint="blank = largest gap"
            onChange={(v) => set({ nClusters: Math.round(v) || null })}
            info={{
              text: "How many clusters HERC splits risk across. Left blank, it cuts the tree at the largest jump in merge height.",
            }}
          />
        )}
      </div>
    );
  }
  if (m === "min_cvar") {
    return (
      <div className="op-params op-params-wide">
        <Slider
          label="CVaR confidence α"
          value={cfg.cvarAlpha}
          min={0.8}
          max={0.99}
          step={0.01}
          format={(v) => `${fmtPct(v, 0)} · worst ${fmtPct(1 - v, 0)} of days`}
          onChange={(cvarAlpha) => set({ cvarAlpha })}
          info={{
            text: "Minimise the average loss over the worst (1 − α) share of historical days.",
          }}
        />
      </div>
    );
  }
  return null;
}

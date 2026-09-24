/**
 * "Overfitting guardrails" — the editorial explainer of why a single great backtest proves
 * little, and what PBO, DSR, SPA and walk-forward each guard against. When a sweep has run,
 * its real numbers are quoted.
 */
import { useState } from "react";
import { Formula, InfoTip } from "../../components";
import { Icon } from "../../components/Icon";
import { fmtPct } from "../../lib/format";
import { useLocalStorage } from "../../lib/hooks";
import { INFO } from "./info";
import { finite, fmtProb, fmtSR } from "./shared";
import type { SweepOut } from "./types";

export function Guardrails({
  sweep,
  onOpen,
}: {
  sweep?: SweepOut;
  onOpen: (tab: "sweep" | "walkforward") => void;
}) {
  const [open, setOpen] = useLocalStorage("ohcamel.research.guardrails", true);
  const [more, setMore] = useState(false);
  const n = sweep?.deflated_sharpe.n_trials;
  return (
    <section
      className={`sl-guard ${open ? "" : "closed"}`}
      aria-label="Overfitting guardrails"
    >
      <header className="sl-guard-head">
        <div>
          <div className="eyebrow">Overfitting guardrails</div>
          <h2 className="sl-guard-title display">
            Try enough variations and one of them will look brilliant.
          </h2>
        </div>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          <Icon name={open ? "chevron-up" : "chevron-down"} size={14} />{" "}
          {open ? "Hide" : "Why backtests lie"}
        </button>
      </header>
      {open && (
        <>
          <p className="sl-guard-lede">
            A backtest is one draw from history. Every parameter you tweak or
            universe you swap is another draw, and the best of many draws is
            biased upwards even when none has an edge — Bailey & López de Prado
            show a few dozen tries on a few years of data can manufacture an
            impressive Sharpe from pure noise. These four checks measure how
            much survives the search.
            {sweep && finite(sweep.deflated_sharpe.sr0_annualized) && (
              <>
                {" "}
                <strong>
                  In your last sweep, {n} combinations would be expected to
                  produce a best Sharpe of about{" "}
                  <span className="num">
                    {fmtSR(sweep.deflated_sharpe.sr0_annualized)}
                  </span>{" "}
                  by luck alone.
                </strong>
              </>
            )}
          </p>
          <div className="sl-guard-grid">
            <Card
              n="01"
              title="Probability of backtest overfitting"
              info={INFO.pbo}
              value={
                sweep && finite(sweep.pbo.pbo)
                  ? `PBO ${fmtPct(sweep.pbo.pbo, 0)}`
                  : undefined
              }
              onClick={() => onOpen("sweep")}
            >
              Pick the best parameters on half the history, rank them on the
              other half — every possible way.
              <span className="sl-guard-rule">Aim for PBO well below 50%.</span>
            </Card>
            <Card
              n="02"
              title="Deflated Sharpe ratio"
              info={INFO.dsr}
              value={
                sweep && finite(sweep.deflated_sharpe.dsr)
                  ? `DSR ${fmtProb(sweep.deflated_sharpe.dsr)}`
                  : undefined
              }
              onClick={() => onOpen("sweep")}
            >
              Is the winner better than the luckiest of N strategies with no
              skill at all?
              <span className="sl-guard-rule">Aim for DSR above 95%.</span>
            </Card>
            <Card
              n="03"
              title="Superior predictive ability"
              info={INFO.spa}
              value={
                sweep && finite(sweep.spa.pvalue_consistent)
                  ? `SPA p ${sweep.spa.pvalue_consistent!.toFixed(2)}`
                  : undefined
              }
              onClick={() => onOpen("sweep")}
            >
              Does any variant beat buy-and-hold by more than the search itself
              would produce?
              <span className="sl-guard-rule">Aim for p below 0.05.</span>
            </Card>
            <Card
              n="04"
              title="Walk-forward"
              info={INFO.walk_forward}
              onClick={() => onOpen("walkforward")}
            >
              Choose with past data only, trade the next block blind, repeat: a
              record without hindsight.
              <span className="sl-guard-rule">
                Compare with the in-sample Sharpe.
              </span>
            </Card>
          </div>
          <button
            type="button"
            className="sl-guard-more"
            onClick={() => setMore(!more)}
            aria-expanded={more}
          >
            {more
              ? "Hide the maths"
              : "The maths: how lucky can the best of N be?"}
          </button>
          {more && (
            <div className="sl-guard-math">
              <div>
                <p className="small">
                  If N strategies have no skill, their estimated Sharpe ratios
                  scatter around zero with variance V. The expected maximum (the{" "}
                  <em>False Strategy Theorem</em>) is approximately
                </p>
                <Formula tex={INFO.sr0.formula} />
                <p className="small subtle">
                  γ ≈ 0.5772 is the Euler–Mascheroni constant. Bailey & López de
                  Prado (2014), JPM 40(5); Bailey, Borwein, López de Prado & Zhu
                  (2014), Notices of the AMS 61(5), “Pseudo-mathematics and
                  financial charlatanism”.
                </p>
              </div>
              <div>
                <p className="small">
                  The deflated Sharpe ratio is the probabilistic Sharpe ratio
                  evaluated at that bar instead of at zero:
                </p>
                <Formula
                  tex={
                    "DSR = \\Phi\\!\\left(\\frac{(\\widehat{SR} - SR_0)\\sqrt{T-1}}{\\sqrt{1 - \\gamma_3 \\widehat{SR} + \\frac{\\gamma_4 - 1}{4}\\widehat{SR}^2}}\\right)"
                  }
                />
                <p className="small subtle">
                  Per-period Sharpe ratios; γ₃ skewness and γ₄ kurtosis of the
                  selected strategy’s returns; T the number of observations.
                </p>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function Card({
  n,
  title,
  info,
  value,
  children,
  onClick,
}: {
  n: string;
  title: string;
  info: any;
  value?: string;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <div className="sl-guard-card">
      <div className="sl-guard-card-top">
        <span className="num subtle">{n}</span>
        {value && <span className="badge accent num">{value}</span>}
      </div>
      <div className="sl-guard-card-title">
        {title}
        <InfoTip info={info} size={12} />
      </div>
      <p className="small">{children}</p>
      <button type="button" className="sl-guard-link small" onClick={onClick}>
        Run it <Icon name="arrow-right" size={12} />
      </button>
    </div>
  );
}

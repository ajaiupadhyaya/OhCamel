/**
 * First-visit orientation strip: three suggested starting points, dismissible. Dismissal is
 * remembered per browser in localStorage ("ohcamel.startHere.dismissed").
 *
 *   <StartHere />            // on the landing page (Markets), above the first section
 */
import { Link } from "react-router-dom";
import { useLocalStorage } from "../lib/hooks";
import { Icon, type IconName } from "./Icon";

const KEY = "ohcamel.startHere.dismissed";

const STEPS: { n: number; to: string; icon: IconName; title: string; text: string; cta: string }[] = [
  { n: 1, to: "/portfolio", icon: "portfolio", title: "Build a portfolio", text: "Pick holdings (or import a 13F) and see its return, risk, factor exposures and stress losses.", cta: "Portfolio Lab" },
  { n: 2, to: "/research", icon: "research", title: "Test a published strategy", text: "Backtest a strategy from the literature and check whether the result survives costs and overfitting tests.", cta: "Strategy Lab" },
  { n: 3, to: "/macro", icon: "macro", title: "Read the market", text: "The yield curve, recession odds and policy rules — what rates are saying about the economy.", cta: "Rates & Macro" },
];

export function StartHere() {
  const [dismissed, setDismissed] = useLocalStorage<boolean>(KEY, false);
  if (dismissed) return null;
  return (
    <section className="oc-starthere" aria-labelledby="oc-starthere-title">
      <div className="oc-starthere-head">
        <h2 id="oc-starthere-title" className="oc-starthere-title">
          <span className="eyebrow">Start here</span> Three things to try
        </h2>
        <button type="button" className="icon-btn oc-starthere-close" aria-label="Dismiss the start-here guide" title="Dismiss" onClick={() => setDismissed(true)}>
          <Icon name="x" size={16} />
        </button>
      </div>
      <ol className="oc-starthere-steps">
        {STEPS.map((s) => (
          <li key={s.n}>
            <Link to={s.to} className="oc-starthere-step">
              <span className="oc-starthere-icon" aria-hidden>
                <Icon name={s.icon} size={18} />
              </span>
              <span className="oc-starthere-body">
                <span className="oc-starthere-step-title">
                  <span className="num subtle">{s.n}.</span> {s.title}
                </span>
                <span className="oc-starthere-text">{s.text}</span>
                <span className="oc-starthere-cta">
                  {s.cta} <Icon name="arrow-right" size={13} />
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ol>
    </section>
  );
}

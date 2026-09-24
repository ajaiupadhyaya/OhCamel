/**
 * Scaffolding shown by pages that have not been built yet. Page authors: delete the
 * <PagePlaceholder/> from your page file and build the real thing.
 */
import type { ReactNode } from "react";
import { Icon } from "./Icon";

export function PagePlaceholder({ plan, endpoints }: { plan: ReactNode[]; endpoints?: string[] }) {
  return (
    <div className="oc-placeholder">
      <div className="eyebrow">In the works</div>
      <ol className="oc-placeholder-plan">
        {plan.map((p, i) => (
          <li key={i}>
            <span className="oc-placeholder-n num">{String(i + 1).padStart(2, "0")}</span>
            <span>{p}</span>
          </li>
        ))}
      </ol>
      {endpoints && endpoints.length > 0 && (
        <div className="oc-placeholder-endpoints">
          <Icon name="database" size={14} />
          {endpoints.map((e) => (
            <code key={e} className="num">{e}</code>
          ))}
        </div>
      )}
    </div>
  );
}

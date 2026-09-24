/**
 * Page frame: display-serif title, optional eyebrow, subtitle and right-aligned actions.
 * Also sets document.title.
 *
 *   <Page title="Portfolio Lab" subtitle="…" actions={<button className="btn">Share</button>}>
 *     …panels…
 *   </Page>
 */
import { useEffect, type ReactNode } from "react";

export function Page({ title, eyebrow, subtitle, actions, children, meta, docTitle }: { title: ReactNode; eyebrow?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; meta?: ReactNode; children?: ReactNode; docTitle?: string }) {
  useEffect(() => {
    const t = docTitle ?? (typeof title === "string" ? title : undefined);
    document.title = t ? `${t} · OhCamel Quant` : "OhCamel Quant";
  }, [title, docTitle]);
  return (
    <div className="oc-page">
      <header className="oc-page-head">
        <div className="oc-page-titles">
          {eyebrow && <div className="eyebrow oc-page-eyebrow">{eyebrow}</div>}
          <h1 className="oc-page-title display">{title}</h1>
          {subtitle && <p className="oc-page-subtitle">{subtitle}</p>}
          {meta && <div className="oc-page-meta">{meta}</div>}
        </div>
        {actions && <div className="oc-page-actions">{actions}</div>}
      </header>
      <div className="oc-page-body">{children}</div>
    </div>
  );
}

/**
 * A titled group of panels inside a page, with a hairline rule. `actions` wrap onto
 * further lines (and never widen the page) when space is short.
 */
export function Section({ title, description, actions, children, id }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section className="oc-section" id={id}>
      <div className="oc-section-head">
        <div className="oc-section-titles">
          <h2 className="oc-section-title display">{title}</h2>
          {description && <p className="oc-section-desc">{description}</p>}
        </div>
        {actions && <div className="row-wrap oc-section-actions">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

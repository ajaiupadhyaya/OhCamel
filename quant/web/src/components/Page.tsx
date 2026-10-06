/**
 * The Sheet: route code, Archivo title over a heavy rule, then mono meta chips.
 * Also sets document.title.
 *
 *   <Page title="Portfolio Lab" meta={<span>BOOK 60/40</span>} actions={<button className="btn">Share</button>}>
 *     …panels…
 *   </Page>
 *
 * The code line shows the route's function code (from lib/routes), then the eyebrow.
 * `subtitle` is accepted for compatibility and not rendered: labels, not sentences.
 */
import { useEffect, type ReactNode } from "react";
import { matchPath, useLocation } from "react-router-dom";
import { ROUTES } from "../lib/routes";

function routeCode(pathname: string): string | undefined {
  return ROUTES.find((r) => matchPath({ path: r.path, end: true }, pathname))?.code;
}

export function Page({ title, eyebrow, actions, children, meta, docTitle }: { title: ReactNode; eyebrow?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; meta?: ReactNode; children?: ReactNode; docTitle?: string }) {
  const { pathname } = useLocation();
  const code = routeCode(pathname);
  useEffect(() => {
    const t = docTitle ?? (typeof title === "string" ? title : undefined);
    document.title = t ? `${t} · OhCamel Quant` : "OhCamel Quant";
  }, [title, docTitle]);
  return (
    <div className="oc-page">
      <header className="oc-page-head">
        <div className="oc-page-titles">
          {(code || eyebrow) && (
            <div className="oc-page-code">
              {code && <span className="oc-page-code-fn">{code}</span>}
              {eyebrow && <span className="oc-page-eyebrow">{eyebrow}</span>}
            </div>
          )}
          <h1 className="oc-page-title">{title}</h1>
        </div>
        {actions && <div className="oc-page-actions">{actions}</div>}
      </header>
      {meta && <div className="oc-page-meta">{meta}</div>}
      <div className="oc-page-body">{children}</div>
    </div>
  );
}

/**
 * A titled group of panels: Archivo caps heading under a heavy rule. `actions` wrap onto
 * further lines (and never widen the page) when space is short. `description` is accepted
 * for compatibility and not rendered.
 */
export function Section({ title, actions, children, id }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section className="oc-section" id={id}>
      <div className="oc-section-head">
        <h2 className="oc-section-title">{title}</h2>
        {actions && <div className="row-wrap oc-section-actions">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

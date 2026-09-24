/**
 * KaTeX formula. KaTeX (+ its CSS and fonts) is loaded lazily on first use.
 *   <Formula tex="SR = \frac{\mu - r_f}{\sigma}" />            display mode
 *   <Formula tex="\beta" inline />
 * In JSX attributes backslashes are literal; in JS strings escape them ("\\frac").
 */
import { useEffect, useState } from "react";

type Katex = typeof import("katex");
let katexPromise: Promise<Katex> | null = null;
function loadKatex(): Promise<Katex> {
  if (!katexPromise) {
    katexPromise = Promise.all([import("katex"), import("katex/dist/katex.min.css")]).then(([m]) => ((m as any).default ?? m) as Katex);
  }
  return katexPromise;
}

export function Formula({ tex, inline = false, className }: { tex: string; inline?: boolean; className?: string }) {
  const [html, setHtml] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    loadKatex().then((k) => {
      if (!live) return;
      setHtml(k.renderToString(tex, { displayMode: !inline, throwOnError: false, output: "html", strict: "ignore" }));
    });
    return () => {
      live = false;
    };
  }, [tex, inline]);
  const Tag = inline ? "span" : "div";
  if (html === null) return <Tag className={`oc-formula ${className ?? ""}`}><code className="num subtle">{tex}</code></Tag>;
  return <Tag className={`oc-formula ${inline ? "" : "oc-formula-block"} ${className ?? ""}`} dangerouslySetInnerHTML={{ __html: html }} />;
}

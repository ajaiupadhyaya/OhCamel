/**
 * The "?" affordance. Hover, focus or click to open; Esc / click-away closes.
 *   <InfoTip info="sharpe" />                                     glossary key (lib/glossary.ts)
 *   <InfoTip info={{ text: "…", formula: "…", reference: "…" }} />
 * Renders nothing when info is undefined, so it is safe to always pass through.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { resolveInfo, type GlossaryKey, type Info } from "../lib/glossary";
import { Formula } from "./Formula";
import { Icon } from "./Icon";

export type InfoProp = Info | GlossaryKey | string;

export function InfoTip({ info, label, size = 14 }: { info?: InfoProp; label?: string; size?: number }) {
  const data = resolveInfo(info);
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number; above: boolean } | null>(null);
  const hoverTimer = useRef<number | undefined>(undefined);

  const place = useCallback(() => {
    const b = btn.current?.getBoundingClientRect();
    if (!b) return;
    const w = Math.min(340, window.innerWidth - 24);
    const h = pop.current?.offsetHeight ?? 160;
    let left = b.left + b.width / 2 - w / 2;
    left = Math.max(12, Math.min(left, window.innerWidth - w - 12));
    const above = b.bottom + h + 12 > window.innerHeight && b.top - h - 12 > 0;
    setPos({ top: above ? b.top - h - 8 : b.bottom + 8, left, above });
  }, []);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && (setOpen(false), setPinned(false));
    const onDown = (e: MouseEvent) => {
      if (!pop.current?.contains(e.target as Node) && !btn.current?.contains(e.target as Node)) {
        setOpen(false);
        setPinned(false);
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, place]);

  if (!data) return null;
  const enter = () => {
    window.clearTimeout(hoverTimer.current);
    hoverTimer.current = window.setTimeout(() => setOpen(true), 120);
  };
  const leave = () => {
    window.clearTimeout(hoverTimer.current);
    if (!pinned) hoverTimer.current = window.setTimeout(() => setOpen(false), 150);
  };

  return (
    <>
      <button
        ref={btn}
        type="button"
        className="oc-infotip-btn"
        aria-label={`What is ${data.title ?? label ?? "this"}?`}
        aria-expanded={open}
        onMouseEnter={enter}
        onMouseLeave={leave}
        onFocus={() => setOpen(true)}
        onBlur={() => !pinned && setOpen(false)}
        onClick={(e) => {
          e.stopPropagation();
          setPinned((p) => !p);
          setOpen(true);
        }}
      >
        <Icon name="help" size={size} strokeWidth={1.7} />
      </button>
      {open &&
        createPortal(
          <div
            ref={pop}
            role="tooltip"
            className="oc-infotip-pop"
            style={{ top: pos?.top ?? -9999, left: pos?.left ?? -9999 }}
            onMouseEnter={() => window.clearTimeout(hoverTimer.current)}
            onMouseLeave={leave}
          >
            {(data.title ?? label) && <div className="oc-infotip-title">{data.title ?? label}</div>}
            <p className="oc-infotip-text">{data.text}</p>
            {data.formula && <Formula tex={data.formula} className="oc-infotip-formula" />}
            {data.reference && (
              <div className="oc-infotip-ref">
                {data.href ? (
                  <a href={data.href} target="_blank" rel="noreferrer">
                    {data.reference}
                  </a>
                ) : (
                  data.reference
                )}
              </div>
            )}
          </div>,
          document.body,
        )}
    </>
  );
}

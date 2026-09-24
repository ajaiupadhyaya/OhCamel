/**
 * Form & navigation controls: Tabs, SegmentedControl, NumberField, Slider, Field, Toggle.
 * All are controlled components (value + onChange).
 */
import { useEffect, useId, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { InfoTip, type InfoProp } from "./InfoTip";

type Opt<T extends string> = T | { value: T; label: ReactNode; title?: string; disabled?: boolean };
const optValue = <T extends string>(o: Opt<T>): T => (typeof o === "string" ? o : o.value);
const optLabel = <T extends string>(o: Opt<T>): ReactNode => (typeof o === "string" ? o : o.label);

// ------------------------------------------------------------------ Tabs

export interface TabItem<T extends string = string> {
  id: T;
  label: ReactNode;
  badge?: ReactNode;
  disabled?: boolean;
}

/**
 * Underlined editorial tabs. Controlled; pair with useTabParam() to deep-link:
 *   const [tab, setTab] = useTabParam("tab", "overview");
 *   <Tabs items={[{id:"overview",label:"Overview"}, …]} value={tab} onChange={setTab} />
 */
export function Tabs<T extends string>({ items, value, onChange, className }: { items: TabItem<T>[]; value: T; onChange: (v: T) => void; className?: string }) {
  return (
    <div className={`oc-tabs ${className ?? ""}`} role="tablist">
      {items.map((t) => (
        <button
          key={t.id}
          role="tab"
          type="button"
          aria-selected={t.id === value}
          disabled={t.disabled}
          className={`oc-tab ${t.id === value ? "active" : ""}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
          {t.badge !== undefined && <span className="oc-tab-badge">{t.badge}</span>}
        </button>
      ))}
    </div>
  );
}

/** A tab (or any string) state mirrored in the URL query (?key=value), replace-navigating. */
export function useTabParam<T extends string>(key: string, fallback: T): [T, (v: T) => void] {
  const [sp, setSp] = useSearchParams();
  const v = (sp.get(key) as T | null) ?? fallback;
  const set = (nv: T) =>
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        if (nv === fallback) n.delete(key);
        else n.set(key, nv);
        return n;
      },
      { replace: true },
    );
  return [v, set];
}

// ------------------------------------------------------------------ SegmentedControl

/** Pill toggle for 2–6 mutually exclusive options: <SegmentedControl options={["1Y","3Y"]} value={w} onChange={setW} /> */
export function SegmentedControl<T extends string>({ options, value, onChange, size = "md", ariaLabel, className }: { options: Opt<T>[]; value: T; onChange: (v: T) => void; size?: "sm" | "md"; ariaLabel?: string; className?: string }) {
  return (
    <div className={`oc-seg oc-seg-${size} ${className ?? ""}`} role="radiogroup" aria-label={ariaLabel}>
      {options.map((o) => {
        const v = optValue(o);
        return (
          <button
            key={v}
            type="button"
            role="radio"
            aria-checked={v === value}
            className={v === value ? "active" : ""}
            disabled={typeof o !== "string" && o.disabled}
            title={typeof o !== "string" ? o.title : undefined}
            onClick={() => onChange(v)}
          >
            {optLabel(o)}
          </button>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ Field wrapper

/** Label + optional info + hint around any control. */
export function Field({ label, info, hint, children, htmlFor, inline }: { label: ReactNode; info?: InfoProp; hint?: ReactNode; children: ReactNode; htmlFor?: string; inline?: boolean }) {
  return (
    <div className={`oc-field ${inline ? "oc-field-inline" : ""}`}>
      <label className="oc-field-label" htmlFor={htmlFor}>
        {label}
        <InfoTip info={info} size={12} label={typeof label === "string" ? label : undefined} />
      </label>
      {children}
      {hint && <div className="oc-field-hint">{hint}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ NumberField

/**
 * Numeric input with units. `percent` shows/edits value×100 with a "%" suffix while the
 * value you receive stays a decimal (0.05 <-> "5").
 *   <NumberField label="Confidence" value={0.99} percent min={0.5} max={0.999} step={0.005} onChange={setA} />
 *   <NumberField label="Notional" value={1e6} unit="$" onChange={setN} />
 */
export function NumberField({ label, value, onChange, min, max, step, unit, percent, info, hint, digits, disabled, width }: { label?: ReactNode; value: number | null | undefined; onChange: (v: number) => void; min?: number; max?: number; step?: number; unit?: string; percent?: boolean; info?: InfoProp; hint?: ReactNode; digits?: number; disabled?: boolean; width?: number | string }) {
  const id = useId();
  const k = percent ? 100 : 1;
  const show = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? "" : String(+(v * k).toFixed(digits ?? (percent ? 4 : 6))));
  const [text, setText] = useState(show(value));
  useEffect(() => setText(show(value)), [value]); // eslint-disable-line react-hooks/exhaustive-deps
  const commit = (t: string) => {
    const n = parseFloat(t.replace(/[,\s%$]/g, ""));
    if (!Number.isFinite(n)) return setText(show(value));
    let v = n / k;
    if (min !== undefined) v = Math.max(min, v);
    if (max !== undefined) v = Math.min(max, v);
    onChange(v);
    setText(show(v));
  };
  const u = percent ? "%" : unit;
  const input = (
    <div className={`oc-number ${disabled ? "disabled" : ""}`} style={{ width }}>
      {u === "$" && <span className="oc-number-unit pre">$</span>}
      <input
        id={id}
        className="num"
        inputMode="decimal"
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit((e.target as HTMLInputElement).value);
          if ((e.key === "ArrowUp" || e.key === "ArrowDown") && step) {
            e.preventDefault();
            const cur = value ?? 0;
            let v = cur + (e.key === "ArrowUp" ? step : -step);
            if (min !== undefined) v = Math.max(min, v);
            if (max !== undefined) v = Math.min(max, v);
            onChange(+v.toFixed(10));
          }
        }}
      />
      {u && u !== "$" && <span className="oc-number-unit">{u}</span>}
    </div>
  );
  if (!label) return input;
  return (
    <Field label={label} info={info} hint={hint} htmlFor={id}>
      {input}
    </Field>
  );
}

// ------------------------------------------------------------------ Slider

/**
 * Range slider with a live readout.
 *   <Slider label="Discount rate" value={r} min={0.04} max={0.15} step={0.0025} format={(v) => fmtPct(v, 2)} onChange={setR} />
 */
export function Slider({ label, value, onChange, min, max, step = 1, format, info, hint, disabled }: { label?: ReactNode; value: number; onChange: (v: number) => void; min: number; max: number; step?: number; format?: (v: number) => string; info?: InfoProp; hint?: ReactNode; disabled?: boolean }) {
  const id = useId();
  const pct = ((value - min) / (max - min || 1)) * 100;
  return (
    <div className="oc-slider">
      {label && (
        <div className="oc-slider-head">
          <label className="oc-field-label" htmlFor={id}>
            {label}
            <InfoTip info={info} size={12} label={typeof label === "string" ? label : undefined} />
          </label>
          <span className="oc-slider-value num">{format ? format(value) : value}</span>
        </div>
      )}
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        style={{ ["--pct" as any]: `${pct}%` }}
      />
      {hint && <div className="oc-field-hint">{hint}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ Toggle

export function Toggle({ label, checked, onChange, disabled }: { label: ReactNode; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className={`oc-toggle ${disabled ? "disabled" : ""}`}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="oc-toggle-track" aria-hidden>
        <span className="oc-toggle-thumb" />
      </span>
      <span>{label}</span>
    </label>
  );
}

/** Native select styled to match. options: string[] or {value,label}[] */
export function Select<T extends string>({ value, onChange, options, ariaLabel, className, style }: { value: T; onChange: (v: T) => void; options: Opt<T>[]; ariaLabel?: string; className?: string; style?: React.CSSProperties }) {
  return (
    <select className={`select ${className ?? ""}`} value={value} aria-label={ariaLabel} onChange={(e) => onChange(e.target.value as T)} style={style}>
      {options.map((o) => (
        <option key={optValue(o)} value={optValue(o)}>
          {typeof o === "string" ? o : typeof o.label === "string" ? o.label : o.value}
        </option>
      ))}
    </select>
  );
}

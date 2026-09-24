/**
 * Renders a backend parameter schema into controls.
 *   const [vals, setVals] = useState(() => paramDefaults(spec.params));
 *   <ParamForm params={spec.params} values={vals} onChange={setVals} />
 *
 * Supported `type`s: int, float/number, bool/boolean, str/string, select/choice (with
 * `choices`/`options`), date. Numbers with both min & max render as sliders; others as
 * NumberFields. `unit: "%"` (or name ending in _pct) edits decimals as percents.
 */
import type { ParamSpec } from "../lib/types";
import { Field, NumberField, Select, Slider, Toggle } from "./Controls";

export type ParamValues = Record<string, unknown>;

export function paramDefaults(params: ParamSpec[] | null | undefined): ParamValues {
  const out: ParamValues = {};
  for (const p of params ?? []) out[p.name] = p.default ?? null;
  return out;
}

const humanize = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function ParamForm({ params, values, onChange, columns = 2 }: { params: ParamSpec[]; values: ParamValues; onChange: (v: ParamValues) => void; columns?: 1 | 2 | 3 }) {
  const set = (k: string, v: unknown) => onChange({ ...values, [k]: v });
  return (
    <div className="oc-paramform" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
      {params.map((p) => {
        const label = p.label ?? humanize(p.name);
        const info = p.description ? { title: label, text: p.description } : undefined;
        const v = values[p.name] ?? p.default;
        const t = String(p.type).toLowerCase();
        const choices = p.choices ?? p.options;
        if (choices && choices.length) {
          return (
            <Field key={p.name} label={label} info={info}>
              <Select value={String(v ?? "")} onChange={(nv) => set(p.name, typeof choices[0] === "number" ? Number(nv) : nv)} options={choices.map(String)} ariaLabel={label} />
            </Field>
          );
        }
        if (t === "bool" || t === "boolean") {
          return (
            <div key={p.name} className="oc-paramform-toggle">
              <Toggle label={label} checked={!!v} onChange={(nv) => set(p.name, nv)} />
            </div>
          );
        }
        if (t === "int" || t === "float" || t === "number" || t === "integer") {
          const isInt = t === "int" || t === "integer";
          const pct = p.unit === "%" || /_pct$/.test(p.name);
          const num = typeof v === "number" ? v : Number(v ?? 0);
          if (p.min !== null && p.min !== undefined && p.max !== null && p.max !== undefined) {
            const step = p.step ?? (isInt ? 1 : (p.max - p.min) / 100);
            return (
              <Slider
                key={p.name}
                label={label}
                info={info}
                value={num}
                min={p.min}
                max={p.max}
                step={step}
                format={(x) => (pct ? `${(x * 100).toFixed(1)}%` : isInt ? String(Math.round(x)) : `${+x.toFixed(4)}${p.unit && p.unit !== "%" ? ` ${p.unit}` : ""}`)}
                onChange={(x) => set(p.name, isInt ? Math.round(x) : x)}
              />
            );
          }
          return <NumberField key={p.name} label={label} info={info} value={num} min={p.min ?? undefined} max={p.max ?? undefined} step={p.step ?? (isInt ? 1 : undefined)} percent={pct} unit={pct ? undefined : (p.unit ?? undefined)} onChange={(x) => set(p.name, isInt ? Math.round(x) : x)} />;
        }
        if (t === "date") {
          return (
            <Field key={p.name} label={label} info={info}>
              <input className="input num" type="date" value={String(v ?? "")} onChange={(e) => set(p.name, e.target.value || null)} />
            </Field>
          );
        }
        return (
          <Field key={p.name} label={label} info={info}>
            <input className="input" value={String(v ?? "")} onChange={(e) => set(p.name, e.target.value)} />
          </Field>
        );
      })}
    </div>
  );
}

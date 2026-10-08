/**
 * One or two numeric parameters and the values to try for each. Emits a validated grid
 * ({param: values}) or the first problem found, as a terse caps flag.
 */
import { useMemo } from "react";
import { Field, Select } from "../../components";
import type { ParamSpec } from "../../lib/types";
import { defaultValues, humanize, parseValues } from "./config";

export interface Axis {
  param: string;
  text: string;
}

export function defaultAxes(params: ParamSpec[], n: 1 | 2 = 2): Axis[] {
  return params.slice(0, n).map((p) => ({ param: p.name, text: defaultValues(p).join(", ") }));
}

export function gridFromAxes(axes: Axis[], params: ParamSpec[]): { grid: Record<string, number[]>; error: string | null; combos: number } {
  const grid: Record<string, number[]> = {};
  for (const a of axes) {
    const spec = params.find((p) => p.name === a.param);
    const { values, error } = parseValues(a.text, spec);
    if (error) return { grid: {}, error: `${humanize(a.param)} · ${error}`, combos: 0 };
    grid[a.param] = values;
  }
  const combos = Object.values(grid).reduce((n, v) => n * v.length, 1);
  return { grid, error: null, combos };
}

export function GridEditor({ axes, onChange, params, maxAxes = 2 }: { axes: Axis[]; onChange: (a: Axis[]) => void; params: ParamSpec[]; maxAxes?: number }) {
  const used = useMemo(() => new Set(axes.map((a) => a.param)), [axes]);
  const setAxis = (i: number, patch: Partial<Axis>) => onChange(axes.map((a, j) => (j === i ? { ...a, ...patch } : a)));
  return (
    <div className="sl-controls">
      {axes.map((a, i) => {
        const spec = params.find((p) => p.name === a.param);
        return (
          <div key={i} className="sl-axis">
            <Field label={i === 0 ? "Axis X" : "Axis Y"}>
              <Select
                value={a.param}
                onChange={(v) => {
                  const p = params.find((x) => x.name === v)!;
                  setAxis(i, { param: v, text: defaultValues(p).join(", ") });
                }}
                options={params.filter((p) => p.name === a.param || !used.has(p.name)).map((p) => ({ value: p.name, label: humanize(p.name) }))}
                ariaLabel={`Axis ${i + 1} parameter`}
              />
            </Field>
            <Field label="Values" hint={spec?.min != null ? <span className="num">{`${spec.type === "int" ? "INT" : "NUM"} · ${spec.min} … ${spec.max}`}</span> : undefined}>
              <input className="input num sl-values" value={a.text} onChange={(e) => setAxis(i, { text: e.target.value })} aria-label={`Values for ${a.param}`} spellCheck={false} />
            </Field>
            {i > 0 && (
              <button type="button" className="btn btn-sm" onClick={() => onChange(axes.filter((_, j) => j !== i))}>
                DROP Y
              </button>
            )}
          </div>
        );
      })}
      {axes.length < Math.min(maxAxes, params.length) && (
        <div>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              const p = params.find((x) => !used.has(x.name));
              if (p) onChange([...axes, { param: p.name, text: defaultValues(p).join(", ") }]);
            }}
          >
            ADD Y
          </button>
        </div>
      )}
    </div>
  );
}

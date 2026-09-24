/**
 * Pick one or two numeric parameters and the values to try for each. Emits a validated
 * grid ({param: values}) or the first problem found.
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
  return params
    .slice(0, n)
    .map((p) => ({ param: p.name, text: defaultValues(p).join(", ") }));
}

export function gridFromAxes(
  axes: Axis[],
  params: ParamSpec[],
): { grid: Record<string, number[]>; error: string | null; combos: number } {
  const grid: Record<string, number[]> = {};
  for (const a of axes) {
    const spec = params.find((p) => p.name === a.param);
    const { values, error } = parseValues(a.text, spec);
    if (error)
      return { grid: {}, error: `${humanize(a.param)}: ${error}`, combos: 0 };
    grid[a.param] = values;
  }
  const combos = Object.values(grid).reduce((n, v) => n * v.length, 1);
  return { grid, error: null, combos };
}

export function GridEditor({
  axes,
  onChange,
  params,
  maxAxes = 2,
}: {
  axes: Axis[];
  onChange: (a: Axis[]) => void;
  params: ParamSpec[];
  maxAxes?: number;
}) {
  const used = useMemo(() => new Set(axes.map((a) => a.param)), [axes]);
  const setAxis = (i: number, patch: Partial<Axis>) =>
    onChange(axes.map((a, j) => (j === i ? { ...a, ...patch } : a)));
  return (
    <div className="sl-grid-editor">
      {axes.map((a, i) => {
        const spec = params.find((p) => p.name === a.param);
        return (
          <div key={i} className="sl-axis">
            <Field
              label={i === 0 ? "Parameter (x)" : "Parameter (y)"}
              info={
                spec?.description
                  ? {
                      title: humanize(a.param),
                      text: `${spec.description}${spec.min != null ? ` Range ${spec.min}–${spec.max}.` : ""}`,
                    }
                  : undefined
              }
            >
              <Select
                value={a.param}
                onChange={(v) => {
                  const p = params.find((x) => x.name === v)!;
                  setAxis(i, { param: v, text: defaultValues(p).join(", ") });
                }}
                options={params
                  .filter((p) => p.name === a.param || !used.has(p.name))
                  .map((p) => ({ value: p.name, label: humanize(p.name) }))}
                ariaLabel={`Axis ${i + 1} parameter`}
              />
            </Field>
            <Field
              label="Values"
              hint={
                spec
                  ? `${spec.type === "int" ? "whole numbers" : "numbers"}${spec.min != null ? `, ${spec.min} to ${spec.max}` : ""}`
                  : undefined
              }
            >
              <input
                className="input num sl-values"
                value={a.text}
                onChange={(e) => setAxis(i, { text: e.target.value })}
                aria-label={`Values for ${a.param}`}
                spellCheck={false}
              />
            </Field>
            {i > 0 && (
              <button
                type="button"
                className="btn btn-ghost btn-sm sl-axis-remove"
                onClick={() => onChange(axes.filter((_, j) => j !== i))}
              >
                Remove
              </button>
            )}
          </div>
        );
      })}
      {axes.length < Math.min(maxAxes, params.length) && (
        <button
          type="button"
          className="btn btn-sm sl-axis-add"
          onClick={() => {
            const p = params.find((x) => !used.has(x.name));
            if (p)
              onChange([
                ...axes,
                { param: p.name, text: defaultValues(p).join(", ") },
              ]);
          }}
        >
          + Add a second parameter
        </button>
      )}
    </div>
  );
}

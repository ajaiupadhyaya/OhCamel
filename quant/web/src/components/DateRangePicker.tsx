/**
 * Analysis window picker with presets 1Y/3Y/5Y/10Y/Max. Dates are ISO strings; null = open.
 *   <DateRangePicker value={{ start, end }} onChange={({ start, end }) => update({ start, end })} />
 * `anchor` is the date presets count back from (default: today; pass the data's last date).
 */
import { toIsoDate, parseDate } from "../lib/format";
import { SegmentedControl } from "./Controls";

export interface DateRange {
  start: string | null;
  end: string | null;
}

const PRESETS = ["1Y", "3Y", "5Y", "10Y", "Max"] as const;
type Preset = (typeof PRESETS)[number] | "Custom";

export function presetRange(p: (typeof PRESETS)[number], anchor?: string | null): DateRange {
  if (p === "Max") return { start: null, end: null };
  const years = parseInt(p, 10);
  const a = parseDate(anchor ?? null) ?? new Date();
  const s = new Date(a.getFullYear() - years, a.getMonth(), a.getDate());
  return { start: toIsoDate(s), end: null };
}

function detectPreset(v: DateRange, anchor?: string | null): Preset {
  if (!v.start && !v.end) return "Max";
  for (const p of PRESETS) {
    const r = presetRange(p, anchor);
    if (r.start === v.start && r.end === v.end) return p;
  }
  return "Custom";
}

export function DateRangePicker({ value, onChange, anchor, showInputs = true, presets = PRESETS as unknown as (typeof PRESETS)[number][] }: { value: DateRange; onChange: (v: DateRange) => void; anchor?: string | null; showInputs?: boolean; presets?: (typeof PRESETS)[number][] }) {
  const active = detectPreset(value, anchor);
  return (
    <div className="oc-daterange">
      <SegmentedControl
        size="sm"
        ariaLabel="Window"
        options={presets as string[]}
        value={active === "Custom" ? ("" as string) : active}
        onChange={(p) => onChange(presetRange(p as (typeof PRESETS)[number], anchor))}
      />
      {showInputs && (
        <div className="oc-daterange-inputs">
          <input className="input num" type="date" aria-label="Start date" value={value.start ?? ""} onChange={(e) => onChange({ ...value, start: e.target.value || null })} />
          <span className="subtle">→</span>
          <input className="input num" type="date" aria-label="End date" value={value.end ?? ""} onChange={(e) => onChange({ ...value, end: e.target.value || null })} />
        </div>
      )}
    </div>
  );
}

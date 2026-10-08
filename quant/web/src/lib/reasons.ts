/**
 * Skip and failure reasons as terse labels. Jobs and vendors write sentences ("AAPL: offline
 * mode and no committed fixture (available: …)", a Python list repr of FRED series); a Cell
 * shows a reason code and how many names it covers. A reason that matches no pattern is
 * OTHER in a table and verbatim elsewhere; the full text stays in the element's title.
 */
const CODES: [RegExp, string][] = [
  [/offline.*fixture/i, "OFFLINE · NO FIXTURE"],
  [/offline/i, "OFFLINE"],
  [/rate.?limit|\b429\b/i, "RATE LIMITED"],
  [/timed? ?out/i, "TIMEOUT"],
  [/non-finite|degenerate/i, "DEGENERATE FIT"],
  [/confidence set not computable/i, "MCS NOT COMPUTABLE"],
  [/sessions? \(<|full history|too short|insufficient history/i, "SHORT HISTORY"],
  [/not in (the )?warehouse|no rows|no data/i, "NO DATA"],
  [/\bHTTP \d{3}\b|status \d{3}/i, "VENDOR ERROR"],
];

export function reasonCode(reason: string | null | undefined): string {
  if (!reason) return "—";
  for (const [re, code] of CODES) if (re.test(reason)) return code;
  return "OTHER";
}

/** The tickers or series a reason names: `T: …` segments joined by `;`, a `['A', 'B']` list, or `fixtures: A, B`. */
export function reasonNames(reason: string | null | undefined): string[] {
  if (!reason) return [];
  const list = reason.match(/\[([^\]]*)\]/);
  if (list) return [...list[1].matchAll(/'([^']+)'|"([^"]+)"/g)].map((m) => m[1] ?? m[2]);
  const tail = reason.match(/fixtures?:\s*([A-Z0-9.^=-]+(?:,\s*[A-Z0-9.^=-]+)*)\s*$/);
  if (tail) return tail[1].split(/,\s*/);
  return reason
    .split(/;\s*/)
    .map((s) => s.match(/^([A-Z0-9.^=-]{1,12}):\s/)?.[1])
    .filter((x): x is string => !!x);
}

/** A one-line label: the code and, when it names several, how many; unknown reasons verbatim. */
export function reasonLabel(reason: string | null | undefined): string {
  const code = reasonCode(reason);
  if (code === "OTHER") return reason ?? "—";
  const n = reasonNames(reason).length;
  if (n < 2) return code;
  const unit = /FRED|series/i.test(reason ?? "") ? "SERIES" : "NAMES";
  return `${code} · ${n} ${unit}`;
}

export interface ReasonGroup {
  key: string;
  code: string;
  model: string | null;
  n: number;
  tickers: string[];
  /** One full reason, for the title tooltip. */
  sample: string;
}

/** Per-ticker skip rows collapsed to one row per (reason code, model), in first-seen order. */
export function groupReasons(rows: { ticker?: string | null; model?: string | null; reason?: string | null }[]): ReasonGroup[] {
  const by = new Map<string, ReasonGroup>();
  for (const r of rows) {
    const code = reasonCode(r.reason);
    const model = r.model ?? null;
    const key = `${code}|${model ?? ""}`;
    let g = by.get(key);
    if (!g) by.set(key, (g = { key, code, model, n: 0, tickers: [], sample: r.reason ?? "" }));
    g.n += 1;
    if (r.ticker) g.tickers.push(r.ticker);
  }
  return [...by.values()];
}

/** Up to three names, then +N for the rest. */
export function namesShort(names: string[], k = 3): string {
  if (!names.length) return "—";
  return names.slice(0, k).join(", ") + (names.length > k ? ` +${names.length - k}` : "");
}

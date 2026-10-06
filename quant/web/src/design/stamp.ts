/**
 * Mono timestamp for provenance and asOf: "05 OCT 16:02" (New York time) for a timestamp,
 * "05 OCT 2026" for a date. A value that does not parse is shown as given.
 */
const time = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

export function fmtStamp(v: string | null | undefined): string {
  if (!v) return "—";
  const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
  if (d) return `${d[3]} ${MONTHS[Number(d[2]) - 1] ?? d[2]} ${d[1]}`;
  const t = Date.parse(v);
  if (Number.isNaN(t)) return v;
  const p = Object.fromEntries(time.formatToParts(new Date(t)).map((x) => [x.type, x.value]));
  return `${p.day} ${String(p.month).toUpperCase()} ${p.hour}:${p.minute}`;
}

export type Session = "open" | "closed" | "pre" | "post" | "unknown";

const fmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", weekday: "short", day: "2-digit", month: "short",
  year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export function formatDateline(now: Date, session: Session): string {
  const p = Object.fromEntries(fmt.formatToParts(now).map((x) => [x.type, x.value]));
  const label = { open: "OPEN", closed: "CLOSED", pre: "PRE-MARKET", post: "AFTER HOURS", unknown: "UNKNOWN" }[session];
  return `${p.weekday} ${p.day} ${p.month} ${p.year} · ${p.hour}:${p.minute} ET · SESSION ${label}`.toUpperCase();
}

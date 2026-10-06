/**
 * The command line's grammar: a pure parser from one typed line to a Command, and the path
 * a Command navigates to. Function codes (`RATES`, `GO VOL`), ticker functions (`SPY GP`,
 * `AAPL DES`, `QQQ OMON`), `PORT RISK <alpha> <h>D`, `JOB <kind>`; anything else is a search.
 * Never throws (Review Focus 2).
 */
export type Command =
  | { kind: "go"; path: string }
  | { kind: "ticker"; ticker: string; fn: "GP" | "DES" | "OMON" }
  | { kind: "risk"; alpha: number; horizon: number }
  | { kind: "job"; job: string }
  | { kind: "search"; query: string }
  | { kind: "unknown"; input: string };

const PAGES: Record<string, string> = {
  FRONT: "/", MKTS: "/markets", RISK: "/risk", PORT: "/portfolio", OPT: "/optimize",
  RSCH: "/research", VOL: "/options", RATES: "/macro", CO: "/company", DECK: "/deck",
  SYS: "/system", COMPUTE: "/compute", ENG: "/engine", DOCS: "/methodology", LEDGER: "/ledger",
};
const TICKER = /^[A-Z][A-Z0-9.-]{0,9}$/;
const FNS = new Set(["GP", "DES", "OMON"]);

export function parseCommand(input: string): Command {
  const raw = input.trim().slice(0, 120);
  if (!raw) return { kind: "unknown", input: "" };
  const t = raw.toUpperCase().split(/\s+/);
  if (t.length === 1 && PAGES[t[0]]) return { kind: "go", path: PAGES[t[0]] };
  if (t.length === 2 && t[0] === "GO" && PAGES[t[1]]) return { kind: "go", path: PAGES[t[1]] };
  if (t.length === 2 && TICKER.test(t[0]) && FNS.has(t[1]))
    return { kind: "ticker", ticker: t[0], fn: t[1] as "GP" | "DES" | "OMON" };
  if (t[0] === "PORT" && t[1] === "RISK" && t.length <= 4) {
    const a = t[2] === undefined ? 99 : Number(t[2]);
    const h = t[3] === undefined ? 1 : Number(t[3].replace(/D$/, ""));
    if ([90, 95, 97.5, 99, 99.5].includes(a) && Number.isInteger(h) && h >= 1 && h <= 20)
      return { kind: "risk", alpha: a / 100, horizon: h };
  }
  if (t.length === 2 && t[0] === "JOB" && /^[A-Z_.]{2,32}$/.test(t[1])) return { kind: "job", job: t[1].toLowerCase() };
  return { kind: "search", query: raw };
}

export function commandPath(c: Command): string | null {
  switch (c.kind) {
    case "go": return c.path;
    case "ticker": return c.fn === "GP" ? `/ticker/${c.ticker}` : c.fn === "DES" ? `/company/${c.ticker}` : `/options?ticker=${c.ticker}`;
    case "risk": return `/risk?alpha=${c.alpha}&h=${c.horizon}`;
    case "job": return `/compute?kind=${c.job}`;
    default: return null;
  }
}

export const RECENT_MAX = 8;

/** The recent-commands list after running `entry`: newest first, upper-cased, deduplicated, capped. */
export function pushRecent(list: unknown, entry: string): string[] {
  const prev = Array.isArray(list) ? list.filter((x): x is string => typeof x === "string") : [];
  const e = entry.trim().slice(0, 120).toUpperCase();
  if (!e) return prev.slice(0, RECENT_MAX);
  return [e, ...prev.filter((x) => x !== e)].slice(0, RECENT_MAX);
}

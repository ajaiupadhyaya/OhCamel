/**
 * Review Focus 1: a panel past its max age is stale. An unknown, unparseable or
 * future asOf is stale too -- never treated as fresh.
 */
export function isStale(asOf: string | null, maxAgeSec: number, now: Date): boolean {
  if (!asOf) return true;
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(asOf);
  // A date-only asOf is good until the end of that New York day (EST/EDT's hour is inside any daily max age).
  const t = Date.parse(dateOnly ? `${asOf}T23:59:59-04:00` : asOf);
  if (Number.isNaN(t)) return true;
  const age = (now.getTime() - t) / 1000;
  if (dateOnly) return Math.max(0, age) > maxAgeSec;
  if (age < -300) return true; // a timestamp in the future is clock skew, not freshness
  return age > maxAgeSec;
}

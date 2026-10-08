/** Split daily returns into ordinary days and VaR breaches (loss beyond that morning's VaR). */
export function splitBreaches(returns: number[], varF: (number | null)[]): { ok: (number | null)[]; breach: (number | null)[]; count: number } {
  const ok: (number | null)[] = [];
  const breach: (number | null)[] = [];
  let count = 0;
  returns.forEach((r, i) => {
    const lim = varF[i];
    if (lim != null && -r > lim) {
      breach.push(r);
      ok.push(null);
      count += 1;
    } else {
      breach.push(null);
      ok.push(r);
    }
  });
  return { ok, breach, count };
}

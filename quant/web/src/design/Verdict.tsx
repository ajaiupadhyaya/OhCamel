/**
 * A stamped verdict. Every research and model result leads with one.
 *   <Verdict value="FAIL" detail="DSR 0.41 < 0.95" />
 * FAIL is set in --signal; every other value in ink. The detail is a terse label (verdictLabel).
 */
export type VerdictValue = "PASS" | "FAIL" | "ADVISORY" | "INSUFFICIENT DATA" | "DESCRIPTIVE ONLY" | "AWAITING PRE-REGISTRATION";

/**
 * A verdict detail as public copy: caps, clauses ruled with " · ". Artifacts written before the
 * products emitted labels carried operator instructions ("…: run `python -m …`"): those are cut
 * to the label before the colon.
 */
export function verdictLabel(detail: string): string {
  let s = detail;
  if (/`|python -m/.test(s) && s.includes(":")) s = s.slice(0, s.indexOf(":"));
  return s.replace(/\s*;\s*/g, " · ").trim().toUpperCase();
}

export function Verdict({ value, detail }: { value: VerdictValue; detail?: string }) {
  const slug = value.toLowerCase().replace(/[^a-z]+/g, "-");
  return (
    <span className="verdict-wrap">
      <span className={"verdict verdict-" + slug}>{value}</span>
      {detail && <span className="verdict-detail num">{verdictLabel(detail)}</span>}
    </span>
  );
}

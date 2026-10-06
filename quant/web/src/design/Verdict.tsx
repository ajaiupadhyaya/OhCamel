/**
 * A stamped verdict. Every research and model result leads with one.
 *   <Verdict value="FAIL" detail="DSR 0.41 < 0.95" />
 * FAIL is set in --signal; every other value in ink.
 */
export type VerdictValue = "PASS" | "FAIL" | "ADVISORY" | "INSUFFICIENT DATA" | "AWAITING PRE-REGISTRATION";

export function Verdict({ value, detail }: { value: VerdictValue; detail?: string }) {
  const slug = value.toLowerCase().replace(/[^a-z]+/g, "-");
  return (
    <span className="verdict-wrap">
      <span className={"verdict verdict-" + slug}>{value}</span>
      {detail && <span className="verdict-detail num">{detail}</span>}
    </span>
  );
}

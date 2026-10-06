/**
 * A block whose source has nothing yet: INSUFFICIENT DATA stamped, the reason beside it,
 * and the read it is waiting on in mono. Never a placeholder number.
 *   <Absent reason="NOT YET RUN" source="risk.mc_atlas" />
 */
import { Verdict, type VerdictValue } from "./Verdict";

export function Absent({ reason, source, value = "INSUFFICIENT DATA" }: { reason: string; source?: string; value?: VerdictValue }) {
  return (
    <div className="absent">
      <Verdict value={value} detail={reason} />
      {source && <div className="absent-src num">{source}</div>}
    </div>
  );
}

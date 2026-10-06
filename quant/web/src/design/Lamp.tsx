/**
 * A square status lamp: filled ink (ok), hollow (idle), filled --signal (fault),
 * two diagonal hairlines (stale / unknown).
 *   <Lamp state="stale" label="Data" />
 */
export type LampState = "ok" | "idle" | "fault" | "stale";

export function Lamp({ state, label }: { state: LampState; label: string }) {
  return (
    <span role="img" aria-label={label + ": " + state} className={"lamp lamp-" + state}>
      {state === "stale" && (
        <svg viewBox="0 0 10 10" width="10" height="10" aria-hidden="true" focusable="false">
          <path d="M0 6 L6 0 M4 10 L10 4" stroke="currentColor" strokeWidth="1" fill="none" shapeRendering="crispEdges" />
        </svg>
      )}
    </span>
  );
}

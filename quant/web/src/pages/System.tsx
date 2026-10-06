/**
 * System (/system): the index of the four back-office pages, each with a one-line mono
 * status from GET /api/ops (job counts, engine mode, build sha) or the Ledger's date.
 */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Page } from "../components";
import { Lamp, type LampState } from "../design";
import { fmtStamp } from "../design/stamp";
import { absentLabel } from "../lib/artifacts";
import { useOps, type OpsOut } from "../shell/ops";
import { LEDGER, LEDGER_AS_OF } from "./ledger/entries";
import { MODELS } from "./methodology/models";
import { REFS } from "./methodology/references";
import "./ledger/ledger.css";

interface Row {
  code: string;
  to: string;
  title: string;
  lamp?: { state: LampState; label: string };
  status: ReactNode;
}

function opsLine(ops: ReturnType<typeof useOps>): string | null {
  if (ops.isLoading) return "OPS …";
  if (ops.isError) return `OPS ${absentLabel(ops.error) ?? "ERROR"}`;
  return null;
}

function rows(ops: ReturnType<typeof useOps>): Row[] {
  const d: OpsOut | undefined = ops.data;
  const down = opsLine(ops);
  const j = d?.jobs;
  const sha = d?.build?.git_sha;
  return [
    {
      code: "COMPUTE",
      to: "/compute",
      title: "Compute",
      lamp: { state: !j ? "stale" : (j.failed_24h ?? 0) > 0 ? "fault" : "ok", label: "Jobs" },
      status: down ?? (j ? `QUEUED ${j.queued ?? "—"} · RUNNING ${j.running ?? "—"} · DONE 24H ${j.done_24h ?? "—"} · FAILED 24H ${j.failed_24h ?? "—"}` : "JOBS NOT YET RUN"),
    },
    {
      code: "ENG",
      to: "/engine",
      title: "Live Engine",
      lamp: { state: d?.mode ? "ok" : "stale", label: "Engine" },
      status: down ?? `ENGINE ${d?.mode ? d.mode.toUpperCase() : "—"}`,
    },
    {
      code: "DOCS",
      to: "/methodology",
      title: "Methodology",
      status: `${MODELS.length} MODELS · ${Object.keys(REFS).length} REFERENCES${sha ? ` · BUILD ${sha.slice(0, 7)}` : ""}`,
    },
    {
      code: "LEDGER",
      to: "/ledger",
      title: "Ledger",
      status: `AS OF ${fmtStamp(LEDGER_AS_OF)} · ${LEDGER.length} ENTRIES`,
    },
  ];
}

export default function System() {
  const ops = useOps();
  return (
    <Page title="System">
      <ul className="sys-rows">
        {rows(ops).map((r) => (
          <li key={r.code} className="sys-row">
            <Link to={r.to} className="sys-link">
              <span className="sys-code">{r.code}</span>
              <span className="sys-title">{r.title}</span>
              <span className="sys-go" aria-hidden>
                →
              </span>
              <span className="sys-status">
                {r.lamp && <Lamp state={r.lamp.state} label={r.lamp.label} />}
                <span>{r.status}</span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </Page>
  );
}

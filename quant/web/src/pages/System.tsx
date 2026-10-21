/**
 * System (/system): the index of the four back-office pages, each with a one-line mono status:
 *   COMPUTE  GET /api/ops (job counts) and GET /api/ops/host (CPU, memory)
 *   ENG      GET /api/engine/status (mode, feed; 503 → BRIDGE OFF / UNREACHABLE)
 *   DOCS     the Methodology's own counts
 *   LEDGER   the Ledger's date and entry count
 * A read that fails says so; nothing is guessed.
 */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Page } from "../components";
import { Lamp, type LampState } from "../design";
import { fmtStamp } from "../design/stamp";
import { absentLabel } from "../lib/artifacts";
import { DataUnavailableError } from "../lib/api";
import { fmtNum, fmtPct } from "../lib/format";
import { useApiQuery } from "../lib/query";
import { useOps, type OpsOut } from "../shell/ops";
import type { EngineStatus } from "./engine/types";
import { LEDGER, LEDGER_AS_OF } from "./ledger/entries";
import { KERNEL_API } from "./methodology/compute";
import { MODELS } from "./methodology/models";
import "./ledger/ledger.css";

interface Row {
  code: string;
  to: string;
  title: string;
  lamp?: { state: LampState; label: string };
  status: ReactNode;
}

type Q<T> = { data?: T; isLoading: boolean; isError: boolean; error: unknown };

interface HostLite {
  cpus?: number | null;
  latest?: { cpu?: number | null; mem_total?: number | null; mem_available?: number | null } | null;
}

function down(label: string, q: Q<unknown>): string | null {
  if (q.isLoading) return `${label} …`;
  if (q.isError) return `${label} ${absentLabel(q.error) ?? "ERROR"}`;
  return null;
}

function computeStatus(ops: Q<OpsOut>, host: Q<HostLite>): string {
  const j = ops.data?.jobs;
  const jobs = down("JOBS", ops) ?? (j ? `QUEUED ${fmtNum(j.queued, 0)} · RUNNING ${fmtNum(j.running, 0)} · DONE 24H ${fmtNum(j.done_24h, 0)} · FAILED 24H ${fmtNum(j.failed_24h, 0)}` : "JOBS NOT YET RUN");
  const l = host.data?.latest;
  const h = down("HOST", host) ?? (l ? `CPU ${fmtPct(l.cpu, 0)} · MEM ${l.mem_total && l.mem_available != null ? fmtPct(1 - l.mem_available / l.mem_total, 0) : "—"}` : "HOST NO SAMPLE");
  return `${jobs} · ${h}`;
}

function engineStatus(st: Q<EngineStatus>): { lamp: LampState; text: string } {
  if (st.isLoading) return { lamp: "stale", text: "ENGINE …" };
  if (st.isError) {
    const off = st.error instanceof DataUnavailableError && (st.error.body as { configured?: boolean } | undefined)?.configured === false;
    return { lamp: off ? "idle" : "fault", text: off ? "BRIDGE OFF" : `ENGINE ${absentLabel(st.error) ?? "ERROR"}` };
  }
  const d = st.data!;
  return {
    lamp: d.health?.healthy ? "ok" : "fault",
    text: `${(d.ops?.mode ?? "MODE —").toUpperCase()} · FEED ${d.health?.healthy ? "HEALTHY" : "STALE"} · ${fmtNum(d.health?.symbols?.length, 0)} SYMBOLS · RTT ${fmtNum(d.latency_ms, 0)} MS`,
  };
}

function rows(ops: Q<OpsOut>, host: Q<HostLite>, eng: Q<EngineStatus>): Row[] {
  const j = ops.data?.jobs;
  const e = engineStatus(eng);
  return [
    {
      code: "COMPUTE",
      to: "/compute",
      title: "Compute",
      lamp: { state: !j ? "stale" : (j.failed_24h ?? 0) > 0 ? "fault" : "ok", label: "Jobs" },
      status: computeStatus(ops, host),
    },
    {
      code: "ENG",
      to: "/engine",
      title: "Live Engine",
      lamp: { state: e.lamp, label: "Engine" },
      status: e.text,
    },
    {
      code: "DOCS",
      to: "/methodology",
      title: "Methodology",
      status: `${fmtNum(MODELS.length, 0)} MODELS · ${fmtNum(KERNEL_API.length, 0)} KERNELS`,
    },
    {
      code: "LEDGER",
      to: "/ledger",
      title: "Ledger",
      status: `AS OF ${fmtStamp(LEDGER_AS_OF)} · ${fmtNum(LEDGER.length, 0)} ENTRIES`,
    },
  ];
}

export default function System() {
  const ops = useOps();
  const host = useApiQuery<HostLite>("/ops/host", undefined, { staleTime: 15_000 });
  const eng = useApiQuery<EngineStatus>("/engine/status", undefined, { staleTime: 15_000 });
  return (
    <Page title="System">
      <ul className="sys-rows">
        {rows(ops, host, eng).map((r) => (
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

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, JobQueuedError, api } from "./api";

afterEach(() => vi.unstubAllGlobals());

function respond(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })),
  );
}

const QUEUED = {
  job: { id: "j1", kind: "api.portfolio_compare", state: "queued", created: "2026-10-06T14:00:00Z" },
  status_url: "/api/jobs/j1",
  notes: ["This request exceeds the endpoint's synchronous cap, so it runs as a job on the batch worker; poll status_url or listen on /api/jobs/events, then read result_url."],
};

describe("202 {job} never reaches a page as data (Lane B, B5)", () => {
  it("rejects with JobQueuedError, which carries the job and is an ApiError that is not retried", async () => {
    respond(202, QUEUED);
    const err = await api.post("/portfolio/compare", { tickers: [] }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(JobQueuedError);
    expect(err).toBeInstanceOf(ApiError);
    const q = err as JobQueuedError;
    expect(q.status).toBe(202);
    expect(q.code).toBe("job_queued");
    expect(q.job.id).toBe("j1");
    expect(q.statusUrl).toBe("/api/jobs/j1");
    expect(q.detail).toMatch(/^Queued as job j1\b/);
    expect(q.detail).toContain("exceeds the endpoint's synchronous cap");
  });

  it("a 202 without a job wrapper (POST /api/jobs itself) is still data", async () => {
    respond(202, { id: "j2", state: "queued" });
    await expect(api.post("/jobs", { kind: "api.risk_backtest", params: {} })).resolves.toEqual({ id: "j2", state: "queued" });
  });

  it("a 200 is still data", async () => {
    respond(200, { ranking_by_fz0: ["fhs"] });
    await expect(api.post("/risk/backtest", {})).resolves.toEqual({ ranking_by_fz0: ["fhs"] });
  });
});

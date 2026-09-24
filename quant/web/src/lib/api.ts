/**
 * Typed fetch wrapper for the OhCamel Quant API.
 *
 *   const data = await api.get<Overview>("/market/overview", { universe: "core" });
 *   const risk = await api.post<RiskOut>("/risk/var", portfolioBody);
 *
 * Paths are relative to /api (a leading "/api" is tolerated). Query params with
 * undefined/null/"" values are dropped; arrays repeat the key (?t=A&t=B).
 *
 * Errors:
 *   - ApiError               any non-2xx; `.status`, `.detail` (server's `detail` string, or a
 *                            readable rendering of FastAPI's 422 validation list), `.body`.
 *   - DataUnavailableError   503 — the backend's DataUnavailable (a source could not serve the
 *                            request, e.g. offline mode or a vendor outage). Render it as an
 *                            informative "data source unavailable" state, never as fake data.
 *   - NetworkError           fetch itself failed (backend down).
 */

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly code?: string;
  readonly body: unknown;
  readonly path: string;
  constructor(status: number, detail: string, path: string, body?: unknown, code?: string) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.path = path;
    this.body = body;
    this.code = code;
  }
}

export class DataUnavailableError extends ApiError {
  constructor(detail: string, path: string, body?: unknown) {
    super(503, detail, path, body, "data_unavailable");
    this.name = "DataUnavailableError";
  }
}

export class NetworkError extends ApiError {
  constructor(message: string, path: string) {
    super(0, message, path, undefined, "network");
    this.name = "NetworkError";
  }
}

export type QueryValue = string | number | boolean | null | undefined | (string | number)[];
export type QueryParams = Record<string, QueryValue>;

export function buildUrl(path: string, params?: QueryParams): string {
  const clean = path.replace(/^\/?(api\/)?/, "");
  const url = `/api/${clean}`;
  if (!params) return url;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => qs.append(k, String(x)));
    else qs.append(k, String(v));
  }
  const s = qs.toString();
  return s ? `${url}?${s}` : url;
}

/** Turn FastAPI's error bodies into one human sentence. */
export function extractDetail(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return typeof body === "string" && body ? body : fallback;
  const d = (body as { detail?: unknown }).detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    // pydantic validation errors: [{loc: [...], msg: "..."}]
    return d
      .map((e: any) => {
        const loc = Array.isArray(e?.loc) ? e.loc.filter((x: unknown) => x !== "body" && x !== "query").join(".") : "";
        return loc ? `${loc}: ${e?.msg ?? "invalid"}` : String(e?.msg ?? JSON.stringify(e));
      })
      .join("; ");
  }
  if (d && typeof d === "object") return JSON.stringify(d);
  return fallback;
}

async function request<T>(method: string, path: string, opts: { params?: QueryParams; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const url = buildUrl(path, opts.params);
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      signal: opts.signal,
      headers: opts.body !== undefined ? { "Content-Type": "application/json", Accept: "application/json" } : { Accept: "application/json" },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    if ((e as Error)?.name === "AbortError") throw e;
    throw new NetworkError("Could not reach the OhCamel Quant API. Is the backend running?", url);
  }
  const text = await res.text();
  let body: unknown = undefined;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    const detail = extractDetail(body, `${res.status} ${res.statusText}`);
    if (res.status === 503) throw new DataUnavailableError(detail, url, body);
    const code = body && typeof body === "object" ? ((body as any).error as string | undefined) : undefined;
    throw new ApiError(res.status, detail, url, body, code);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string, params?: QueryParams, signal?: AbortSignal) => request<T>("GET", path, { params, signal }),
  post: <T>(path: string, body?: unknown, params?: QueryParams, signal?: AbortSignal) => request<T>("POST", path, { body, params, signal }),
};

export function isDataUnavailable(e: unknown): e is DataUnavailableError {
  return e instanceof DataUnavailableError;
}

/**
 * Wire shapes of the read-only engine bridge (quant/src/ohcamel_quant/api/routers/engine.py),
 * which wraps the OCaml engine's own JSON (lib/server.ml) unchanged. Money is USD; VaR/ES
 * are at the engine's configured confidence (95% by default); null = not computed yet.
 */
import type { Envelope } from "../../lib/types";

export interface FeedSymbol {
  symbol: string;
  last_tick: string | null;
  never_seen: boolean;
  stale: boolean;
}
export interface FeedHealth {
  healthy: boolean;
  stale: string[];
  never_seen: string[];
  symbols: FeedSymbol[];
}

export interface EngineStatus extends Envelope {
  configured: boolean;
  reachable: boolean;
  latency_ms: number;
  health: FeedHealth;
  ops: {
    mode?: "demo" | "live" | string;
    uptime_s?: number;
    build?: { git_sha?: string; built_at?: string };
    feed?: { healthy?: boolean; symbols?: number; stale?: number; never_seen?: number };
    stream?: { frames_sent?: number; subscribers?: number };
  } | null;
}

export interface EnginePosition {
  symbol: string;
  sector: string;
  exposure: number;
  weight: number | null;
  component_var: number | null;
  price: number | null;
  qty: number;
  marginal: number | null;
  standalone: number | null;
  risk_share: number | null;
  risk_over_money: number | null;
}

export interface EngineLimit {
  name: string;
  scope: string;
  unit: string;
  observed: number | null;
  threshold: number;
  excess: number | null;
  breached: boolean;
  utilisation: number | null;
}

export interface Snapshot {
  as_of: string;
  factor?: string;
  positions: EnginePosition[];
  sectors?: { sector: string; exposure: number; component_var: number | null; risk_share: number | null }[];
  gross_exposure: number | null;
  net_exposure?: number | null;
  equity?: number | null;
  current_drawdown?: number | null;
  historical_var?: number | null;
  expected_shortfall?: number | null;
  parametric_var?: number | null;
  parametric_var_ewma?: number | null;
  ewma_lambda?: number | null;
  value_at_risk_notional: number | null;
  expected_shortfall_notional?: number | null;
  portfolio_beta?: number | null;
  portfolio_gamma?: number | null;
  portfolio_vega?: number | null;
  vega_by_bucket?: Record<string, number>;
  diversification_ratio?: number | null;
  euler_residual?: number | null;
  quiet?: string[];
  warming_up: boolean;
  feed?: FeedHealth;
  limits?: EngineLimit[];
  unevaluated?: string[];
  nodes_recomputed: number;
  stabilizes?: number;
  nodes_recomputed_delta?: number | null;
  stabilizes_delta?: number | null;
}

export interface SnapshotOut extends Envelope {
  snapshot: Snapshot;
}

export interface History {
  points: number;
  capacity: number;
  appended: number;
  time: number[];
  gross: (number | null)[];
  net: (number | null)[];
  equity: (number | null)[];
  drawdown: (number | null)[];
  var_notional: (number | null)[];
  es_notional: (number | null)[];
}
export interface HistoryOut extends Envelope {
  history: History;
}

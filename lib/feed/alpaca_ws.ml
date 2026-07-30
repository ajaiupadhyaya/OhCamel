(* Phase 2.

   Alpaca WebSocket market data client. Parses ticks and pushes them into the
   Var.t cells created in graph.ml. This module's only job is to move the
   outside world into the graph's inputs -- it must not compute risk, and it
   must not read derived nodes.

   Reconnect/backoff is a requirement, not a nicety: feeds drop, and a graph
   whose inputs quietly stop updating still serves confident, stale numbers to
   the dashboard. Staleness needs to be visible -- a last-tick timestamp that
   the graph can reason about, so "no data" is distinguishable from "no change."

   Intentionally empty until Phase 2. *)

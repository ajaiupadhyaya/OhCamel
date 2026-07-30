(* Phase 3.

   HTTP/JSON layer exposing current graph state (positions, exposure, VaR, limit
   status), plus a push stream (SSE or WebSocket) for live updates.

   The stream should be driven by Incremental.Observer callbacks firing on
   change -- not by a timer that serializes the whole book every second. A
   polling transport bolted onto a reactive core would reintroduce exactly the
   staleness the engine exists to remove.

   Built on cohttp-async (stable) rather than dream (1.0.0~alpha) -- see the
   toolchain note in README.md.

   Intentionally empty until Phase 3. *)

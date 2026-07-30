(* Phase 2.

   FRED macro series (rates, etc.), feeding the rolling-beta / factor-exposure
   node.

   Polling is fine here and the README says so explicitly: these series update
   daily or slower, so a slow poll is matched to the data rather than papering
   over a design gap. The reactive requirement is about tick-driven quantities.

   Intentionally empty until Phase 2. *)

(* Link checks for dependencies that no real code exercises yet.

   This module started life in Phase 0 as a check on all four load-bearing
   libraries. Three of them no longer need it, because Phase 1 uses them for
   real and far harder than a smoke test could:

     core         used throughout
     incremental  graph.ml, and test_graph.ml counts its recomputations
     owl / BLAS   risk_metrics.portfolio_stddev goes through Mat.dot

   What remains is the part Phase 1 does NOT reach. Both are kept because the
   Phase 0 lesson on this machine was that linking, not calling, is where these
   libraries fail on macOS/ARM -- owl cost hours over an optimiser crash and a
   missing OpenMP symbol, and neither would have shown up as anything other
   than a link error. A ten-line check that fails at `make test` is much cheaper
   than discovering the same thing halfway through writing a feed.

   Delete each of these the moment real code covers it:
     - owl_linalg_inv_trace  when anything calls Owl.Linalg (risk attribution
                             and marginal VaR would; nothing does today)
     - async_peek            when Phase 2's feed starts the Async scheduler *)

(* Expect 1.5. Inverts diag(2,2,2) -> diag(0.5,0.5,0.5), trace 1.5. This is a
   LAPACK entry point, which is linked separately from the BLAS one that
   portfolio_stddev already proves. *)
let owl_linalg_inv_trace () =
  let scaled = Owl.Mat.mul_scalar (Owl.Mat.eye 3) 2.0 in
  Owl.Mat.trace (Owl.Linalg.D.inv scaled)

(* Expect Some 42. [peek] on an already-determined Deferred resolves without
   starting the Async scheduler, which keeps this usable from a unit test. *)
let async_peek () = Async.Deferred.peek (Async.Deferred.return 42)

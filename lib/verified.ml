(* What has been verified, and when. Dated constants, nothing computed.

   The page prints these as quoted, with [dated] beside them,
   because a test count and a coverage figure are facts about a moment and a
   machine, not about the running process -- the one number on the page that
   the process cannot vouch for by producing it. Keeping them here rather than
   in the HTML means the compiler sees them: test_ohcamel.ml asserts [tests]
   against the registry it runs, so the count cannot drift silently, and Phase
   5 serves them on /api/reports beside the numbers the process did compute.

   [coverage_pct] is the published rounding, not the quotient: 3,891 / 4,743
   is 82.037%, published as 82.0 -- README.md's badge rounds further to the
   whole percent (82%) and docs/status.md carries the one-decimal figure
   (82.0%), so a page that printed 82.037 would
   be claiming a precision `make coverage` did not measure (bisect_ppx
   counts visited points, not lines, and the number moves with the
   instrumentation). The fraction is kept beside it so the rounding is
   checkable. Re-measure both with `make coverage` and re-date whenever
   [tests] changes; a stale coverage under a fresh test count is the
   failure a single [dated] exists to prevent.

   The points are library [ohcamel]'s alone. desk/ has no instrumentation
   stanza yet, so the desk's tests raise this figure only where they reach
   the kernel, and it says nothing about the desk's own code. *)

let tests = 386

(* The scheduler suite's count: test/desk_async, the executable whose cases run
   with Async's scheduler on clocks of their own. It asserts this against its
   own registry, as test_ohcamel.ml asserts [tests], so neither count can
   drift. Counted apart because the main suite's promise is that it never
   starts the scheduler. Not served on /api/reports: the page's dated block
   prints [tests], and reports.ml names its keys one by one. *)
let scheduler_tests = 2
let coverage_covered = 3_891
let coverage_lines = 4_743
let coverage_pct = 82.0
let dated = "2026-09-13"

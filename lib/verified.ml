(* What has been verified, and when. Dated constants, nothing computed.

   The page prints these as quoted, with [dated] beside them,
   because a test count and a coverage figure are facts about a moment and a
   machine, not about the running process -- the one number on the page that
   the process cannot vouch for by producing it. Keeping them here rather than
   in the HTML means the compiler sees them: test_ohcamel.ml asserts [tests]
   against the registry it runs, so the count cannot drift silently, and Phase
   5 serves them on /api/reports beside the numbers the process did compute.

   [coverage_pct] is the published rounding, not the quotient: 7,643 / 9,618
   is 79.466%, published as 79.5 -- README.md's badge rounds further to the
   whole percent (79%) and docs/status.md carries the one-decimal figure
   (79.5%), so a page that printed 76.560 would
   be claiming a precision `make coverage` did not measure (bisect_ppx
   counts visited points, not lines, and the number moves with the
   instrumentation). The fraction is kept beside it so the rounding is
   checkable. Re-measure both with `make coverage` and re-date whenever
   [tests] changes; a stale coverage under a fresh test count is the
   failure a single [dated] exists to prevent.

   The points are both libraries': the kernel's in lib/ and the desk's in
   desk/, whose dune file declares the same instrumentation backend. The desk
   is named here by its directory, because CI's grep refuses the desk
   library's name anywhere in lib/. *)

let tests = 658

(* The scheduler suite's count: test/desk_async, the executable whose cases run
   with Async's scheduler on clocks of their own. It asserts this against its
   own registry, as test_ohcamel.ml asserts [tests], so neither count can
   drift. Counted apart because the main suite's promise is that it never
   starts the scheduler. Not served on /api/reports: the page's dated block
   prints [tests], and reports.ml names its keys one by one. *)
let scheduler_tests = 30

(* The coverage figures: dated and re-measured together, never adjusted
   alone -- see the comment above [tests] for why. *)
let coverage_covered = 7_643
let coverage_lines = 9_618
let coverage_pct = 79.5
let dated = "2026-09-19"

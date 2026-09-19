# Desk A2 — The Order Manager — Implementation Plan

> **Status (2026-09-19):** historical record of a finished, merged phase. Superseded as the active plan by `docs/superpowers/plans/2026-09-19-final-completion.md` ("the finish"); see `docs/superpowers/specs/2026-09-19-the-finish.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The desk places, tracks and reconciles paper orders — the rules, then the limits on a fork of the live graph, then the journal, then the venue — and every order, fill, rejection and cost is on the page with its reasons.

**Architecture:** Every decision is a pure function tested with hand-derived values in the main suite: the gate (`lib/gate.ml`, a fork of the graph), the rules, the order state machine (A1), the event mapping, submission classification, cost analysis and route protection. The order manager (`desk/oms.ml`) is a thin Async layer over them, tested in a second test executable that runs a scheduler against the simulated venue on a clock the test advances, so the main suite keeps its no-scheduler guarantee and no test waits on the wall clock. Alpaca paper gains its trading half behind the same interface. Five tasks come first and take what A1's final review handed over (Tasks 1-5): a venue id that arrives late, the journal's interface, reads ordered by number and raced against fills, the request bound on a synthetic clock, and the desk's code in the coverage figure.

**Tech Stack:** OCaml 5.2.1, Core/Async v0.17, Incremental, sqlite3 5.4.2, cohttp-async 5.3, websocket-async 2.17, yojson, alcotest, qcheck.

**Spec:** `docs/superpowers/specs/2026-09-12-the-desk-design.md` — §2 invariants 2, 6, 9, 10, 11, 12; §3.4 (the trading half), §3.6, §3.7, §3.8, §3.9, §3.11; §5 row A2. Builds on phase A1 as merged to main at `cf79764`, plus the docs-only deploy record `52e1fbe` (plan `docs/superpowers/plans/2026-09-12-desk-a1-record.md`; its final review, fix wave `4d12268` and re-review under `.superpowers/sdd/2026-09-12-desk-a1-record/`): `Ids`, `Order`, `Journal`, `Venue`, `Sim_venue`, `Alpaca_paper` (with `within` and `request_timeout`), `Book_sync`, `Session_close` (with `record_if_current` and `run_forever ~book_is_current`), `Desk` (with `~on_first_sync` and `book_is_current`), the server's `extension` seam. Every A1 name this plan uses was checked against that tree on 2026-09-13.

## Global Constraints

- Work in `/Users/ajaiupadhyaya/Documents/OhCamel` on branch `desk/a2-orders`, created from `main` at or after `cf79764` (A1 merged). `make build`, `make test`, `make fmt` enter the opam switch; raw dune needs `eval $(opam env --switch=$PWD --set-switch)` first.
- `dune build @fmt` clean before every commit (ocamlformat 0.29.0).
- Library `ohcamel` (`lib/`) never contains `ohcamel_desk`, `Sqlite3`, `paper-api.alpaca.markets` or `/v2/orders` -- in code or in a comment, because CI's grep reads both. `lib/gate.ml` is a read-only calculation on a fork and names none of them; a `lib/` comment that must mention the desk library says `desk/`.
- No code path can reach a live-money endpoint: the trading host is the constant in `desk/alpaca_paper.ml`; a trading key must begin `PK`.
- Journal before wire: an order is written as `pending_submit` before the request that submits it is sent. An unknown submission outcome is resolved only by looking the client order id up at the venue, never by sending the order again.
- Fills are facts: a fill is journaled even when the state machine calls it an anomaly; a position is set from the venue's `position_qty` when the venue gives one. A venue's order id is kept whenever it arrives, in any state an order lacks one (Task 1).
- Every order passes the rules and the gate before it reaches a venue. A proposal that creates or worsens a breach is rejected naming the limits; one that reduces a breach passes.
- An order is refused by the `trading` rule unless the desk's last applied read of the account is within two sync intervals (Tasks 14, 18). Until then the graph the gate forks may hold the book file's quantities and cash.
- Every failing rule is reported, not only the first.
- Every journal write goes through a function `desk/journal.mli` exports (Task 2). Only tests reach the handle, through `Journal.For_testing`.
- Every request the desk sends to either Alpaca host goes through `Alpaca_paper.within` (Tasks 4 and 11). Its bound closes a request that is still connecting or has answered. A peer that never sends its status line keeps its socket until the peer or the kernel ends it. A sequencer job's request is bounded at 10 s, the arrival quote at 2 s.
- The book's reads are ordered by the desk's own read numbers, never by wall-clock time, and a read that started before one of the desk's own fills is refused. At most one read that fills start is out at a time, with one more owed (Task 3).
- The main test suite never starts the Async scheduler and never runs its cycles. Tests that need a scheduler live in `test/desk_async/`, a separate test executable with its own runner (Task 4 creates it).
- No test waits on the wall clock. A case that needs time to pass creates a `Time_source` and advances it; production code that waits takes an optional `?time_source` whose default is the wall clock.
- Mutating routes answer 405 on the demo host. On the live host they require `X-OhCamel-Desk: 1` and either `Sec-Fetch-Site: same-origin` or an `Origin` whose host equals the `Host` header, or they answer 403.
- `lib/verified.ml`'s `let tests = N` equals the main registry's case count; bump it in the same commit. The count starts at 381 (A1, `cf79764`), and each task's step names the figure it reaches. The scheduler suite's cases are counted apart, in `let scheduler_tests = N`, which test/desk_async asserts against its own registry; bump it in the same commit that adds a case there (2 at Task 4, 4 at Task 14, 7 at Task 15). `/api/reports` serves `tests` alone.
- Coverage is measured over `lib/` and `desk/` together from Task 5 on. The CI floor (60%) reads that combined figure, and no task lowers it.
- Every numeric assertion carries its derivation beside it. Comments explain why, in the repository's voice.
- Commit exactly the files the task names (plus `lib/verified.ml`). Never `git add -A`/`git add .`. Never commit `notes.txt`, `claudecodehandoff.md`, `Competitive Market Behavior LLMs.pdf`, `book.sexp` or any `.env`.
- Commit subjects: lowercase area prefix, then the reason; every message ends with a blank line and `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Where `bin/main.ml` changes, the six credential-free modes print byte-for-byte what they printed before.

## File Structure

```
desk/order.ml                   MODIFY  a venue id kept whenever it arrives; an overfill's figure to every digit (Task 1)
desk/journal.mli                CREATE  the journal's interface; For_testing for the handle (Task 2)
desk/journal.ml                 MODIFY  a failed open closes its handle, WAL read back (Task 2); orders, events and fills (Task 9)
desk/desk.ml                    MODIFY  reads numbered, a raced read refused, after_fill one read at a time (Task 3); the frame and /api/desk carry orders, fills, costs, the switch (Task 17)
desk/alpaca_paper.ml            MODIFY  within ?time_source (Task 4); order JSON, submission classification, key accessors, request_json on cohttp's one-shot call, bounded and closed as A1's unchanged get_json is (Task 11)
desk/dune                       MODIFY  bisect_ppx instrumentation (Task 5); websocket, websocket-async (Task 11)
Makefile                        MODIFY  the coverage comment names both libraries (Task 5)
lib/gate.ml                     CREATE  the pre-trade gate on a fork
lib/alerts.ml                   MODIFY  on_trip: a callback when the switch trips; comments that something now obeys it
lib/graph.ml                    MODIFY  topology ?kill_switch_wired_to
lib/server.ml                   MODIFY  create ?kill_switch_wired_to
desk/rules.ml                   CREATE  the rules, every failure reported
desk/venue.ml                   MODIFY  Venue_order, Submission, Update, Trade
desk/sim_venue.ml               MODIFY  the trading half: submit, cancel, step, pump, trade
desk/tca.ml                     CREATE  cost per fill, summaries
desk/trade_updates.ml           CREATE  the trade_updates messages and session
desk/alpaca_trade.ml            CREATE  Alpaca paper's Venue.Trade.t
desk/halt.ml                    CREATE  the switch the desk obeys: a limit's trip or a hand's halt
desk/ticket.ml                  CREATE  a ticket, from JSON
desk/reconcile.ml               CREATE  what a restart learns from the venue, as events
desk/oms.ml                     CREATE  preview, propose, updates, cancel, kill, reconcile, refresh
desk/desk_routes.ml             CREATE  protection and the desk's routes
bin/main.ml                     MODIFY  both hosts attach the order manager; the demo's trader
deploy/smoke.sh                 MODIFY  routes; the demo refuses orders and previews
web/index.html, web/dashboard.js, web/page.css   MODIFY  switch, ticket, blotter, fills; the sentences A2 makes untrue
test/test_desk_order.ml, test/test_journal.ml, test/test_session_close.ml,
  test/test_alpaca_paper.ml                                   MODIFY (Tasks 1-4)
test/desk_async/dune, test/desk_async/test_desk_async.ml      CREATE in Task 4 (scheduler suite, on a clock each case advances); MODIFY in Tasks 14-15
test/test_gate.ml, test_rules.ml, test_sim_trade.ml, test_journal_orders.ml, test_tca.ml,
  test_alpaca_trading.ml, test_halt.ml, test_ticket.ml, test_reconcile.ml, test_oms.ml,
  test_desk_routes.ml                                         CREATE (main suite)
test/test_ohcamel.ml, lib/verified.ml, test/test_server.ml, test/test_desk.ml   MODIFY
README.md, docs/overview.md, docs/status.md, web/index.html   MODIFY (Tasks 4, 5, 20)
```

---

### Task 1: The order keeps a late venue id, and an anomaly's exact figure

A1's final review, M3 and its ledger lines 43 and 44, taken before any order is written.

**Files:**
- Modify: `desk/order.ml`, `test/test_desk_order.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: A1's `Order` (desk/order.ml): `State`, `Event`, `Anomaly`, `apply`, `illegal`, the record's `venue_order_id`.
- Produces (in `Ohcamel_desk.Order`; no signature changes):
  - `apply o (Acknowledged id)` and `apply o (Found id)` move `Pending_submit` and `Submit_unknown` to `Submitted` with the id, as before. `Found` in `Pending_submit` becomes legal: a venue that has the client id has answered.
  - In every other state:
    - an order with no venue id takes this one;
    - the same id again changes nothing, with no anomaly;
    - a different id is `Illegal`, and the first id stands;
    - `Failed` takes the id and also returns `Illegal`, because the venue has an order the lookups declared never received;
    - `Rejected_pre_trade` takes nothing and returns `Illegal`, because that order never left the desk.
  - `Anomaly.to_string` prints an overfill's `filled` with `%.17g`, so the journal's text (Task 9 writes it) round-trips the float.

- [ ] **Step 1: Write the failing tests.** In `test/test_desk_order.ml`, add above `prop_no_fill_is_ever_lost`:

```ocaml
(* The trade-updates stream can deliver a fill before the desk has read the
   POST's 200. The order has no venue id when the fill lands, and the 200's id
   must still be kept: the venue cancels by it. *)
let test_an_acknowledgement_that_arrives_after_a_fill_keeps_the_venue's_id () =
  let partial, anomalies =
    run (Order.create (request ())) [ fill "x1" 40.0 100.0; Order.Event.Acknowledged "venue-1" ]
  in
  (* 40 of 100 filled before the 200 was read *)
  Alcotest.check state "partially filled" Order.State.Partially_filled partial.Order.state;
  Alcotest.(check (option string)) "the 200's id, kept" (Some "venue-1") partial.Order.venue_order_id;
  Alcotest.(check int) "no anomaly: the race is ordinary" 0 (List.length anomalies);
  let filled, anomalies =
    run (Order.create (request ~qty:10 ())) [ fill "x1" 10.0 100.0; Order.Event.Acknowledged "venue-2" ]
  in
  (* 10 of 10 filled before the 200 was read *)
  Alcotest.check state "filled" Order.State.Filled filled.Order.state;
  Alcotest.(check (option string)) "kept after the order completed" (Some "venue-2") filled.Order.venue_order_id;
  Alcotest.(check int) "no anomaly" 0 (List.length anomalies);
  let _, anomalies = run partial [ Order.Event.Acknowledged "venue-1" ] in
  Alcotest.(check int) "the same id again changes nothing" 0 (List.length anomalies);
  let kept, anomalies = run partial [ Order.Event.Found "venue-7" ] in
  Alcotest.(check (option string)) "a different id does not replace it" (Some "venue-1") kept.Order.venue_order_id;
  Alcotest.(check (list string))
    "and is named" [ "illegal: found in partially_filled" ]
    (List.map anomalies ~f:Order.Anomaly.to_string)

(* A reconciliation's lookup races the stream. The order was unknown; the
   stream moved it on before the lookup's answer was applied -- an acceptance
   in one case, a fill in the other -- and the lookup's id must still be kept.
   The one state where a found id is a contradiction is failed: the lookups
   had declared the order never received. The id is kept there too, because
   it is how a person finds that order at the venue, and the contradiction is
   named beside it. An order refused before the wire takes no venue's id. *)
let test_a_lookup_that_lands_after_the_stream_keeps_the_venue's_id () =
  let unknown = [ Order.Event.Outcome_unknown "timed out" ] in
  let accepted, anomalies =
    run (Order.create (request ())) (unknown @ [ Order.Event.Venue_accepted; Order.Event.Found "venue-9" ])
  in
  Alcotest.check state "accepted" Order.State.Accepted accepted.Order.state;
  Alcotest.(check (option string)) "the lookup's id, kept" (Some "venue-9") accepted.Order.venue_order_id;
  Alcotest.(check int) "no anomaly" 0 (List.length anomalies);
  let partial, anomalies =
    run (Order.create (request ())) (unknown @ [ fill "x1" 30.0 99.0; Order.Event.Found "venue-9" ])
  in
  (* 30 of 100 filled while the lookup was out *)
  Alcotest.check state "partially filled" Order.State.Partially_filled partial.Order.state;
  Alcotest.(check (option string)) "kept after a fill too" (Some "venue-9") partial.Order.venue_order_id;
  Alcotest.(check int) "no anomaly" 0 (List.length anomalies);
  let failed, anomalies =
    run (Order.create (request ())) (unknown @ [ Order.Event.Not_found; Order.Event.Found "venue-9" ])
  in
  Alcotest.check state "still failed" Order.State.Failed failed.Order.state;
  Alcotest.(check (option string)) "the id is kept" (Some "venue-9") failed.Order.venue_order_id;
  Alcotest.(check (list string))
    "and the contradiction named" [ "illegal: found in failed" ]
    (List.map anomalies ~f:Order.Anomaly.to_string);
  let refused, anomalies =
    run (Order.create (request ())) [ Order.Event.Pre_trade_rejected [ "universe: TSLA" ]; Order.Event.Acknowledged "venue-9" ]
  in
  Alcotest.(check (option string)) "refused before the wire: no id" None refused.Order.venue_order_id;
  Alcotest.(check (list string))
    "named" [ "illegal: acknowledged in rejected_pre_trade" ]
    (List.map anomalies ~f:Order.Anomaly.to_string)

(* The journal is the record, so an anomaly's figure is written to round-trip,
   not to be short. 600,000 then 634,567.5 against 1,000,000 ordered: the
   second fill takes a partially filled order past its quantity, to
   1,234,567.5, exact in binary (a half-integer far below 2^53). "%g" keeps
   six significant digits and writes 1.23457e+06; "%.17g" writes every digit
   the float has and drops trailing zeros as "%g" does, so the existing case's
   12 still prints as 12. *)
let test_an_overfill's_figure_is_written_exactly () =
  let _, anomalies =
    run
      (Order.create (request ~qty:1_000_000 ()))
      [ Order.Event.Acknowledged "v"; fill "x1" 600_000.0 100.0; fill "x2" 634_567.5 100.0 ]
  in
  Alcotest.(check (list string))
    "every digit" [ "overfill: 1234567.5 filled against 1000000 ordered" ]
    (List.map anomalies ~f:Order.Anomaly.to_string)
```

  Replace `prop_no_fill_is_ever_lost` (its comment and the whole value) with the widened property:

```ocaml
(* Fills are facts, and so is a venue's id. Whatever else happens, the filled
   quantity is the sum of the distinct fills applied. And an order once told
   its venue id -- by an acknowledgement or a lookup, in any state but one
   refused before the wire -- keeps one. Events are drawn at random, including
   the refusals and the lookups that the races of a stream and a
   reconciliation are made of. Fills carry distinct execution ids, so no case
   is a replay. *)
let prop_no_fill_and_no_venue_id_is_ever_lost =
  let open QCheck in
  let event_gen =
    Gen.(
      oneof_weighted
        [
          (3, map (fun q -> `Fill (float_of_int (q + 1))) (int_bound 50));
          (1, return (`Other Order.Event.Venue_accepted));
          (1, return (`Other Order.Event.Cancel_requested));
          (1, return (`Other Order.Event.Venue_cancelled));
          (1, return (`Other Order.Event.Venue_expired));
          (1, return (`Other (Order.Event.Acknowledged "v")));
          (1, return (`Other (Order.Event.Found "v")));
          (1, return (`Other (Order.Event.Outcome_unknown "t")));
          (1, return (`Other Order.Event.Not_found));
          (1, return (`Other (Order.Event.Pre_trade_rejected [ "a rule" ])));
          (1, return (`Other (Order.Event.Venue_rejected_submission "403")));
          (1, return (`Other (Order.Event.Venue_rejected "halted")));
        ])
  in
  QCheck_alcotest.to_alcotest
    (Test.make ~name:"no fill and no venue id is ever lost, in any order of events" ~count:300
       (make Gen.(list_size (int_bound 30) event_gen))
       (fun events ->
         let _, o, total, told =
           List.fold events
             ~init:(0, Order.create (request ()), 0.0, false)
             ~f:(fun (n, o, total, told) e ->
               match e with
               | `Fill q -> (n + 1, fst (Order.apply o (fill (Printf.sprintf "x%d" n) q 10.0)), total +. q, told)
               | `Other ev ->
                   let tells =
                     match ev with
                     | Order.Event.Acknowledged _ | Order.Event.Found _ ->
                         not (Order.State.equal o.Order.state Order.State.Rejected_pre_trade)
                     | _ -> false
                   in
                   (n, fst (Order.apply o ev), total, told || tells))
         in
         Float.( = ) o.Order.filled_qty total && ((not told) || Option.is_some o.Order.venue_order_id)))
```

  In `suite`, add after the case "the twelve states round-trip and six are terminal":

```ocaml
      Alcotest.test_case "an acknowledgement that arrives after a fill keeps the venue's id" `Quick
        test_an_acknowledgement_that_arrives_after_a_fill_keeps_the_venue's_id;
      Alcotest.test_case "a lookup that lands after the stream keeps the venue's id" `Quick
        test_a_lookup_that_lands_after_the_stream_keeps_the_venue's_id;
      Alcotest.test_case "an overfill's figure is written exactly" `Quick test_an_overfill's_figure_is_written_exactly;
```

  and replace `prop_no_fill_is_ever_lost;` with `prop_no_fill_and_no_venue_id_is_ever_lost;`.

- [ ] **Step 2: Run and watch it fail.** `make test 2>&1 | grep -E 'FAIL|Expected|Received' | head -20`. Expected failures:
  - the two race cases at "the 200's id, kept" and "the lookup's id, kept" (`None` received);
  - the figure case, receiving `overfill: 1.23457e+06 filled against 1000000 ordered`;
  - the property, with a counterexample such as a fill followed by an acknowledgement;
  - the count case, 381 against 384.

- [ ] **Step 3: Implement.** In `desk/order.ml`:
  1. In `Anomaly.to_string`, change the overfill line's `"overfill: %g filled against %d ordered"` to `"overfill: %.17g filled against %d ordered"`, with this comment above the function:

```ocaml
  (* The journal writes this text (order_events.anomaly), and the journal is
     the record: an overfill's figure is printed with every digit its float
     has, not the six "%g" keeps. "%.17g" still drops trailing zeros, so a
     whole number of shares prints as one. *)
```

  2. Directly after `let illegal t event = ...`, add:

```ocaml
(* A venue's id for an order, arriving after the order has moved on. The
   trade-updates stream can deliver a fill before the desk reads the POST's
   200, and a reconciliation's lookup can land after the stream accepted or
   filled the order. The venue cancels by this id, so it is kept whatever
   state the order reached, and a race this ordinary is no anomaly. Three
   answers are contradictions and say so:
   - a different id from the one the order holds;
   - any id for an order refused before the wire, which no venue has;
   - an id for an order the lookups declared failed. That id is kept anyway,
     because it is how a person finds the order the venue does have. *)
let keep_venue_id (t : t) (event : Event.t) (id : string) : t * Anomaly.t option =
  match (t.state, t.venue_order_id) with
  | State.Rejected_pre_trade, _ -> illegal t event
  | _, Some known when String.equal known id -> (t, None)
  | _, Some _ -> illegal t event
  | State.Failed, None -> ({ t with venue_order_id = Some id }, snd (illegal t event))
  | _, None -> ({ t with venue_order_id = Some id }, None)
```

  3. In `apply`, replace these four arms:

```ocaml
  | (State.Pending_submit | State.Submit_unknown), Event.Acknowledged id ->
      ({ t with state = State.Submitted; venue_order_id = Some id }, None)
  | (State.Submitted | State.Accepted), Event.Acknowledged _ -> (t, None)
```

```ocaml
  | State.Submit_unknown, Event.Found id ->
      ({ t with state = State.Submitted; venue_order_id = Some id }, None)
  | State.Submitted, Event.Found _ -> (t, None)
```

  with these two, placed where the first pair was (the `Venue_rejected_submission`, `Outcome_unknown` and `Not_found` arms stay as they are):

```ocaml
  | (State.Pending_submit | State.Submit_unknown), (Event.Acknowledged id | Event.Found id) ->
      ({ t with state = State.Submitted; venue_order_id = Some id }, None)
  | _, (Event.Acknowledged id | Event.Found id) -> keep_venue_id t event id
```

  The module's header says `Submit_unknown` is left only through `Found`, `Not_found` or a venue event, and that still holds. A `Found` in `Pending_submit` now settles the order the same way: the venue has the client id, which is the answer invariant 10 waits for. Reconciliation (Task 13) still turns a pending order into an unknown before its lookup.

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 384` (381 + the three cases; the property was replaced, not added).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | tail -3
git add desk/order.ml test/test_desk_order.ml lib/verified.ml
git commit -F - <<'EOF'
desk: an order keeps a venue id that arrives after a fill or after the stream moved it on, and an overfill's figure is written to every digit, because the venue cancels by that id and the journal is the record

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 2: The journal's interface

A1's final review, M8, with M1 and M2 (both in `desk/journal.ml`), before Task 9 adds the first order write.

**Files:**
- Create: `desk/journal.mli`
- Modify: `desk/journal.ml`, `test/test_journal.ml`, `test/test_session_close.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: A1's `Journal` at `cf79764` (desk/journal.ml), and every use of it outside the file. `grep -rhoE 'Journal\.[A-Za-z_]+' desk bin test | sort -u` lists what those uses need. Only `db`, `write` and `run`, all from the two tests, are not public below.
- Produces:
  - `desk/journal.mli`: `t` abstract; `schema_version`; `open_`, `close`, `location`, `version`; `Session`, `record_session`, `sessions`, `session_count`, `recent_sessions`, `session`; `Mark`, `record_marks`, `marks`; `Forecast`, `record_forecasts`, `forecasts`, `latest_forecasts`; `Alert`, `record_alert`, `recent_alerts`; and `For_testing` with `db : t -> Sqlite3.db`, `write`, `run` and `journal_mode : t -> string`.
  - `open_` closes its handle on every failure after `Sqlite3.db_open` (M1). It reads `PRAGMA journal_mode=WAL`'s answer back and refuses a file journal that is not `wal` (M2). Both refusals keep A1's `Error` shape.

- [ ] **Step 1: Write the failing tests.**
  1. In `test/test_journal.ml`, in `test_a_failed_commit_rolls_back_before_the_next_write`, replace each `j.Journal.db` with `Journal.For_testing.db j` (three places), `Journal.write` with `Journal.For_testing.write`, and `Journal.run` with `Journal.For_testing.run`.
  2. In `test/test_session_close.ml`, replace both `ja.Journal.db` with `Journal.For_testing.db ja`.
  3. In `test/test_journal.ml`, above `let suite`, add:

```ocaml
(* Spec §3.5: a file journal runs in WAL mode. SQLite answers PRAGMA
   journal_mode with the mode it actually set, and open_ now refuses any
   answer but wal; this pins the answer on a real file. An in-memory database
   has no file for WAL to mean anything, and SQLite reports it as "memory". *)
let test_a_file_journal_runs_in_wal_mode () =
  with_temp_path ~f:(fun path ->
      let j = open_exn path in
      Alcotest.(check string) "a file: wal, as SQLite reports it" "wal" (Journal.For_testing.journal_mode j);
      Journal.close j);
  let m = open_exn ":memory:" in
  Alcotest.(check string) "in memory: memory" "memory" (Journal.For_testing.journal_mode m);
  Journal.close m
```

  and register it in `suite`: `Alcotest.test_case "a file journal runs in WAL mode" `Quick test_a_file_journal_runs_in_wal_mode;`.

- [ ] **Step 2: Run and watch it fail.** `make test 2>&1 | grep -E 'Error|Unbound' | head -5`. Expected: `Unbound module Journal.For_testing`.

- [ ] **Step 3: Implement.**
  1. `desk/journal.mli`:

```ocaml
(* The journal's interface: what the rest of the desk may do to the record.

   Every write goes through a function here, and so through one transaction
   and one step of the version counter. Invariant 10 -- the journal before the
   wire -- is a promise about order writes, and it holds only if no caller can
   reach the database around them. Before this file a caller could, through
   the record's [db] field (A1's final review, M8). The two tests that need
   the handle itself reach it through [For_testing], whose name says what it
   is for. *)

open Core
open Ohcamel.Types

type t

val schema_version : int

(** Opens, or creates, the journal at [path] (":memory:" for one that ends
    with the process). An error names the file and what failed, and leaves no
    handle open behind it. A file journal that SQLite would not put in WAL mode
    is refused. *)
val open_ : path:string -> t Or_error.t

val close : t -> unit
val location : t -> string

(** Moves once per committed transaction: the page's signal that the record
    changed. *)
val version : t -> int

module Session : sig
  type t = {
    date : Date.t;
    equity_close : float;
    cash_close : float;
    gross_close : float;
    net_close : float;
    recorded_at : Time_ns.t;
  }
  [@@deriving sexp_of, compare, equal]
end

val record_session : t -> Session.t -> unit
val sessions : t -> Session.t list
val session_count : t -> int

(** The newest [limit], oldest first. *)
val recent_sessions : t -> limit:int -> Session.t list

val session : t -> Date.t -> Session.t option

module Mark : sig
  type t = { date : Date.t; symbol : Symbol.t; close : float; qty : float }
  [@@deriving sexp_of, compare, equal]
end

val record_marks : t -> Mark.t list -> unit
val marks : t -> Date.t -> Mark.t list

module Forecast : sig
  type t = {
    date : Date.t;
    estimator : string;
    confidence : float;
    var_fraction : float option;
    var_notional : float option;
    es_notional : float option;
  }
  [@@deriving sexp_of, compare, equal]
end

val record_forecasts : t -> Forecast.t list -> unit
val forecasts : t -> Forecast.t list

(** The latest date's forecasts, by estimator. *)
val latest_forecasts : t -> Forecast.t list

module Alert : sig
  type t = { at : Time_ns.t; kind : string; limit_name : string; line : string }
  [@@deriving sexp_of, compare, equal]
end

val record_alert : t -> Alert.t -> unit
val recent_alerts : t -> limit:int -> Alert.t list

module For_testing : sig
  (** The raw handle, for a test that must make SQLite fail on purpose -- a
      trigger, a deferred foreign key. Nothing in desk/ or bin/ calls it. *)
  val db : t -> Sqlite3.db

  val write : t -> what:string -> (unit -> unit) -> unit
  val run : t -> what:string -> string -> Sqlite3.Data.t list -> unit

  (** SQLite's answer to PRAGMA journal_mode: "wal" for a file journal,
      "memory" for ":memory:". *)
  val journal_mode : t -> string
end
```

  (`Session.t` and `Alert.t` derive against `Time_ns.Alternate_sexp` in the `.ml`, which is the same type as `Time_ns.t`. A deriving attribute in a signature only declares `sexp_of_t`, `compare` and `equal`, so the poisoned `Time_ns.sexp_of_t` is never referenced here.)

  2. In `desk/journal.ml`, replace `let open_ ~(path : string) : t Or_error.t = ...` (the whole function) with:

```ocaml
(* Everything [open_] does once the handle exists. Each refusal raises, and
   [open_] closes the handle.

   A second connection to the same file (sqlite3 .backup, a person reading
   it) waits up to five seconds instead of failing at once. WAL lets a reader
   and the one writer proceed together; an in-memory database has no file for
   either to mean anything.

   SQLite answers PRAGMA journal_mode=WAL with the mode it actually set. On a
   filesystem without shared memory it stays in its rollback journal and says
   so. A journal that only asked would run on unnoticed, and a .backup reader
   could then block the one writer (A1's final review, M2). *)
let set_up t ~path =
  Sqlite3.busy_timeout t.db 5_000;
  if not (String.equal path ":memory:") then (
    (match query t ~what:"journal_mode" "PRAGMA journal_mode=WAL" [] ~row:(fun r -> col_text r 0) with
    | [ mode ] when String.equal (String.lowercase mode) "wal" -> ()
    | modes ->
        failwithf "journal: %s answered journal_mode %s, not wal; this journal runs only in WAL mode" path
          (String.concat ~sep:"," modes) ());
    exec t ~what:"synchronous" "PRAGMA synchronous=NORMAL");
  exec t ~what:"meta" (List.hd_exn schema);
  (match
     query t ~what:"schema version" "SELECT value FROM meta WHERE key = 'schema_version'" [] ~row:(fun r ->
         col_text r 0)
   with
  | [] -> ()
  | [ v ] when String.equal v (Int.to_string schema_version) -> ()
  | [ v ] ->
      failwithf
        "journal: %s is at schema version %s and this build knows version %d; it will not guess at a layout it \
         has never seen"
        path v schema_version ()
  | _ -> failwith "journal: meta holds more than one schema version");
  List.iter (List.tl_exn schema) ~f:(exec t ~what:"schema");
  run t ~what:"schema version" "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)"
    [ text (Int.to_string schema_version) ]

(* A refusal after the handle opened closes it, once, here. Before, only the
   two schema-version refusals did, and a failed pragma, a file that is not a
   database, or a failed insert left the handle open behind the error (A1's
   final review, M1): a caller that tried again would leak one per try. The
   schema-version branches no longer close the handle themselves, so it is
   never closed twice. *)
let open_ ~(path : string) : t Or_error.t =
  Or_error.try_with (fun () ->
      let t = { db = Sqlite3.db_open path; location = path; version = 0 } in
      match set_up t ~path with
      | () -> t
      | exception e ->
          ignore (Sqlite3.db_close t.db : bool);
          raise e)
```

  3. At the end of `desk/journal.ml`:

```ocaml
module For_testing = struct
  let db t = t.db
  let write = write
  let run = run

  let journal_mode t =
    match query t ~what:"journal_mode" "PRAGMA journal_mode" [] ~row:(fun r -> col_text r 0) with
    | [ mode ] -> mode
    | _ -> failwith "journal: PRAGMA journal_mode did not answer with one row"
end
```

  4. Check that nothing else reached the record: `grep -rn 'Journal\.db' desk bin test` prints nothing. The two tests now say `Journal.For_testing.db`, which that pattern does not match.

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 385` (384 + 1).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | tail -3
git add desk/journal.mli desk/journal.ml test/test_journal.ml test/test_session_close.ml lib/verified.ml
git commit -F - <<'EOF'
desk: the journal has an interface, so every write goes through its transaction and its counter, a failed open closes its handle and a file journal that did not enter WAL is refused, because invariant 10 rests on order writes having one door

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 3: The book's reads in order, and a fill's race

A1's ledger line 128 and final review M4 (the desk's half), with re-review residual 8. All three are in `desk/desk.ml`, and all come before Task 14 makes positions follow fills.

**Files:**
- Modify: `desk/desk.ml`, `test/test_desk.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: A1's `Desk` (desk/desk.ml at `cf79764`): `create ~graph ~journal ~venue ~spec ~on_change ~on_first_sync`, `sync_with ~account ~positions ~at`, `sync`, `sync_forever`, `book_is_current ~now ~within`, `summary_json`, `body_json`; `Book_sync.plan`/`apply`; `Graph.apply_fill`.
- Produces (in `Ohcamel_desk.Desk`):
  - `start_read : t -> int` returns this desk's next read number, starting from 1. `sync` takes one when its read starts.
  - `sync_with : t -> account:Venue.Account.t Or_error.t -> positions:Venue.Position.t list Or_error.t -> at:Time_ns.t -> seq:int -> unit` applies nothing, and changes no error, unless `seq` is greater than two numbers: the last applied read's, and every read number taken before the desk's latest fill. `at` is still what `last_sync` holds and what `book_is_current` measures.
  - `fill_applied : t -> unit` marks every read already started as older than the book.
  - `after_fill : t -> unit` runs `fill_applied`, then starts a fresh `sync` without awaiting it, unless a read it started is still out. In that case it owes one more read, which starts when the first answers. So at most one `after_fill` read is in flight, with one owed, however many fills land; the read that finally applies still started after the last fill. The order manager calls it through its `after_fill` callback: Task 14 defines the callback and Task 18 wires `Desk.after_fill desk` into it.

  Where the correction lives (M4): positions follow the desk's own fills in the order manager's `apply_to_graph` (Task 14: `Graph.apply_fill`, then the venue's `position_qty`). The account read that corrects them is the one `after_fill` starts. A read that was already out when the fill landed is refused here, in `sync_with`, so it cannot undo the fill for a minute.

- [ ] **Step 1: Write the failing tests.** In `test/test_desk.ml`:
  1. Every existing `Desk.sync_with` call gains `~seq`, in the order the reads start:
     - `test_a_sync_sets_the_book_and_names_what_it_cannot_hold`: `~at ~seq:1`.
     - `test_a_failed_read_keeps_the_last_good_account_and_says_what_failed`: the first read `~at ~seq:1`, the failed read `~at ~seq:2`.
     - `test_a_read_that_started_before_the_last_applied_one_changes_nothing`: the newer read at 14:01 is `~at:newer ~seq:2`, and the older read at 14:00 is `~at ~seq:1`. Change its comments to say the older read is read 1, numbered before read 2, the last applied.
     - `test_the_trail_is_restored_at_the_first_applied_read_and_only_then`: its helper becomes `let read ~at ~seq = Desk.sync_with desk ~account:... ~positions:... ~at ~seq in`, called with `~seq:1`, `~seq:2` and `~seq:3` in the order it already calls it.
     - `test_a_close_with_no_current_book_records_nothing`: `~at ~seq:1`.
  2. Above `let suite`, add:

```ocaml
(* A1's ledger, line 128. The guard ordered reads by wall-clock time, so a
   clock stepped back by more than a sync interval -- an NTP correction, a
   resumed VM -- refused every read until the clock passed the old stamp, with
   the book frozen and nothing logged. Reads are numbered now, and read 2 is
   the newer read whatever its stamp says. Started after the clock stepped
   back two minutes, its stamp of 13:59 is before read 1's 14:01, and it
   applies. *)
let test_a_newer_read_applies_after_the_clock_steps_back () =
  with_desk
    ~f:(fun desk graph _ ->
      let first = Desk.start_read desk in
      (* 14:00:00 + 60 s = 14:01:00 *)
      Desk.sync_with desk
        ~account:(Ok (account ~cash:80_000.0 ~equity:83_000.0))
        ~positions:(Ok [ position aapl 20.0 ])
        ~at:(Time_ns.add at (Time_ns.Span.of_sec 60.0))
        ~seq:first;
      let second = Desk.start_read desk in
      (* 14:00:00 - 60 s = 13:59:00 *)
      Desk.sync_with desk
        ~account:(Ok (account ~cash:90_000.0 ~equity:91_500.0))
        ~positions:(Ok [ position aapl 10.0 ])
        ~at:(Time_ns.sub at (Time_ns.Span.of_sec 60.0))
        ~seq:second;
      Alcotest.(check (list int)) "numbered 1 and 2, in the order they started" [ 1; 2 ] [ first; second ];
      Alcotest.(check (float 0.0)) "AAPL is read 2's 10" 10.0 (Qty.to_float (Graph.qty graph aapl));
      let s = Desk.summary_json desk in
      Alcotest.(check (float 1e-9)) "cash is read 2's 90,000" 90_000.0 (Yojson.Safe.Util.to_number (field s "cash"));
      Alcotest.(check string)
        "last_sync is read 2's stamp, 13:59:00" "2026-09-14T13:59:00.000000000Z"
        (Yojson.Safe.Util.to_string (field s "last_sync")))
    ()

(* A1's final review, M4: a read that raced one of the desk's own fills. The
   book holds AAPL 0 and 100,000 of cash from read 1. Read 2 starts; the
   desk's fill of 10 AAPL at 150 lands while it is out; read 2 then answers
   with the account from before the fill. Applied, it would put AAPL back to 0
   and cash back to 100,000, leaving the book ten shares short and 1,500 of
   cash over until the next minute's read. It is refused, and read 3, started
   after the fill, applies. *)
let test_a_read_that_raced_a_fill_changes_nothing_and_the_next_one_applies () =
  with_desk
    ~f:(fun desk graph _ ->
      let first = Desk.start_read desk in
      Desk.sync_with desk ~account:(Ok (account ~cash:100_000.0 ~equity:100_000.0)) ~positions:(Ok []) ~at ~seq:first;
      let raced = Desk.start_read desk in
      Graph.apply_fill graph { Fill.symbol = aapl; qty = Qty.of_float 10.0; price = Price.of_float 150.0; time = Time.epoch };
      Graph.stabilize graph;
      Desk.fill_applied desk;
      (* read 2 answers at 14:00:30 with the account from before the fill *)
      Desk.sync_with desk
        ~account:(Ok (account ~cash:100_000.0 ~equity:100_000.0))
        ~positions:(Ok [])
        ~at:(Time_ns.add at (Time_ns.Span.of_sec 30.0))
        ~seq:raced;
      Alcotest.(check (float 0.0)) "AAPL is the fill's 10" 10.0 (Qty.to_float (Graph.qty graph aapl));
      (* 100,000 - 10 x 150 = 98,500 *)
      Alcotest.(check (float 1e-9)) "cash is the fill's 98,500" 98_500.0 (Notional.to_float (Graph.cash graph));
      Alcotest.(check string)
        "last_sync is still read 1's 14:00:00" "2026-09-14T14:00:00.000000000Z"
        (Yojson.Safe.Util.to_string (field (Desk.summary_json desk) "last_sync"));
      let after = Desk.start_read desk in
      (* read 3, at 14:00:45, sees the fill: cash 98,500, equity 98,500 + 10 x 150 = 100,000 *)
      Desk.sync_with desk
        ~account:(Ok (account ~cash:98_500.0 ~equity:100_000.0))
        ~positions:(Ok [ position aapl 10.0 ])
        ~at:(Time_ns.add at (Time_ns.Span.of_sec 45.0))
        ~seq:after;
      Alcotest.(check string)
        "read 3 applies: last_sync 14:00:45" "2026-09-14T14:00:45.000000000Z"
        (Yojson.Safe.Util.to_string (field (Desk.summary_json desk) "last_sync"));
      (* graph 98,500 + 10 x 150 + MSFT 0 x 300 = 100,000; venue 100,000 *)
      Alcotest.(check (float 1e-9))
        "and the ledgers agree" 0.0
        (Yojson.Safe.Util.to_number (field (Desk.body_json desk) "equity_gap")))
    ()
```

  and register both in `suite`:

```ocaml
      Alcotest.test_case "a newer read applies after the clock steps back" `Quick
        test_a_newer_read_applies_after_the_clock_steps_back;
      Alcotest.test_case "a read that raced a fill changes nothing, and the next one applies" `Quick
        test_a_read_that_raced_a_fill_changes_nothing_and_the_next_one_applies;
```

  (`Fill` is `Ohcamel.Types.Fill`, the kernel's signed fill, and `Time.epoch` is `Ohcamel.Types.Time.epoch`; test_desk.ml already opens `Ohcamel.Types`.)

- [ ] **Step 2: Run and watch it fail.** `make test 2>&1 | grep -E 'Error|Unbound|label' | head -5`. Expected: `Unbound value Desk.start_read`, or `The function applied to this argument has type ... It is applied to too many arguments` at the first `~seq`.

- [ ] **Step 3: Implement.** In `desk/desk.ml`:
  1. In `type t`, after `mutable last_error : string option;`, add:

```ocaml
  (* Reads are numbered as they start. [applied_seq] is the number of the read
     the book came from; [raced_through] is the highest number taken before
     the desk's latest fill. Zero is "none". *)
  mutable reads_started : int;
  mutable applied_seq : int;
  mutable raced_through : int;
  (* The read [after_fill] starts: none out, one out, or one out and one more
     owed, because a fill landed while it was out. *)
  mutable fill_read : [ `Idle | `Running | `Owed ];
```

  and in `create`, after `last_error = None;`, add ``reads_started = 0; applied_seq = 0; raced_through = 0; fill_read = `Idle;``.
  2. Replace `let sync_with t ~account ~positions ~at = ...` (the whole function) with:

```ocaml
(* A read's place in the order this desk started its reads. The order is by
   number and not by [at], because the wall clock can step backwards -- an
   NTP correction, a resumed VM -- and a guard on time would then refuse every
   read until the clock passed the old stamp, silently, with the book frozen
   (A1's ledger, line 128). *)
let start_read t =
  t.reads_started <- t.reads_started + 1;
  t.reads_started

(* The desk's own fill has moved the graph's book: the order manager applied
   it and set the venue's position_qty, so positions follow fills. A read
   that started before the fill carries the account from before it -- cash
   without the trade, or quantities with it beside cash without -- and
   applying it would move the book backwards by the fill's notional until the
   next read (A1's final review, M4). So every read already started is
   refused, and the read [after_fill] starts is the correction. *)
let fill_applied t = t.raced_through <- t.reads_started

let sync_with t ~account ~positions ~at ~seq =
  if seq <= t.applied_seq || seq <= t.raced_through then
    (* Older than what the book already holds: a read started before the last
       applied one, or before one of the desk's own fills. *)
    ()
  else (
    (match (account, positions) with
    | Ok account, Ok positions ->
        let plan = Book_sync.plan ~universe:(Graph.symbols t.graph) ~positions ~account in
        Book_sync.apply t.graph plan;
        t.account <- Some account;
        (* Design §3.3's own definition of "managed": a name in the book's
           universe. Read here rather than recovered from [plan.unmanaged] by
           subtraction -- that would agree with [plan] only because
           [Book_sync.plan] happens to build [unmanaged] as an untransformed
           filter of [positions], which is Book_sync's implementation and not a
           fact desk.ml is entitled to lean on. [t.unmanaged] still comes from
           the plan, because Book_sync owns that decision; the two now agree by
           definition, not by construction. *)
        let universe = Symbol.Set.of_list (Graph.symbols t.graph) in
        t.unmanaged <- plan.Book_sync.Plan.unmanaged;
        t.positions <- List.filter positions ~f:(fun p -> Set.mem universe p.Venue.Position.symbol);
        t.equity_gap <-
          Some (Notional.to_float (Graph.equity t.graph) -. Notional.to_float account.Venue.Account.equity);
        t.last_sync <- Some at;
        t.applied_seq <- seq;
        t.last_error <- None;
        (* The first moment the graph's book is the account's, and so the
           first moment the journal's closes -- the account's equity -- can go
           back into the trail beside it. [Book_sync.apply] has already
           stabilized on the account's book, so no stabilize sees those closes
           beside the file's. Once only: a second restore would replace the
           trail again and drop every mark made since.

           A restore that raises is caught neither here nor by either caller
           (bin/main.ml's first sync, [sync_forever]). It leaves [sync] through
           the enclosing monitor and ends the process, as the startup restore
           it replaced did; there is no next read. The flag is set first only
           so that a caller which did catch the exception would not restore a
           second time. *)
        if not t.synced then (
          t.synced <- true;
          t.on_first_sync ())
    | Error e, _ | _, Error e -> t.last_error <- Some (Error.to_string_hum e));
    t.on_change ())
```

  3. In `sync`, replace the comment and the two lines before `let%bind account`:

```ocaml
      (* Stamped when the read starts, not when it answers: two syncs can
         overlap, and [sync_with] orders them by what each one read. *)
      let started = Time_ns.now () in
```

  with:

```ocaml
      (* Numbered and stamped when the read starts, not when it answers: two
         syncs can overlap, and [sync_with] orders them by the number. *)
      let seq = start_read t in
      let started = Time_ns.now () in
```

  and `sync_with t ~account ~positions ~at:started;` with `sync_with t ~account ~positions ~at:started ~seq;`.
  4. After `sync`, before `sync_forever`, add:

```ocaml
(* What the order manager calls once one of the desk's fills is in the graph:
   the reads in flight are refused, and a new one starts, so the account
   catches up with the fill -- the sync corrects the book the fills drive.
   Not awaited: the manager's one-at-a-time jobs must not wait on the
   venue's account.

   One such read at a time, and one owed. A read is two requests, the account
   and the positions, and Alpaca allows 200 a minute for everything the desk
   sends. A rebalance that fills thirty names, or a stream of partial fills,
   would otherwise spend that budget on reads the next fill refuses, and it is
   the budget a submit, a cancel and a kill's cancels need; a 429 on a submit
   is a refusal. A fill that lands while the read is out refuses it, as any
   fill does, and is owed a read that starts when that one answers. So the
   read that finally applies started after the last fill. *)
let rec after_fill t =
  fill_applied t;
  match t.fill_read with
  | `Running | `Owed -> t.fill_read <- `Owed
  | `Idle ->
      t.fill_read <- `Running;
      upon (sync t) (fun (_ : unit Or_error.t) ->
          match t.fill_read with
          | `Owed ->
              t.fill_read <- `Idle;
              after_fill t
          | _ -> t.fill_read <- `Idle)
```

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 387` (385 + 2). Neither case calls `after_fill`: the first numbers its reads by hand and the second calls `fill_applied` directly. So coalescing its reads changes no figure here; the hosts call it (Task 18).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | tail -3
git add desk/desk.ml test/test_desk.ml lib/verified.ml
git commit -F - <<'EOF'
desk: the book's reads are ordered by the desk's own read numbers and a read that raced one of the desk's fills is refused and read again, because a clock stepped back froze the book without a word and positions that follow fills would be undone by an account read taken before them

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 4: A request's bound on the test's clock, in a scheduler suite of its own

A1's final re-review, residuals 6 and 7. The one test that waited on the wall clock, and that ran whatever Async jobs earlier suites left queued, moves onto a synthetic clock in its own executable. The five sentences it made untrue are restored.

**Files:**
- Modify: `desk/alpaca_paper.ml`, `test/test_alpaca_paper.ml`, `lib/verified.ml`
- Create: `test/desk_async/dune`, `test/desk_async/test_desk_async.ml`
- Modify: `README.md`, `docs/overview.md`, `docs/status.md`, `web/index.html`

**Interfaces:**
- Consumes: A1's `Alpaca_paper.within ~span ~what f` and `request_timeout` (desk/alpaca_paper.ml:261-271); the case `test_a_request_that_never_answers_is_an_error_at_its_bound` (test/test_alpaca_paper.ml:164-202), whose loop runs `Async.Scheduler.Expert.run_cycles_until_no_jobs_remain` until 5 ms of wall time pass; Async's `Time_source.create`, `read_only`, `with_timeout`, `advance_by_alarms ?wait_for`.
- Produces:
  - `Alpaca_paper.within : ?time_source:Time_source.t -> span:Time_ns.Span.t -> what:string -> (abandon:unit Deferred.t -> 'a Or_error.t Deferred.t) -> 'a Or_error.t Deferred.t`. The bound is measured on `time_source`, whose default is the wall clock, so every existing caller is unchanged.
  - `test/desk_async/`, a second test executable that `dune runtest` (and so `make test`) runs beside the main suite. Its cases need a running scheduler, and every wait in them is on a `Time_source` the case advances. Its cases are the request bound's and its own count's; Tasks 14 and 15 add the order manager's.
  - `lib/verified.ml` gains `let scheduler_tests = 2`, the scheduler suite's count: the transport case plus the suite's own count case. That suite asserts it against its own registry, as test_ohcamel.ml asserts `tests`. `/api/reports` does not serve it (lib/reports.ml:326-333 is untouched).
  - The main suite loses its one case that cycled the scheduler: 387 - 1 = 386.

- [ ] **Step 1: Write the failing test.**
  1. `test/desk_async/dune`:

```
; The desk's cases that need a running Async scheduler: a request bound that
; must fire, and (from Task 14) the order manager's flows -- a proposal waits
; on the venue, updates arrive on a pipe, an unknown answer is looked up after
; a delay. The main suite never starts the scheduler or runs its cycles --
; every case there is a pure function or an already-determined deferred -- so
; these run in their own executable, and lib/verified.ml counts them apart, as
; scheduler_tests. `dune runtest` runs both. No case here
; waits on the wall clock: every delay is on a Time_source the case creates
; and advances.
; let%bind and let%map need the preprocessor desk/dune names.
(test
 (name test_desk_async)
 (libraries ohcamel ohcamel_desk core async alcotest)
 (preprocess
  (pps ppx_jane)))
```

  2. `test/desk_async/test_desk_async.ml`:

```ocaml
(* The desk with the scheduler running, and never the wall clock.

   A case that needs time to pass creates a clock of its own (Time_source.create)
   and advances it, so a bound of thirty seconds is crossed in no time at all.
   A case sees only its own timers: moving its clock fires nothing an earlier
   case left behind, and nothing here depends on which case ran first. *)

open Core
open Async
module D = Ohcamel_desk

let t0 = Time_ns.of_string_with_utc_offset "2026-09-14T14:00:00Z"
let case name test = Alcotest.test_case name `Quick (fun () -> Thread_safe.block_on_async_exn test)

(* The transport's bound (A1's final review, I3), at the production span. A
   request that never answers ends as an error naming what was asked and the
   bound. The request is told it has been abandoned, which is what the
   transport closes a connection on when it can: one still connecting, or one
   that has answered. One nanosecond short of 30 s nothing has fired; at 30 s
   it has. An answer inside the bound is that answer. *)
let test_a_request_that_never_answers_is_an_error_at_its_bound () =
  let clock = Time_source.create ~now:t0 () in
  let time_source = Time_source.read_only clock in
  let abandoned = ref None in
  let never =
    D.Alpaca_paper.within ~time_source ~span:D.Alpaca_paper.request_timeout ~what:"GET /v2/account"
      (fun ~abandon ->
        abandoned := Some abandon;
        Deferred.never ())
  in
  let%bind answered =
    D.Alpaca_paper.within ~time_source ~span:D.Alpaca_paper.request_timeout ~what:"GET /v2/clock"
      (fun ~abandon:_ -> return (Ok 42))
  in
  Alcotest.(check (option int)) "an answer inside the bound is that answer, the 42 given" (Some 42) (Result.ok answered);
  let settle () = Scheduler.yield_until_no_jobs_remain () in
  (* 14:00:00 + 30 s - 1 ns = 14:00:29.999999999 *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle clock
      ~to_:(Time_ns.add t0 Time_ns.Span.(D.Alpaca_paper.request_timeout - nanosecond))
  in
  let%bind () = settle () in
  Alcotest.(check bool) "nothing at 1 ns short of 30 s" false (Deferred.is_determined never);
  (* 14:00:00 + 30 s *)
  let%bind () = Time_source.advance_by_alarms ~wait_for:settle clock ~to_:(Time_ns.add t0 D.Alpaca_paper.request_timeout) in
  let%map () = settle () in
  (match Deferred.peek never with
  | Some (Error e) ->
      (* the bound, 30 s, as Time_ns.Span.to_string_hum prints it *)
      Alcotest.(check string)
        "the request and the bound, and nothing else" "alpaca_paper: GET /v2/account did not answer within 30s"
        (Error.to_string_hum e)
  | Some (Ok _) -> Alcotest.fail "a Deferred that never fills answered"
  | None -> Alcotest.fail "the bound had not fired at 30 s on the test's clock");
  Alcotest.(check bool)
    "the request was told it is abandoned" true
    (Option.value_map !abandoned ~default:false ~f:Deferred.is_determined)

let suites =
  [
    ( "transport",
      [ case "a request that never answers is an error at its bound" test_a_request_that_never_answers_is_an_error_at_its_bound ] );
  ]

(* The registry counts itself against lib/verified.ml's [scheduler_tests], as
   test_ohcamel.ml counts itself against [tests]. The +1 is this case. It needs
   no scheduler. *)
let test_the_count_is_the_dated_one () =
  let registered = 1 + List.sum (module Int) suites ~f:(fun (_, cases) -> List.length cases) in
  Alcotest.(check int)
    (sprintf "lib/verified.ml says %d scheduler tests; the registry holds %d" Ohcamel.Verified.scheduler_tests registered)
    Ohcamel.Verified.scheduler_tests registered

let () =
  Alcotest.run "ohcamel desk, with the scheduler"
    (suites
    @ [ ("verified", [ Alcotest.test_case "the scheduler suite's count is the dated one" `Quick test_the_count_is_the_dated_one ]) ])
```

  3. In `test/test_alpaca_paper.ml`, delete `test_a_request_that_never_answers_is_an_error_at_its_bound`, with its comment, and its `Alcotest.test_case` entry in `suite`. In the file's header comment, change `and the transport is four lines that the live host exercises.` to `and the transport's bound is pinned in test/desk_async, on a clock that test advances.`

- [ ] **Step 2: Run and watch it fail.** `make test 2>&1 | grep -E 'Error|Unbound|label|FAIL' | head -8`. Expected:
  - in test_desk_async.ml: `The function applied to this argument has type ... This argument cannot be applied with label ~time_source`. The compiler stops at a file's first error. Once Step 3 lands, the next is `Unbound value Ohcamel.Verified.scheduler_tests`, which Step 4 adds.
  - in the main suite, which still builds and runs, the count case: `lib/verified.ml says 387 tests; the registry holds 386`. Step 1 deleted a case, and Step 4 lowers `tests`.

- [ ] **Step 3: Implement.** In `desk/alpaca_paper.ml`, replace `within`'s first three lines:

```ocaml
let within ~(span : Time_ns.Span.t) ~(what : string)
    (f : abandon:unit Deferred.t -> 'a Or_error.t Deferred.t) : 'a Or_error.t Deferred.t =
  let abandoned = Ivar.create () in
  match%map Clock_ns.with_timeout span (f ~abandon:(Ivar.read abandoned)) with
```

  with:

```ocaml
let within ?(time_source = Time_source.wall_clock ()) ~(span : Time_ns.Span.t) ~(what : string)
    (f : abandon:unit Deferred.t -> 'a Or_error.t Deferred.t) : 'a Or_error.t Deferred.t =
  let abandoned = Ivar.create () in
  match%map Time_source.with_timeout time_source span (f ~abandon:(Ivar.read abandoned)) with
```

  and add to the comment above `request_timeout`: `[time_source] is the wall clock everywhere but the test that pins the bound, which advances a clock of its own, so no test waits on the wall's.`

  (async_kernel v0.17's `advance_by_alarms` stops at each alarm's own time, then fires every alarm at or before the clock (_opam/lib/async_kernel/time_source.ml:113-143). So nothing fires 1 ns early, and the 30 s alarm fires at 30 s. If a later Async rounds alarms to its timing wheel's precision, pin 14:00:29 instead, change that assertion's derivation to "30 s - 1 s", and say so in the report. The 30 s assertion stays.)

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 386` (387 - 1: the case moved to the scheduler suite). Directly after it, add:

```ocaml
(* The scheduler suite's count: test/desk_async, the executable whose cases run
   with Async's scheduler on clocks of their own. It asserts this against its
   own registry, as test_ohcamel.ml asserts [tests], so neither count can
   drift. Counted apart because the main suite's promise is that it never
   starts the scheduler. Not served on /api/reports: the page's dated block
   prints [tests], and reports.ml names its keys one by one. *)
let scheduler_tests = 2
```

  (2: the transport case and the suite's own count case. The comment names no desk library, host, path or `Sqlite3`, as CI's lib/ grep requires; lib/reports.ml is untouched.) `make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'` shows both executables. The scheduler suite's two cases pass.

- [ ] **Step 5: No test waits on the wall clock, so the documents say so again.** `grep -rn 'Clock_ns\|run_cycles_until_no_jobs_remain\|Scheduler.Expert' test/` prints nothing. Then restore the five sentences A1's `cf79764` qualified, each to the text it had before that commit:
  1. README.md:1099-1101. `and nothing\nthat waits on the wall clock except one test, which lets a 5 ms request timeout\nexpire. They cover the numerics against hand-computed` becomes `and nothing\nthat waits on the wall clock. They cover the numerics against hand-computed`.
  2. README.md:1200-1202. `credentials, nothing waiting on a clock beyond one 5 ms request timeout — so the\ncode whose job is to hold a` becomes `credentials, nothing waiting on a clock — so the code whose job is to hold a`.
  3. docs/overview.md:201-202. `no waiting on a\n  clock except one test's 5 ms request timeout. Expected values` becomes `no waiting on a\n  clock. Expected values`.
  4. docs/status.md:196-197. `nothing waiting on a\n  clock except one test that lets a 5 ms request timeout expire. Expected values` becomes `nothing waiting on a\n  clock. Expected values`.
  5. web/index.html:291. `and nothing that waits on the wall clock except one test, which lets a 5 ms request timeout expire. Seven are` becomes `and nothing that waits on the wall clock. Seven are`.

  The counts in those sentences are Task 20's. `grep -rn '5 ms' README.md docs/overview.md docs/status.md web/index.html` prints nothing.

- [ ] **Step 6: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'
git add desk/alpaca_paper.ml test/test_alpaca_paper.ml test/desk_async/dune test/desk_async/test_desk_async.ml lib/verified.ml README.md docs/overview.md docs/status.md web/index.html
git commit -F - <<'EOF'
test: the request bound is pinned on a clock the test advances, in a scheduler suite of its own, because a case that waited on the wall clock made five published sentences untrue and ran whatever jobs the suites before it had left queued

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 5: The desk's code in the coverage figure

The ledger's A1 coverage ruling (progress.md:168): instrumenting `desk/` was carried to A2's pre-flight, with the CI floor checked against the combined figure.

**Files:**
- Modify: `desk/dune`, `Makefile` (the coverage target's comment), `lib/verified.ml`, `README.md`, `docs/overview.md`, `docs/status.md`

**Interfaces:**
- Consumes:
  - lib/dune's instrumentation stanza (lib/dune:30-37);
  - the Makefile's `coverage` target (Makefile:195-216);
  - ci.yml's `coverage` job. Its `Measure` step runs `dune runtest --force --instrument-with bisect_ppx`. Its `Coverage floor` step takes the first `N.N%` of `bisect-ppx-report summary` and fails below 60. Neither step changes: both already read whatever is instrumented.
  - lib/verified.ml's coverage constants at `cf79764`: 3,891 / 4,743 = 82.037%, published 82.0.
- Produces:
  - `bisect_ppx` instruments `ohcamel_desk` as it does `ohcamel`, and only under `--instrument-with bisect_ppx`.
  - `lib/verified.ml`'s `coverage_covered`, `coverage_lines`, `coverage_pct` and `dated` are the combined figure on this tree (386 tests).
  - The documents say the figure covers both libraries.

No test count change. The check is a measurement, taken before and after.

- [ ] **Step 1: The figure leaves the desk out.** `make coverage 2>&1 | grep -E 'desk/|Coverage:'`. Expected: the `Coverage:` line only. No `desk/` file is in the per-file table.

- [ ] **Step 2: Instrument the desk.** In `desk/dune`, replace the library's last field:

```
 (preprocess
  (pps ppx_jane)))
```

  with:

```
 (preprocess
  (pps ppx_jane))
 ; Coverage instrumentation, declared as lib/dune declares it: OFF unless dune
 ; is invoked with --instrument-with bisect_ppx, which only `make coverage` and
 ; CI's coverage job do. Without it the published figure was the kernel's
 ; alone and said nothing about the code that writes the journal and sends
 ; orders.
 (instrumentation
  (backend bisect_ppx)))
```

- [ ] **Step 3: Measure, and check the floor against the combined figure.**

```bash
make coverage 2>&1 | grep -E 'desk/|Coverage:'
eval $(opam env --switch=$PWD --set-switch) && bisect-ppx-report summary --coverage-path _coverage | grep -oE '[0-9]+\.[0-9]+%' | tr -d '%'
```

  Expected:
  - every `desk/*.ml` file appears in the per-file table;
  - the `Coverage: C/L (P%)` line names more points than 4,743;
  - the second command prints exactly one number, `P`. That pipeline is CI's floor, so the floor now reads the combined figure.

  Also compute the desk's own figure from its per-file rows: sum the desk files' covered points over their total points. Write all three figures (combined, kernel, desk) in the report.

  **If `P` is below 60, stop.** Do not lower the floor and do not commit. Report the per-file table instead.

- [ ] **Step 4: Record the figure.** In `lib/verified.ml`:
  - set `coverage_covered` to `C` and `coverage_lines` to `L`;
  - set `coverage_pct` to `P` rounded to one decimal, and `dated` to today;
  - in the comment, replace `3,891 / 4,743 is 82.037%, published as 82.0 -- README.md's badge rounds further to the whole percent (82%) and docs/status.md carries the one-decimal figure (82.0%)` with the new quotient to three decimals, its one-decimal rounding, and its whole-percent rounding;
  - replace the comment's last paragraph (`The points are library [ohcamel]'s alone. desk/ has no instrumentation stanza yet, ...`) with:

```ocaml
   The points are both libraries': the kernel's in lib/ and the desk's in
   desk/, whose dune file declares the same instrumentation backend. The desk
   is named here by its directory, because CI's grep refuses the desk
   library's name anywhere in lib/. *)
```

  Then the documents, each to the new figure:
  1. README.md:4 -- the badge's `coverage-82%25` and `coverage 82%` become the whole percent.
  2. README.md:1171-1176 -- `reports **82%** (measured\n2026-09-13)` takes the new whole percent and date. `The number is the risk kernel's, library\n`ohcamel` in `lib/`. The desk library in `desk/` is not instrumented yet, so its\ncode is in neither the figure nor the table below.` becomes `The number covers both libraries: the risk\nkernel, `ohcamel` in `lib/`, and the desk, `ohcamel_desk` in `desk/`.`
  3. README.md:1178-1192 -- the per-file table is re-measured. Each desk file goes in the column its figure puts it in: 80% and above on the left, below on the right.
     - The sentence after the table becomes `The left column is everything that computes a risk number or decides what the desk does. The right column is everything that talks to a network`.
     - A desk file in the right column that does not talk to a network is named in the sentence that already names `types.ml` and `options.ml`, with its reason.
  4. docs/overview.md:210-211 -- `**82.0% coverage** of the risk kernel in `lib/` (measured 2026-09-13; `desk/`\n  is not instrumented yet).` becomes `**P% coverage** of `lib/` and `desk/` together (measured <date>).`
  5. docs/status.md's coverage bullet (find it by its words; it is :204-206 at both `cf79764` and `52e1fbe`) -- `(3,891 / 4,743 instrumented points in the risk kernel,\n  `lib/`, measured 2026-09-13; the desk library in `desk/` is not instrumented\n  yet)` becomes `(C / L instrumented points in `lib/` and `desk/`, measured <date>)`, and `Coverage 82.0%` takes the new figure.
  6. Makefile, the coverage target's comment -- `lib/dune declares the backend` becomes `lib/dune and desk/dune declare the backend`. The IO edges' list gains `the desk's Alpaca transport`.

  Web pages print the figure from `/api/reports` (web/argument.js:265), so no page text changes. `grep -rn 'not instrumented\|3,891\|4,743' README.md docs/overview.md docs/status.md lib/verified.ml` prints one line only: docs/status.md's dated 2026-09-13 history row, added by `52e1fbe`. It states that day's figure, and a dated row stays as written.

- [ ] **Step 5: Commit.**

```bash
make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'
git add desk/dune Makefile lib/verified.ml README.md docs/overview.md docs/status.md
git commit -F - <<'EOF'
verified: the coverage figure takes in the desk library, measured on this tree, with CI's floor reading the combined number, because a dated figure that leaves out the code which writes the journal and sends orders says less than the page implies

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 6: The gate

**Files:**
- Create: `lib/gate.ml`, `test/test_gate.ml`
- Modify: `test/test_ohcamel.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: `Graph.snapshot`, `Graph.fork`, `Graph.destroy`, `Graph.apply_fill`, `Graph.limit_results`, `Graph.set_returns`, the `gross_exposure` and `equity` fields of `Graph.Snapshot.t`; `Types.Breach`.
- Produces (module `Ohcamel.Gate`):
  - `Fill.t = { symbol : Symbol.t; qty : Qty.t; price : Price.t }` — `qty` signed, positive buys.
  - `Move.t = { limit : string; before : Breach.t option; after : Breach.t option }`.
  - `Verdict.t = { passed : bool; created : Move.t list; worsened : Move.t list; cleared : Move.t list; unevaluable : Move.t list; gross_before : Notional.t; gross_after : Notional.t; equity_before : Notional.t; equity_after : Notional.t }`; `Verdict.reasons : t -> string list` (one sentence per created or worsened limit, created first, in configured limit order).
  - `check : Graph.t -> fills:Fill.t list -> Verdict.t`.
  - `epsilon : float` (= 1e-9): an excess must rise by more than this to count as worsened.

- [ ] **Step 1: Write the failing tests.** `test/test_gate.ml`:

```ocaml
(* The gate, on a book small enough to check by hand.

     AAPL  400 x 150 = 60,000   TECH
     MSFT  100 x 300 = 30,000   TECH
     XOM  -200 x 100 = -20,000  ENERGY
     cash 1,000,000; TECH 90,000; gross 110,000; net 70,000; equity 1,070,000

   Limits: aapl-cap 80,000 on AAPL, tech-cap 100,000 on TECH, book-cap 200,000
   on the portfolio. Every fill below is priced at the mark unless it says
   otherwise, so equity moves only where the test says it does. *)

open Core
open Ohcamel.Types
module Graph = Ohcamel.Graph
module Gate = Ohcamel.Gate

let aapl = Symbol.of_string "AAPL"
let msft = Symbol.of_string "MSFT"
let xom = Symbol.of_string "XOM"
let money = Notional.of_float
let limit name scope n = { Limit.name; scope; kind = Limit.Gross_notional (money n) }

let with_book ?(msft_qty = 100.0) ~f () =
  let graph =
    Graph.create ~starting_cash:(money 1_000_000.0)
      ~instruments:
        [
          { Instrument.symbol = aapl; sector = Sector.of_string "TECH" };
          { Instrument.symbol = msft; sector = Sector.of_string "TECH" };
          { Instrument.symbol = xom; sector = Sector.of_string "ENERGY" };
        ]
      ~limits:
        [
          limit "aapl-cap" (Limit.Instrument aapl) 80_000.0;
          limit "tech-cap" (Limit.Sector (Sector.of_string "TECH")) 100_000.0;
          limit "book-cap" Limit.Portfolio 200_000.0;
        ]
      ~confidence:0.95 ~return_window:10 ()
  in
  List.iter [ (aapl, 150.0, 400.0); (msft, 300.0, msft_qty); (xom, 100.0, -200.0) ] ~f:(fun (s, p, q) ->
      Graph.set_price graph s (Price.of_float p);
      Graph.set_qty graph s (Qty.of_float q);
      (* A full window, as test_stress.ml's book has, so every node a snapshot
         reads is evaluable; no limit here reads it. *)
      Graph.set_returns graph s [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |]);
  Graph.stabilize graph;
  Exn.protect ~f:(fun () -> f graph) ~finally:(fun () -> Graph.destroy graph)

let fill symbol qty price = { Gate.Fill.symbol; qty = Qty.of_float qty; price = Price.of_float price }
let names moves = List.map moves ~f:(fun m -> m.Gate.Move.limit)

let test_a_buy_that_takes_tech_over_its_cap_fails_naming_it () =
  with_book
    ~f:(fun graph ->
      (* AAPL 500 x 150 = 75,000 (under 80,000); TECH 75,000 + 30,000 = 105,000 (over 100,000) *)
      let v = Gate.check graph ~fills:[ fill aapl 100.0 150.0 ] in
      Alcotest.(check bool) "fails" false v.Gate.Verdict.passed;
      Alcotest.(check (list string)) "created: tech-cap only" [ "tech-cap" ] (names v.Gate.Verdict.created);
      Alcotest.(check (float 1e-9)) "gross after 110,000 + 15,000" 125_000.0 (Notional.to_float v.Gate.Verdict.gross_after);
      Alcotest.(check bool) "the reason names the limit" true
        (List.exists (Gate.Verdict.reasons v) ~f:(String.is_substring ~substring:"tech-cap")))
    ()

let test_the_same_trade_the_other_way_passes () =
  with_book
    ~f:(fun graph ->
      (* TECH 45,000 + 30,000 = 75,000 *)
      let v = Gate.check graph ~fills:[ fill aapl (-100.0) 150.0 ] in
      Alcotest.(check bool) "passes" true v.Gate.Verdict.passed;
      Alcotest.(check (list string)) "nothing created" [] (names v.Gate.Verdict.created);
      Alcotest.(check (float 1e-9)) "gross 110,000 - 15,000" 95_000.0 (Notional.to_float v.Gate.Verdict.gross_after))
    ()

let test_worsening_a_breach_fails_and_reducing_it_passes () =
  (* MSFT 200 x 300 = 60,000, so TECH is 120,000 and 20,000 over. *)
  with_book ~msft_qty:200.0
    ~f:(fun graph ->
      (* +10 MSFT: TECH 123,000, 23,000 over > 20,000 *)
      let up = Gate.check graph ~fills:[ fill msft 10.0 300.0 ] in
      Alcotest.(check bool) "worsening fails" false up.Gate.Verdict.passed;
      Alcotest.(check (list string)) "worsened: tech-cap" [ "tech-cap" ] (names up.Gate.Verdict.worsened);
      (* -10 MSFT: TECH 117,000, 17,000 over < 20,000 -- still breached, and better *)
      let down = Gate.check graph ~fills:[ fill msft (-10.0) 300.0 ] in
      Alcotest.(check bool) "reducing passes" true down.Gate.Verdict.passed;
      Alcotest.(check (list string)) "not worsened" [] (names down.Gate.Verdict.worsened);
      Alcotest.(check (list string)) "not cleared either: still over" [] (names down.Gate.Verdict.cleared))
    ()

let test_a_rebalance_is_gated_as_one_set_of_fills () =
  with_book
    ~f:(fun graph ->
      (* +100 AAPL alone breaches TECH (test above). With -100 MSFT beside it,
         TECH is 75,000 + 0 = 75,000. *)
      let v = Gate.check graph ~fills:[ fill aapl 100.0 150.0; fill msft (-100.0) 300.0 ] in
      Alcotest.(check bool) "the pair passes" true v.Gate.Verdict.passed)
    ()

let test_a_price_away_from_the_mark_moves_equity_by_exactly_the_difference () =
  with_book
    ~f:(fun graph ->
      (* Buy 10 AAPL at 151 against a mark of 150: cash falls 1,510, exposure
         rises 1,500, equity falls 10. 1,070,000 - 10 = 1,069,990. *)
      let v = Gate.check graph ~fills:[ fill aapl 10.0 151.0 ] in
      Alcotest.(check (float 1e-6)) "equity before" 1_070_000.0 (Notional.to_float v.Gate.Verdict.equity_before);
      Alcotest.(check (float 1e-6)) "equity after" 1_069_990.0 (Notional.to_float v.Gate.Verdict.equity_after))
    ()

let test_the_live_book_does_not_move () =
  with_book
    ~f:(fun graph ->
      ignore (Gate.check graph ~fills:[ fill aapl 100.0 150.0; fill xom 50.0 100.0 ] : Gate.Verdict.t);
      Alcotest.(check (float 0.0)) "AAPL still 400" 400.0 (Qty.to_float (Graph.qty graph aapl));
      Alcotest.(check (float 0.0)) "XOM still -200" (-200.0) (Qty.to_float (Graph.qty graph xom));
      Alcotest.(check (float 0.0)) "cash still 1,000,000" 1_000_000.0 (Notional.to_float (Graph.cash graph));
      Alcotest.(check (float 1e-9)) "gross still 110,000" 110_000.0 (Notional.to_float (Graph.gross_exposure graph)))
    ()

(* Isolation over arbitrary proposals: whatever is proposed, the live book's
   quantities and cash are what they were. *)
let prop_the_gate_never_moves_the_live_book =
  let open QCheck in
  QCheck_alcotest.to_alcotest
    (Test.make ~name:"the gate never moves the live book, whatever is proposed" ~count:100
       (list_of_size Gen.(int_range 1 4) (pair (int_bound 2) (int_range (-50) 50)))
       (fun proposals ->
         let symbols = [| aapl; msft; xom |] and marks = [| 150.0; 300.0; 100.0 |] in
         with_book
           ~f:(fun graph ->
             let before = List.map [ aapl; msft; xom ] ~f:(fun s -> Qty.to_float (Graph.qty graph s)) in
             let fills =
               List.filter_map proposals ~f:(fun (i, q) ->
                   if q = 0 then None else Some (fill symbols.(i) (Float.of_int q) marks.(i)))
             in
             ignore (Gate.check graph ~fills : Gate.Verdict.t);
             List.equal Float.equal before (List.map [ aapl; msft; xom ] ~f:(fun s -> Qty.to_float (Graph.qty graph s)))
             && Float.equal 1_000_000.0 (Notional.to_float (Graph.cash graph)))
           ()))

(* Spec §6: the gate never passes a proposal whose fork creates a breach --
   checked against the live book, not the fork. Whatever passed is applied for
   real, and no limit that was clear before is over its line after. If the
   fork and the live graph ever disagreed, this is where it would show. The
   range is wide enough (up to 300 shares, 90,000 of MSFT) that both answers
   occur. *)
let prop_what_the_gate_passes_creates_no_breach_when_traded =
  let open QCheck in
  QCheck_alcotest.to_alcotest
    (Test.make ~name:"what the gate passes creates no breach when it is traded" ~count:100
       (list_of_size Gen.(int_range 1 4) (pair (int_bound 2) (int_range (-300) 300)))
       (fun proposals ->
         let symbols = [| aapl; msft; xom |] and marks = [| 150.0; 300.0; 100.0 |] in
         with_book
           ~f:(fun graph ->
             let fills =
               List.filter_map proposals ~f:(fun (i, q) ->
                   if q = 0 then None else Some (fill symbols.(i) (Float.of_int q) marks.(i)))
             in
             let clear_before =
               List.filter_map (Graph.limit_results graph) ~f:(fun (l, b) ->
                   match b with Some b when not (Breach.breached b) -> Some (Limit.name l) | _ -> None)
             in
             let v = Gate.check graph ~fills in
             (not v.Gate.Verdict.passed)
             || (List.iter fills ~f:(fun (f : Gate.Fill.t) ->
                     Graph.apply_fill graph
                       { Fill.symbol = f.Gate.Fill.symbol; qty = f.Gate.Fill.qty; price = f.Gate.Fill.price; time = Time.epoch });
                 Graph.stabilize graph;
                 List.for_all (Graph.limit_results graph) ~f:(fun (l, b) ->
                     (not (List.mem clear_before (Limit.name l) ~equal:String.equal))
                     || match b with Some b -> not (Breach.breached b) | None -> true)))
           ()))

let suite =
  ( "gate",
    [
      Alcotest.test_case "a buy that takes TECH over its cap fails, naming it" `Quick
        test_a_buy_that_takes_tech_over_its_cap_fails_naming_it;
      Alcotest.test_case "the same trade the other way passes" `Quick test_the_same_trade_the_other_way_passes;
      Alcotest.test_case "worsening a breach fails and reducing it passes" `Quick
        test_worsening_a_breach_fails_and_reducing_it_passes;
      Alcotest.test_case "a rebalance is gated as one set of fills" `Quick test_a_rebalance_is_gated_as_one_set_of_fills;
      Alcotest.test_case "a price away from the mark moves equity by exactly the difference" `Quick
        test_a_price_away_from_the_mark_moves_equity_by_exactly_the_difference;
      Alcotest.test_case "the live book does not move" `Quick test_the_live_book_does_not_move;
      prop_the_gate_never_moves_the_live_book;
      prop_what_the_gate_passes_creates_no_breach_when_traded;
    ] )
```

Register `Test_gate.suite;` after `Test_stress.suite;` in `test/test_ohcamel.ml`.

- [ ] **Step 2: Run and watch it fail.** `make test 2>&1 | tail -20`. Expected: `Unbound module Ohcamel.Gate`.

- [ ] **Step 3: Implement.** `lib/gate.ml`:

```ocaml
(* The pre-trade gate: what a proposed set of fills would do to the book's
   limits, answered by the engine itself.

   Design §3.7 and invariant 12: every order passes here before it exists at a
   venue. The answer comes from a fork of the live graph with the fills
   applied, as stress.ml's scenarios do, so there is no second implementation
   of exposure, equity or any limit rule to drift from the live one (invariant
   2). It lives in the risk kernel rather than the desk because it is a
   read-only calculation on a fork -- the category invariant 6 always
   permitted -- and it names nothing that trades.

   CREATED OR WORSENED FAILS; REDUCED PASSES. A proposal that takes a clear
   limit over its line fails, naming it. One that leaves a breached limit
   further over its line fails too. One that brings a breached limit closer to
   its line passes, still breached: a desk must be able to trade out of a
   breach, and a gate that refused every trade on a breached book would hold
   the book exactly where it should not stay.

   Excess is compared within one limit, in that limit's own unit, so "further
   over" needs no conversion; [epsilon] keeps a difference of float rounding
   in a limit the fills did not touch from reading as a worsening. A limit the
   fork cannot evaluate -- a VaR still warming up -- is reported as
   unevaluable and does not fail the proposal: unknown is not a breach, and it
   is not a pass either, which is why it is reported rather than folded into
   one. *)

open Core
open Types

let epsilon = 1e-9

module Fill = struct
  type t = { symbol : Symbol.t; qty : Qty.t; price : Price.t } [@@deriving sexp_of]
end

module Move = struct
  type t = { limit : string; before : Breach.t option; after : Breach.t option } [@@deriving sexp_of]
end

module Verdict = struct
  type t = {
    passed : bool;
    created : Move.t list;
    worsened : Move.t list;
    cleared : Move.t list;
    unevaluable : Move.t list;
    gross_before : Notional.t;
    gross_after : Notional.t;
    equity_before : Notional.t;
    equity_after : Notional.t;
  }
  [@@deriving sexp_of]

  let describe verb (m : Move.t) =
    match m.Move.after with
    | None -> sprintf "%s would be %s" m.Move.limit verb
    | Some after -> sprintf "%s would be %s: %s" m.Move.limit verb (Limits.to_string after)

  let reasons t =
    List.map t.created ~f:(describe "breached") @ List.map t.worsened ~f:(describe "further over its line")
end

let breached = function Some b -> Breach.breached b | None -> false

let check (graph : Graph.t) ~(fills : Fill.t list) : Verdict.t =
  (* The live snapshot first: it stabilizes, so any write the live graph had
     not settled is settled before the fork copies its cells. *)
  let before = Graph.snapshot graph in
  let fork = Graph.fork graph in
  Exn.protect
    ~finally:(fun () -> Graph.destroy fork)
    ~f:(fun () ->
      List.iter fills ~f:(fun (f : Fill.t) ->
          Graph.apply_fill fork { Types.Fill.symbol = f.symbol; qty = f.qty; price = f.price; time = Time.epoch });
      let after = Graph.snapshot fork in
      let moves =
        List.map2_exn (Graph.limit_results graph) (Graph.limit_results fork) ~f:(fun (l, b) (_, a) ->
            { Move.limit = Limit.name l; before = b; after = a })
      in
      let created = List.filter moves ~f:(fun m -> breached m.after && not (breached m.before)) in
      let worsened =
        List.filter moves ~f:(fun m ->
            match (m.before, m.after) with
            | Some b, Some a ->
                Breach.breached b && Breach.breached a && Float.( > ) (Breach.excess a) (Breach.excess b +. epsilon)
            | _ -> false)
      in
      let cleared =
        List.filter moves ~f:(fun m ->
            breached m.before && match m.after with Some a -> not (Breach.breached a) | None -> false)
      in
      let unevaluable = List.filter moves ~f:(fun m -> Option.is_none m.after) in
      {
        Verdict.passed = List.is_empty created && List.is_empty worsened;
        created;
        worsened;
        cleared;
        unevaluable;
        gross_before = before.Graph.Snapshot.gross_exposure;
        gross_after = after.Graph.Snapshot.gross_exposure;
        equity_before = before.Graph.Snapshot.equity;
        equity_after = after.Graph.Snapshot.equity;
      })
```

Note for the implementer: `Graph.limit_results` reads observers and does not stabilize; both snapshots above stabilized first, so both lists are settled. If `Types.Time.epoch` is spelled differently, use the epoch constant `lib/types.ml`'s `Time` module exports. The fork shares Incremental's process-wide state and is destroyed in `finally`, as stress.ml does.

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 394` (386 + 8).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | tail -3
git add lib/gate.ml test/test_gate.ml test/test_ohcamel.ml lib/verified.ml
git commit -F - <<'EOF'
gate: a proposal's fills on a fork, failing on a created or worsened breach and passing a reduced one, because every order must be judged by the engine and a desk must be able to trade out of a breach

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 7: The rules

**Files:**
- Create: `desk/rules.ml`, `test/test_rules.ml`
- Modify: `test/test_ohcamel.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: `Order.Request.t`, `Order.Kind`, `Order.Side`; `Ohcamel.Config.Book.Desk_spec`.
- Produces (module `Ohcamel_desk.Rules`):
  - `Recent.t = { symbol : Symbol.t; side : Order.Side.t; qty : int; at : Time_ns.t }`.
  - `Context.t = { spec : Desk_spec.t; universe : Symbol.Set.t; can_trade : (unit, string) Result.t; halted : string option; session_open : bool; mark : Price.t option; stale : bool; adv20 : float option; recent : Recent.t list; open_orders : int; now : Time_ns.t }`.
  - `Failure.t = { rule : string; why : string }`.
  - `names : string list` = `["universe"; "whole_shares"; "trading"; "kill_switch"; "session"; "mark"; "tick"; "collar"; "notional"; "adv"; "duplicate"; "open_orders"]`.
  - `check : Context.t -> Order.Request.t -> Failure.t list` — failures in the order of `names`.

  `tick` is an addition to the spec's table (ruling recorded in this plan's ledger): a limit price on a stock at or above $1 must be a whole cent, because Rule 612 forbids sub-penny quotes there and a venue would refuse the order after the journal had already recorded it as sent.

- [ ] **Step 1: Write the failing tests.** `test/test_rules.ml`:

```ocaml
(* The rules, each on both sides of its line.

   The base context is an order that passes everything: AAPL in the universe,
   trading enabled, no halt, the session open, a fresh mark of 150, twenty-day
   volume of 16,600 shares, nothing recent, no open orders. Each case moves one
   thing and names the rule that must fail -- except the last, which moves
   three and requires all three named. *)

open Core
open Ohcamel.Types
module Rules = Ohcamel_desk.Rules
module Order = Ohcamel_desk.Order
module Desk_spec = Ohcamel.Config.Book.Desk_spec

let aapl = Symbol.of_string "AAPL"
let now = Time_ns.of_string_with_utc_offset "2026-09-14T14:00:00Z"

let spec = { Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled }

let context =
  {
    Rules.Context.spec;
    universe = Symbol.Set.of_list [ aapl; Symbol.of_string "MSFT" ];
    can_trade = Ok ();
    halted = None;
    session_open = true;
    mark = Some (Price.of_float 150.0);
    stale = false;
    adv20 = Some 16_600.0;
    recent = [];
    open_orders = 0;
    now;
  }

let request ?(symbol = aapl) ?(side = Order.Side.Buy) ?(kind = Order.Kind.Market) qty =
  {
    Order.Request.client_order_id =
      Option.value_exn (Ohcamel_desk.Ids.Client_order_id.of_string "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZZ");
    symbol;
    side;
    qty;
    kind;
  }

let failed ctx req = List.map (Rules.check ctx req) ~f:(fun f -> f.Rules.Failure.rule)
let check_rules what expected ctx req = Alcotest.(check (list string)) what expected (failed ctx req)

let test_an_order_inside_every_line_passes () = check_rules "no failures" [] context (request 100)

let test_the_book_and_the_desk_decide_whether_to_trade_at_all () =
  check_rules "a name outside the universe" [ "universe" ] context (request ~symbol:(Symbol.of_string "TSLA") 10);
  check_rules "zero shares" [ "whole_shares" ] context (request 0);
  check_rules "a book that disables trading" [ "trading" ]
    { context with spec = Desk_spec.default } (request 10);
  check_rules "a desk with no trading half" [ "trading" ]
    { context with can_trade = Error "the venue has no trading half" } (request 10);
  check_rules "a tripped switch" [ "kill_switch" ] { context with halted = Some "tripped by nvda-cap" } (request 10);
  check_rules "a closed session" [ "session" ] { context with session_open = false } (request 10)

let test_an_order_needs_a_live_mark () =
  (* With no mark there is nothing to price the notional or the collar
     against, so those two are not evaluated -- the mark rule is the reason. *)
  check_rules "no mark" [ "mark" ] { context with mark = None } (request 10);
  check_rules "a stale mark" [ "mark" ] { context with stale = true } (request 10)

let test_the_tick_and_the_collar () =
  let limit p = Order.Kind.Limit (Price.of_float p) in
  check_rules "a whole-cent limit" [] context (request ~kind:(limit 149.5) 10);
  check_rules "a sub-penny limit on a $150 stock" [ "tick" ] context (request ~kind:(limit 149.505) 10);
  (* collar 5% of 150 = 7.50: 157.50 is on the line, 157.51 is past it *)
  check_rules "exactly 5% away" [] context (request ~kind:(limit 157.5) 10);
  check_rules "just past 5%" [ "collar" ] context (request ~kind:(limit 157.51) 10)

let test_the_notional_cap () =
  (* 25,000 at 150: 166 x 150 = 24,900 passes; 167 x 150 = 25,050 fails.
     ADV is raised here so the volume rule is not the one that speaks. *)
  let ctx = { context with adv20 = Some 1_000_000.0 } in
  check_rules "166 shares" [] ctx (request 166);
  check_rules "167 shares" [ "notional" ] ctx (request 167)

let test_participation_in_twenty_day_volume () =
  (* 1% of 16,600 = 166 shares *)
  check_rules "166 shares" [] context (request 166);
  check_rules "167 shares" [ "adv" ] { context with spec = { spec with Desk_spec.max_order_notional = 1e9 } } (request 167);
  check_rules "unknown volume refuses" [ "adv" ] { context with adv20 = None } (request 1)

let test_a_duplicate_within_the_window () =
  let recent seconds_ago qty =
    { Rules.Recent.symbol = aapl; side = Order.Side.Buy; qty; at = Time_ns.sub now (Time_ns.Span.of_sec seconds_ago) }
  in
  check_rules "the same order 9 s ago" [ "duplicate" ] { context with recent = [ recent 9.0 10 ] } (request 10);
  check_rules "the same order 11 s ago" [] { context with recent = [ recent 11.0 10 ] } (request 10);
  check_rules "a different quantity 1 s ago" [] { context with recent = [ recent 1.0 11 ] } (request 10)

let test_open_orders () =
  check_rules "19 open" [] { context with open_orders = 19 } (request 10);
  check_rules "20 open" [ "open_orders" ] { context with open_orders = 20 } (request 10)

let test_every_failure_is_reported_in_rule_order () =
  check_rules "three at once"
    [ "universe"; "kill_switch"; "session" ]
    { context with halted = Some "halted by hand"; session_open = false }
    (request ~symbol:(Symbol.of_string "TSLA") 10)

let suite =
  ( "rules",
    [
      Alcotest.test_case "an order inside every line passes" `Quick test_an_order_inside_every_line_passes;
      Alcotest.test_case "the book and the desk decide whether to trade at all" `Quick
        test_the_book_and_the_desk_decide_whether_to_trade_at_all;
      Alcotest.test_case "an order needs a live mark" `Quick test_an_order_needs_a_live_mark;
      Alcotest.test_case "the tick and the collar" `Quick test_the_tick_and_the_collar;
      Alcotest.test_case "the notional cap" `Quick test_the_notional_cap;
      Alcotest.test_case "participation in twenty-day volume" `Quick test_participation_in_twenty_day_volume;
      Alcotest.test_case "a duplicate within the window" `Quick test_a_duplicate_within_the_window;
      Alcotest.test_case "open orders" `Quick test_open_orders;
      Alcotest.test_case "every failure is reported, in rule order" `Quick test_every_failure_is_reported_in_rule_order;
    ] )
```

  (In `test_participation_in_twenty_day_volume`, the 167-share case lifts the notional cap so only `adv` speaks; the 166-share case needs no lift because 166 x 150 = 24,900 is under 25,000.) Register after `Test_desk.suite;`.

- [ ] **Step 2: Run and watch it fail.** Expected: `Unbound module Ohcamel_desk.Rules`.

- [ ] **Step 3: Implement.** `desk/rules.ml`:

```ocaml
(* The rules an order must pass before the gate is even asked.

   Design §3.7. The gate asks what an order does to the book's limits; these
   ask whether the order should exist at all -- is it in the mandate, is the
   desk allowed to trade, is there a price to trade against, is it the size of
   a mistake. Pure: the order manager gathers the context, and every rule is a
   line of arithmetic on it.

   EVERY FAILURE IS REPORTED. The signal contract stops at its first failing
   rule because a signal is a document to reject; a ticket is something a
   person is fixing, and fixing one problem to discover the next is a slow way
   to be told three things at once.

   A missing or stale mark fails [mark] and skips the rules that need a price
   ([notional], [collar]): a notional computed against a price nobody has is a
   number with no meaning, and reporting it would bury the one reason that
   matters. The same for an unknown twenty-day volume, which fails [adv]
   rather than passing it: an order sized against liquidity nobody measured is
   exactly the order this rule exists to stop. *)

open Core
open Ohcamel.Types
module Desk_spec = Ohcamel.Config.Book.Desk_spec

module Recent = struct
  (* Alternate_sexp is Time_ns.t itself, with a sexp_of Core has not poisoned.
     The field names it so the record can derive one; the arithmetic below
     keeps Core's Time_ns, which the alias module does not carry. *)
  type t = { symbol : Symbol.t; side : Order.Side.t; qty : int; at : Time_ns.Alternate_sexp.t } [@@deriving sexp_of]
end

module Context = struct
  type t = {
    spec : Desk_spec.t;
    universe : Symbol.Set.t;
    can_trade : (unit, string) Result.t;
    halted : string option;
    session_open : bool;
    mark : Price.t option;
    stale : bool;
    adv20 : float option;
    recent : Recent.t list;
    open_orders : int;
    now : Time_ns.t;
  }
end

module Failure = struct
  type t = { rule : string; why : string } [@@deriving sexp_of, compare, equal]
end

let names =
  [ "universe"; "whole_shares"; "trading"; "kill_switch"; "session"; "mark"; "tick"; "collar"; "notional"; "adv"; "duplicate"; "open_orders" ]

let check (c : Context.t) (r : Order.Request.t) : Failure.t list =
  let fail rule why = Some { Failure.rule; why } in
  let sym = Symbol.to_string r.Order.Request.symbol in
  let priced = match c.mark with Some p when not c.stale -> Some (Price.to_float p) | _ -> None in
  let limit = Option.map (Order.Kind.limit_price r.Order.Request.kind) ~f:Price.to_float in
  List.filter_opt
    [
      (if Set.mem c.universe r.Order.Request.symbol then None
       else fail "universe" (sprintf "%s is not in the book's universe" sym));
      (if r.Order.Request.qty > 0 then None
       else fail "whole_shares" (sprintf "%d shares is not a positive whole number" r.Order.Request.qty));
      (match (c.spec.Desk_spec.trading, c.can_trade) with
      | Desk_spec.Disabled, _ -> fail "trading" "the book disables trading"
      | Desk_spec.Enabled, Error why -> fail "trading" why
      | Desk_spec.Enabled, Ok () -> None);
      Option.map c.halted ~f:(fun why -> { Failure.rule = "kill_switch"; why });
      (if c.session_open then None else fail "session" "the regular session is closed");
      (match (c.mark, c.stale) with
      | None, _ -> fail "mark" (sprintf "%s has no mark" sym)
      | Some _, true -> fail "mark" (sprintf "%s's mark is stale" sym)
      | Some _, false -> None);
      (match limit with
      | Some p when Float.( >= ) p 1.0 && Float.( > ) (Float.abs ((p *. 100.0) -. Float.round_nearest (p *. 100.0))) 1e-6 ->
          fail "tick" (sprintf "a limit of %g is not a whole cent" p)
      | _ -> None);
      (match (limit, priced) with
      | Some p, Some m when Float.( > ) (Float.abs (p -. m) /. m) (c.spec.Desk_spec.price_collar +. 1e-12) ->
          fail "collar"
            (sprintf "a limit of %.2f is %.2f%% from the mark of %.2f; the collar is %.2f%%" p
               (100.0 *. Float.abs (p -. m) /. m)
               m (100.0 *. c.spec.Desk_spec.price_collar))
      | _ -> None);
      (match priced with
      | Some m when Float.( > ) (Float.of_int r.Order.Request.qty *. m) c.spec.Desk_spec.max_order_notional ->
          fail "notional"
            (sprintf "%d x %.2f = %.2f exceeds the order cap of %.2f" r.Order.Request.qty m
               (Float.of_int r.Order.Request.qty *. m)
               c.spec.Desk_spec.max_order_notional)
      | _ -> None);
      (match c.adv20 with
      | None -> fail "adv" (sprintf "%s's twenty-day volume is unknown" sym)
      | Some adv when Float.( > ) (Float.of_int r.Order.Request.qty) (c.spec.Desk_spec.max_adv_participation *. adv) ->
          fail "adv"
            (sprintf "%d shares is more than %.2f%% of %s's twenty-day volume of %.0f" r.Order.Request.qty
               (100.0 *. c.spec.Desk_spec.max_adv_participation)
               sym adv)
      | Some _ -> None);
      (if
         List.exists c.recent ~f:(fun x ->
             Symbol.equal x.Recent.symbol r.Order.Request.symbol
             && Order.Side.equal x.Recent.side r.Order.Request.side
             && x.Recent.qty = r.Order.Request.qty
             && Float.( < ) (Time_ns.Span.to_sec (Time_ns.diff c.now x.Recent.at)) c.spec.Desk_spec.duplicate_window_s)
       then
         fail "duplicate"
           (sprintf "the same order was proposed less than %g s ago" c.spec.Desk_spec.duplicate_window_s)
       else None);
      (if c.open_orders < c.spec.Desk_spec.max_open_orders then None
       else fail "open_orders" (sprintf "%d orders are already open; the cap is %d" c.open_orders c.spec.Desk_spec.max_open_orders));
    ]
```

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 403` (394 + 9).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | tail -3
git add desk/rules.ml test/test_rules.ml test/test_ohcamel.ml lib/verified.ml
git commit -F - <<'EOF'
desk: the rules an order must pass before the gate is asked, every failure named at once, because a person fixing a ticket should be told everything wrong with it in one answer

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 8: The venue's trading half, and the simulated venue's

**Files:**
- Modify: `desk/venue.ml`, `desk/sim_venue.ml`
- Create: `test/test_sim_trade.ml`
- Modify: `test/test_ohcamel.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: `Order` (Side, Kind, Request, Fill, Event), `Ids.Client_order_id`.
- Produces (in `Ohcamel_desk.Venue`):
  - `Venue_order.t = { id : string; client_order_id : string; symbol : Symbol.t; side : Order.Side.t; qty : float; filled_qty : float; filled_avg_price : float option; status : string; limit_price : float option }`.
  - `Submission.t = Accepted of Venue_order.t | Rejected of string | Unknown of string`.
  - `Update.t = { event : string; order : Venue_order.t; fill : Order.Fill.t option; at : Time_ns.t }`; `Update.to_event : Update.t -> Order.Event.t option` — `new`/`accepted`/`pending_new` → `Venue_accepted`; `fill`/`partial_fill` with a fill → `Venue_fill`; `canceled` → `Venue_cancelled`; `expired` → `Venue_expired`; `rejected` → `Venue_rejected "rejected by the venue"`; every other event → `None`.
  - `Trade.t = { submit : Order.Request.t -> Submission.t Deferred.t; cancel : string -> unit Or_error.t Deferred.t; find_order : Ids.Client_order_id.t -> Venue_order.t option Or_error.t Deferred.t; open_orders : unit -> Venue_order.t list Or_error.t Deferred.t; updates : Update.t Pipe.Reader.t }`.
- Produces (in `Ohcamel_desk.Sim_venue`): `create` gains `?latency:Time_ns.Span.t` (default 150 ms); `submit_now : t -> Order.Request.t -> Venue.Submission.t` (records the order as `new`, with id `sim-<n>`); `cancel_now : t -> string -> Venue.Update.t Or_error.t`; `find_now : t -> Ids.Client_order_id.t -> Venue.Venue_order.t option`; `open_now : t -> Venue.Venue_order.t list`; `step : t -> Venue.Update.t list` (fills every order whose latency has elapsed and whose price condition holds at the current marks, updating positions and cash); `pump : t -> unit` (runs `step` and writes its updates to the pipe of the most recent `trade`); `received : t -> int` (orders ever submitted); `trade : ?auto:bool -> t -> Venue.Trade.t` (the Async face: `submit` returns `submit_now` and writes a `new` update; `cancel` writes the `canceled` update; with `auto`, the default, a 100 ms `Clock_ns.every` runs `pump`).

  Fills: a market buy at mark x (1 + h), a market sell at mark x (1 - h), where h is the symbol's half-spread in bps / 10,000; a limit buy when mark x (1 + h) <= limit, at mark x (1 + h); a limit sell when mark x (1 - h) >= limit, at mark x (1 - h). The whole quantity fills at once; the execution id is `<order id>-1`; `position_qty` is the position after the fill. (Spec §3.4 says a limit fills when the mark crosses it; here the side of the quote the order trades against must reach it, the rule a market order already pays by -- otherwise a buy limit at the mark would fill half a spread better than a market buy. Recorded as a ruling in this plan's ledger.)

- [ ] **Step 1: Write the failing tests.** `test/test_sim_trade.ml`:

```ocaml
(* The simulated venue's trading half, with its clock and its marks in the
   test's hands.

   Cash 100,000, no positions, a half-spread of 5 bps, 200 ms of latency, and
   the arithmetic of every fill written beside it. *)

open Core
open Ohcamel.Types
module Sim = Ohcamel_desk.Sim_venue
module Venue = Ohcamel_desk.Venue
module Order = Ohcamel_desk.Order
module Ids = Ohcamel_desk.Ids

let aapl = Symbol.of_string "AAPL"
let t0 = Time_ns.of_string_with_utc_offset "2026-09-14T14:00:00Z"

let fixture () =
  let now = ref t0 and mark = ref 150.0 in
  let venue =
    Sim.create ~latency:(Time_ns.Span.of_ms 200.0) ~opened_at:t0
      ~marks:(fun s -> if Symbol.equal s aapl then Some (Price.of_float !mark) else None)
      ~now:(fun () -> !now) ~half_spread_bps:(fun _ -> 5.0) ~cash:(Notional.of_float 100_000.0) ~positions:[] ()
  in
  (venue, now, mark)

let id n = Option.value_exn (Ids.Client_order_id.of_string (sprintf "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ%d" n))
let request ?(side = Order.Side.Buy) ?(kind = Order.Kind.Market) n qty =
  { Order.Request.client_order_id = id n; symbol = aapl; side; qty; kind }

let later now ms = now := Time_ns.add t0 (Time_ns.Span.of_ms ms)

let test_a_market_buy_fills_at_the_ask_after_the_latency () =
  let venue, now, _ = fixture () in
  (match Sim.submit_now venue (request 1 10) with
  | Venue.Submission.Accepted o -> Alcotest.(check string) "accepted as new" "new" o.Venue.Venue_order.status
  | _ -> Alcotest.fail "the simulated venue refused a plain market order");
  later now 100.0;
  Alcotest.(check int) "nothing at 100 ms" 0 (List.length (Sim.step venue));
  later now 250.0;
  match Sim.step venue with
  | [ { Venue.Update.event = "fill"; fill = Some f; _ } ] ->
      (* 150 x (1 + 5/10,000) = 150.075 *)
      Alcotest.(check (float 1e-9)) "at the ask" 150.075 (Price.to_float f.Order.Fill.price);
      Alcotest.(check (option (float 0.0))) "position 10" (Some 10.0) f.Order.Fill.position_qty;
      let a = Or_error.ok_exn (Sim.account venue) in
      (* cash 100,000 - 10 x 150.075 = 98,499.25; equity 98,499.25 + 1,500 = 99,999.25 *)
      Alcotest.(check (float 1e-6)) "cash" 98_499.25 (Notional.to_float a.Venue.Account.cash);
      Alcotest.(check (float 1e-6)) "equity is down exactly the half-spread" 99_999.25 (Notional.to_float a.Venue.Account.equity)
  | updates -> Alcotest.failf "expected one fill at 250 ms, got %d updates" (List.length updates)

let test_a_limit_buy_waits_for_the_ask_to_reach_it () =
  let venue, now, mark = fixture () in
  ignore (Sim.submit_now venue (request ~kind:(Order.Kind.Limit (Price.of_float 149.0)) 2 5) : Venue.Submission.t);
  later now 300.0;
  (* ask 150.075 > 149: no fill *)
  Alcotest.(check int) "not at 150" 0 (List.length (Sim.step venue));
  mark := 148.9;
  match Sim.step venue with
  | [ { Venue.Update.fill = Some f; _ } ] ->
      (* ask 148.9 x 1.0005 = 148.97445 <= 149 *)
      Alcotest.(check (float 1e-9)) "at the ask" 148.97445 (Price.to_float f.Order.Fill.price)
  | _ -> Alcotest.fail "the limit did not fill once the ask reached it"

let test_a_sell_from_flat_is_a_short () =
  let venue, now, _ = fixture () in
  ignore (Sim.submit_now venue (request ~side:Order.Side.Sell 3 5) : Venue.Submission.t);
  later now 250.0;
  match Sim.step venue with
  | [ { Venue.Update.fill = Some f; _ } ] ->
      (* bid 150 x 0.9995 = 149.925; cash 100,000 + 5 x 149.925 = 100,749.625 *)
      Alcotest.(check (option (float 0.0))) "position -5" (Some (-5.0)) f.Order.Fill.position_qty;
      Alcotest.(check (float 1e-6)) "cash" 100_749.625 (Notional.to_float (Or_error.ok_exn (Sim.account venue)).Venue.Account.cash)
  | _ -> Alcotest.fail "the sell did not fill"

let test_a_cancelled_order_never_fills () =
  let venue, now, _ = fixture () in
  let o = match Sim.submit_now venue (request 4 10) with Venue.Submission.Accepted o -> o | _ -> Alcotest.fail "refused" in
  (match Sim.cancel_now venue o.Venue.Venue_order.id with
  | Ok u -> Alcotest.(check string) "canceled" "canceled" u.Venue.Update.event
  | Error e -> Alcotest.fail (Error.to_string_hum e));
  later now 500.0;
  Alcotest.(check int) "no fill" 0 (List.length (Sim.step venue));
  Alcotest.(check bool) "and it can be found by its client id, cancelled" true
    (match Sim.find_now venue (id 4) with Some v -> String.equal v.Venue.Venue_order.status "canceled" | None -> false)

let test_venue_events_map_to_order_events () =
  let order = { Venue.Venue_order.id = "v"; client_order_id = "c"; symbol = aapl; side = Order.Side.Buy; qty = 1.0; filled_qty = 0.0; filled_avg_price = None; status = "new"; limit_price = None } in
  let update event fill = { Venue.Update.event; order; fill; at = t0 } in
  let name u = Option.value_map (Venue.Update.to_event u) ~default:"none" ~f:Order.Event.name in
  let f = { Order.Fill.execution_id = "x"; qty = 1.0; price = Price.of_float 1.0; at = t0; position_qty = None } in
  Alcotest.(check (list string)) "the table"
    [ "venue_accepted"; "venue_accepted"; "venue_accepted"; "venue_fill"; "venue_fill"; "venue_cancelled"; "venue_expired"; "venue_rejected"; "none"; "none"; "none" ]
    (List.map ~f:name
       [ update "new" None; update "accepted" None; update "pending_new" None; update "fill" (Some f); update "partial_fill" (Some f);
         update "canceled" None; update "expired" None; update "rejected" None; update "done_for_day" None; update "pending_cancel" None; update "fill" None ])

let suite =
  ( "sim_trade",
    [
      Alcotest.test_case "a market buy fills at the ask after the latency" `Quick test_a_market_buy_fills_at_the_ask_after_the_latency;
      Alcotest.test_case "a limit buy waits for the ask to reach it" `Quick test_a_limit_buy_waits_for_the_ask_to_reach_it;
      Alcotest.test_case "a sell from flat is a short" `Quick test_a_sell_from_flat_is_a_short;
      Alcotest.test_case "a cancelled order never fills" `Quick test_a_cancelled_order_never_fills;
      Alcotest.test_case "venue events map to order events" `Quick test_venue_events_map_to_order_events;
    ] )
```

  Register after `Test_rules.suite;`. (The id strings in `id n` are 26 base-32 symbols after `ohc-`: 10 of timestamp, 15 `Z`s and the digit `n`; `n` must be 0-9.)

- [ ] **Step 2: Run and watch it fail.** Expected: `Unbound value Sim.submit_now`.

- [ ] **Step 3: Implement.**
  In `desk/venue.ml`, add (after `Bar`, before `Read`). `Quote.t` already derives `sexp_of` over a `Time_ns.t` field, so the file carries A1's fix for Core's poisoned `Time_ns.sexp_of_t` -- the file-local `module Time_ns = Time_ns.Alternate_sexp` at desk/venue.ml:23 -- and `Update.t` relies on it. (That alias has no `Span`; nothing added here needs one.)

```ocaml
(* The trading half (design §3.4). Kept apart from [Read] so a desk that can
   read an account and not trade it is a value, not a stub that answers "not
   yet": [Trade.t option] is None, and the rules say why. *)
module Venue_order = struct
  type t = {
    id : string;
    client_order_id : string;
    symbol : Symbol.t;
    side : Order.Side.t;
    qty : float;
    filled_qty : float;
    filled_avg_price : float option;
    status : string;
    limit_price : float option;
  }
  [@@deriving sexp_of, compare, equal]
end

module Submission = struct
  (* Three answers, because a request has three outcomes: the venue has the
     order, the venue refused it, or nobody knows. The third is not an error to
     retry; it is invariant 10's unknown, resolved only by asking. *)
  type t = Accepted of Venue_order.t | Rejected of string | Unknown of string [@@deriving sexp_of]
end

module Update = struct
  type t = { event : string; order : Venue_order.t; fill : Order.Fill.t option; at : Time_ns.t } [@@deriving sexp_of]

  (* Alpaca's trade_updates vocabulary, onto the state machine's. Events that
     change nothing the machine models -- done_for_day, pending_cancel,
     calculated -- map to None and are logged by the caller, not dropped
     silently. A fill event without a fill is None too: the machine counts
     executions, and an event that names one without describing it has
     nothing to count. *)
  let to_event (t : t) : Order.Event.t option =
    match (t.event, t.fill) with
    | ("new" | "accepted" | "pending_new"), _ -> Some Order.Event.Venue_accepted
    | ("fill" | "partial_fill"), Some f -> Some (Order.Event.Venue_fill f)
    | "canceled", _ -> Some Order.Event.Venue_cancelled
    | "expired", _ -> Some Order.Event.Venue_expired
    | "rejected", _ -> Some (Order.Event.Venue_rejected "rejected by the venue")
    | _ -> None
end

module Trade = struct
  type t = {
    submit : Order.Request.t -> Submission.t Deferred.t;
    cancel : string -> unit Or_error.t Deferred.t;
    find_order : Ids.Client_order_id.t -> Venue_order.t option Or_error.t Deferred.t;
    open_orders : unit -> Venue_order.t list Or_error.t Deferred.t;
    updates : Update.t Pipe.Reader.t;
  }
end
```

  In `desk/sim_venue.ml`, extend `t` with `latency : Time_ns.Span.t; mutable next_id : int; mutable orders : (Venue.Venue_order.t * Order.Request.t * Time_ns.t) String.Map.t` (keyed by venue id; the time is when it becomes fillable) and `mutable by_client : string String.Map.t` (client id to venue id) and `mutable updates : Venue.Update.t Pipe.Writer.t option` (None until `trade`); give `create` `?(latency = Time_ns.Span.of_ms 150.0)`; then add:

```ocaml
let half t symbol = t.half_spread_bps symbol /. 10_000.0

let submit_now (t : t) (r : Order.Request.t) : Venue.Submission.t =
  t.next_id <- t.next_id + 1;
  let id = sprintf "sim-%d" t.next_id in
  let order =
    {
      Venue.Venue_order.id;
      client_order_id = Ids.Client_order_id.to_string r.Order.Request.client_order_id;
      symbol = r.Order.Request.symbol;
      side = r.Order.Request.side;
      qty = Float.of_int r.Order.Request.qty;
      filled_qty = 0.0;
      filled_avg_price = None;
      status = "new";
      limit_price = Option.map (Order.Kind.limit_price r.Order.Request.kind) ~f:Price.to_float;
    }
  in
  t.orders <- Map.set t.orders ~key:id ~data:(order, r, Time_ns.add (t.now ()) t.latency);
  t.by_client <- Map.set t.by_client ~key:order.Venue.Venue_order.client_order_id ~data:id;
  Venue.Submission.Accepted order

let find_now (t : t) (client : Ids.Client_order_id.t) : Venue.Venue_order.t option =
  Option.bind (Map.find t.by_client (Ids.Client_order_id.to_string client)) ~f:(fun id ->
      Option.map (Map.find t.orders id) ~f:(fun (o, _, _) -> o))

let open_now (t : t) : Venue.Venue_order.t list =
  Map.data t.orders
  |> List.filter_map ~f:(fun (o, _, _) ->
         if List.mem [ "new"; "partially_filled" ] o.Venue.Venue_order.status ~equal:String.equal then Some o else None)

let cancel_now (t : t) (id : string) : Venue.Update.t Or_error.t =
  match Map.find t.orders id with
  | Some (o, r, due) when String.equal o.Venue.Venue_order.status "new" ->
      let o = { o with Venue.Venue_order.status = "canceled" } in
      t.orders <- Map.set t.orders ~key:id ~data:(o, r, due);
      Ok { Venue.Update.event = "canceled"; order = o; fill = None; at = t.now () }
  | Some _ -> Or_error.errorf "simulated venue: order %s is not cancelable" id
  | None -> Or_error.errorf "simulated venue: no order %s" id

let step (t : t) : Venue.Update.t list =
  let now = t.now () in
  Map.to_alist t.orders
  |> List.filter_map ~f:(fun (id, (o, r, due)) ->
         if (not (String.equal o.Venue.Venue_order.status "new")) || Time_ns.( < ) now due then None
         else
           match t.marks o.Venue.Venue_order.symbol with
           | None -> None
           | Some mark -> (
               let m = Price.to_float mark and h = half t o.Venue.Venue_order.symbol in
               let price =
                 match (o.Venue.Venue_order.side, o.Venue.Venue_order.limit_price) with
                 | Order.Side.Buy, None -> Some (m *. (1.0 +. h))
                 | Order.Side.Sell, None -> Some (m *. (1.0 -. h))
                 | Order.Side.Buy, Some l -> if Float.( <= ) (m *. (1.0 +. h)) l then Some (m *. (1.0 +. h)) else None
                 | Order.Side.Sell, Some l -> if Float.( >= ) (m *. (1.0 -. h)) l then Some (m *. (1.0 -. h)) else None
               in
               match price with
               | None -> None
               | Some price ->
                   let signed = Order.Side.sign o.Venue.Venue_order.side *. o.Venue.Venue_order.qty in
                   let position =
                     Qty.add (Option.value (Map.find t.positions o.Venue.Venue_order.symbol) ~default:Qty.zero) (Qty.of_float signed)
                   in
                   t.positions <- Map.set t.positions ~key:o.Venue.Venue_order.symbol ~data:position;
                   t.cash <- Notional.sub t.cash (Notional.of_float (signed *. price));
                   let o =
                     { o with Venue.Venue_order.status = "filled"; filled_qty = o.Venue.Venue_order.qty; filled_avg_price = Some price }
                   in
                   t.orders <- Map.set t.orders ~key:id ~data:(o, r, due);
                   Some
                     {
                       Venue.Update.event = "fill";
                       order = o;
                       fill =
                         Some
                           {
                             Order.Fill.execution_id = id ^ "-1";
                             qty = o.Venue.Venue_order.qty;
                             price = Price.of_float price;
                             at = now;
                             position_qty = Some (Qty.to_float position);
                           };
                       at = now;
                     }))

let received (t : t) = t.next_id

(* Updates go to the pipe of the most recent [trade]: a test that builds a
   second order manager over the same venue -- a restart -- hears the venue on
   the new pipe, as a restarted process hears it on a new socket. *)
let emit (t : t) (u : Venue.Update.t) =
  Option.iter t.updates ~f:(fun w -> Pipe.write_without_pushback_if_open w u)

let pump (t : t) = List.iter (step t) ~f:(emit t)

(* [auto] is false only in tests, which pump by hand so every fill happens
   where the test says. *)
let trade ?(auto = true) (t : t) : Venue.Trade.t =
  let reader, writer = Pipe.create () in
  t.updates <- Some writer;
  if auto then Clock_ns.every (Time_ns.Span.of_ms 100.0) (fun () -> pump t);
  {
    Venue.Trade.submit =
      (fun r ->
        let s = submit_now t r in
        (match s with
        | Venue.Submission.Accepted o -> emit t { Venue.Update.event = "new"; order = o; fill = None; at = t.now () }
        | _ -> ());
        return s);
    cancel = (fun id -> return (Or_error.map (cancel_now t id) ~f:(emit t)));
    find_order = (fun c -> return (Ok (find_now t c)));
    open_orders = (fun () -> return (Ok (open_now t)));
    updates = reader;
  }
```

  (`cash` and `positions` are already `mutable` in A1's `t` (desk/sim_venue.ml:30-31), and A1's `positions t` and `account t` read the same fields. The `auto` pump runs on the wall clock and only the demo uses it: every test passes `~auto:false` and pumps by hand, so no test waits on it.)

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 408` (403 + 5).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | tail -3
git add desk/venue.ml desk/sim_venue.ml test/test_sim_trade.ml test/test_ohcamel.ml lib/verified.ml
git commit -F - <<'EOF'
desk: the venue's trading half, and a simulated venue that fills at the quote after a latency, because the order manager needs a venue it can be tested against to the cent

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 9: Orders, events and fills in the journal

**Files:**
- Modify: `desk/journal.ml`, `desk/journal.mli` (Task 2's interface)
- Create: `test/test_journal_orders.ml`
- Modify: `test/test_ohcamel.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: `Order` (all of it, with Task 1's venue-id rule and exact anomaly figure), `Ids.Client_order_id`, the A1 schema's `orders`, `order_events` and `fills` tables (desk/journal.ml:119-130), and the helpers Task 2 left private behind `desk/journal.mli`.
- Produces (in `Ohcamel_desk.Journal`, each also declared in `desk/journal.mli`):
  - `Order_row.t = { order : Order.t; source : string; decision_price : Price.t; arrival : (Price.t * Price.t) option; verdict : Yojson.Safe.t; created_at : Time_ns.t; updated_at : Time_ns.t }`.
  - `Fill_row.t = { client_order_id : Ids.Client_order_id.t; symbol : Symbol.t; side : Order.Side.t; fill : Order.Fill.t; decision_price : Price.t; arrival : (Price.t * Price.t) option }` -- the order's decision price and arrival quote joined in, because every reader of a fill row is pricing its cost.
  - `insert_order : t -> Order.t -> source:string -> decision_price:Price.t -> arrival:(Price.t * Price.t) option -> verdict:Yojson.Safe.t -> at:Time_ns.t -> unit` (one transaction: the orders row and a `created` event).
  - `update_order : t -> Order.t -> event:Order.Event.t -> anomaly:Order.Anomaly.t option -> at:Time_ns.t -> unit` (one transaction: the row's state, venue id, filled quantity and average price, and one `order_events` row whose `detail` is a JSON object with `"reason"` -- the order's reason after the event, whenever it has one -- `"venue_order_id"` for `Acknowledged` and `Found`, and `"execution_id"` for a fill; NULL when none applies. The order's reason rather than the event's, so an illegal event that carried a reason can never become the reason a restart reads back).
  - `record_fill : t -> Order.t -> Order.Fill.t -> bool` (`INSERT OR IGNORE`; true when the execution id is new).
  - `load_order : t -> Ids.Client_order_id.t -> Order_row.t option` (rebuilds `Order.t`: request and state from the row, `filled_qty` and `filled_notional` and `execution_ids` from the fills table, `reason` from the latest event detail carrying one).
  - `open_orders : t -> Order_row.t list` (non-terminal states, oldest first).
  - `recent_orders : t -> limit:int -> Order_row.t list` (newest first); `recent_fills : t -> limit:int -> Fill_row.t list` (newest first).

- [ ] **Step 1: Write the failing tests.** `test/test_journal_orders.ml`:

```ocaml
(* Orders in the journal: what the order manager writes before the wire and
   what a restart reads back.

   The round trip is checked against the state machine itself: an order is
   driven through Order.apply, journaled at every step, and the row read back
   must rebuild the same order -- state, venue id, filled quantity, average
   price, the set of executions, the reason. *)

open Core
open Ohcamel.Types
module Journal = Ohcamel_desk.Journal
module Order = Ohcamel_desk.Order
module Ids = Ohcamel_desk.Ids

let at = Time_ns.of_string_with_utc_offset "2026-09-14T14:31:00Z"
let id n = Option.value_exn (Ids.Client_order_id.of_string (sprintf "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ%d" n))

let request n qty =
  { Order.Request.client_order_id = id n; symbol = Symbol.of_string "AAPL"; side = Order.Side.Buy; qty; kind = Order.Kind.Market }

let open_journal () = Or_error.ok_exn (Journal.open_ ~path:":memory:")

let insert j o = Journal.insert_order j o ~source:"manual" ~decision_price:(Price.of_float 100.0) ~arrival:None ~verdict:`Null ~at

let step j o event =
  let o, anomaly = Order.apply o event in
  (match event with Order.Event.Venue_fill f -> ignore (Journal.record_fill j o f : bool) | _ -> ());
  Journal.update_order j o ~event ~anomaly ~at;
  o

let fill id qty price = Order.Event.Venue_fill { Order.Fill.execution_id = id; qty; price = Price.of_float price; at; position_qty = None }

let test_an_order_round_trips_through_the_journal () =
  let j = open_journal () in
  let o = Order.create (request 1 100) in
  insert j o;
  let o = List.fold [ Order.Event.Acknowledged "venue-1"; Order.Event.Venue_accepted; fill "x1" 40.0 100.0; fill "x2" 60.0 100.5 ] ~init:o ~f:(step j) in
  let back = (Option.value_exn (Journal.load_order j (id 1))).Journal.Order_row.order in
  Alcotest.(check string) "state" "filled" (Order.State.to_string back.Order.state);
  Alcotest.(check (option string)) "venue id" (Some "venue-1") back.Order.venue_order_id;
  Alcotest.(check (float 1e-9)) "filled 100" 100.0 back.Order.filled_qty;
  (* (40 x 100 + 60 x 100.5) / 100 = 100.30 *)
  Alcotest.(check (option (float 1e-9))) "average 100.30" (Some 100.30) (Order.avg_fill_price back);
  Alcotest.(check (list string)) "executions" [ "x1"; "x2" ] (Set.to_list back.Order.execution_ids);
  Alcotest.(check bool) "the rebuilt order equals the machine's" true
    (Float.equal back.Order.filled_notional o.Order.filled_notional && Order.State.equal back.Order.state o.Order.state)

let test_a_fill_is_recorded_once () =
  let j = open_journal () in
  let o = Order.create (request 2 10) in
  insert j o;
  let f = { Order.Fill.execution_id = "x1"; qty = 10.0; price = Price.of_float 50.0; at; position_qty = Some 10.0 } in
  Alcotest.(check bool) "new" true (Journal.record_fill j o f);
  Alcotest.(check bool) "a replay is not" false (Journal.record_fill j o f);
  Alcotest.(check int) "one fill on the page" 1 (List.length (Journal.recent_fills j ~limit:10))

let test_open_orders_are_the_non_terminal_ones_oldest_first () =
  let j = open_journal () in
  let o1 = Order.create (request 3 10) and o2 = Order.create (request 4 10) and o3 = Order.create (request 5 10) in
  List.iter [ o1; o2; o3 ] ~f:(insert j);
  ignore (step j o1 (Order.Event.Acknowledged "a") : Order.t);
  ignore (List.fold [ Order.Event.Acknowledged "b"; fill "y" 10.0 1.0 ] ~init:o2 ~f:(step j) : Order.t);
  ignore (step j o3 (Order.Event.Outcome_unknown "timed out") : Order.t);
  Alcotest.(check (list string)) "submitted and unknown are open; filled is not"
    [ "submitted"; "submit_unknown" ]
    (List.map (Journal.open_orders j) ~f:(fun r -> Order.State.to_string r.Journal.Order_row.order.Order.state))

let test_a_venue's_reason_survives_the_restart () =
  let j = open_journal () in
  let o = Order.create (request 6 10) in
  insert j o;
  ignore (step j o (Order.Event.Venue_rejected_submission "403: insufficient buying power") : Order.t);
  Alcotest.(check (option string)) "the reason" (Some "403: insufficient buying power")
    (Option.value_exn (Journal.load_order j (id 6))).Journal.Order_row.order.Order.reason

(* Spec §6: a journal round trip is the identity. Whatever events the machine
   is given -- legal or not, fills replayed or late -- journaled step by step,
   the row reads back as the order the machine holds. *)
let prop_a_journal_round_trip_is_the_identity =
  let open QCheck in
  let event =
    Gen.(
      oneof_weighted
        [
          (2, return (Order.Event.Acknowledged "venue-9"));
          (1, return Order.Event.Venue_accepted);
          (1, map (fun why -> Order.Event.Outcome_unknown why) (oneofl [ "timed out"; "503" ]));
          (1, return (Order.Event.Found "venue-9"));
          (1, return Order.Event.Not_found);
          (1, return Order.Event.Cancel_requested);
          (1, return Order.Event.Venue_cancelled);
          (1, map (fun why -> Order.Event.Venue_rejected why) (oneofl [ "halted"; "no borrow" ]));
          (4, map2 (fun n q -> fill (sprintf "x%d" n) (Float.of_int q) (100.0 +. Float.of_int n)) (int_bound 5) (int_range 1 6));
        ])
  in
  QCheck_alcotest.to_alcotest
    (Test.make ~name:"a journal round trip is the identity, whatever the events" ~count:200
       (make Gen.(list_size (int_range 0 12) event))
       (fun events ->
         let j = open_journal () in
         let o = Order.create (request 7 10) in
         insert j o;
         let o = List.fold events ~init:o ~f:(step j) in
         let back = (Option.value_exn (Journal.load_order j (id 7))).Journal.Order_row.order in
         Journal.close j;
         Order.State.equal back.Order.state o.Order.state
         && Option.equal String.equal back.Order.venue_order_id o.Order.venue_order_id
         && Float.( < ) (Float.abs (back.Order.filled_qty -. o.Order.filled_qty)) 1e-9
         && Float.( < ) (Float.abs (back.Order.filled_notional -. o.Order.filled_notional)) 1e-6
         && Set.equal back.Order.execution_ids o.Order.execution_ids
         && Option.equal String.equal back.Order.reason o.Order.reason))

let suite =
  ( "journal_orders",
    [
      Alcotest.test_case "an order round-trips through the journal" `Quick test_an_order_round_trips_through_the_journal;
      Alcotest.test_case "a fill is recorded once" `Quick test_a_fill_is_recorded_once;
      Alcotest.test_case "open orders are the non-terminal ones, oldest first" `Quick
        test_open_orders_are_the_non_terminal_ones_oldest_first;
      Alcotest.test_case "a venue's reason survives the restart" `Quick test_a_venue's_reason_survives_the_restart;
      prop_a_journal_round_trip_is_the_identity;
    ] )
```

  Register after `Test_sim_trade.suite;`.

- [ ] **Step 2: Run and watch it fail.** Expected: `Unbound value Journal.insert_order`.

- [ ] **Step 3: Implement** in `desk/journal.ml`, using the helpers the file keeps private behind Task 2's interface (`write`, `run`, `query`, `text`, `real`, `opt_real`, `time`, `col_text`, `col_real`, `col_opt_real`, `col_time`). The SQL:

```ocaml
(* insert_order, inside one [write]: *)
"INSERT INTO orders (client_order_id, venue_order_id, source, symbol, side, qty, kind, limit_price, tif, state, filled_qty, avg_fill_price, decision_price, arrival_bid, arrival_ask, verdict, created_at, updated_at) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'day', ?, 0, NULL, ?, ?, ?, ?, ?, ?)"
"INSERT INTO order_events (client_order_id, at, event, state_after, anomaly, detail) VALUES (?, ?, 'created', ?, NULL, NULL)"

(* update_order, inside one [write]: *)
"UPDATE orders SET venue_order_id = ?, state = ?, filled_qty = ?, avg_fill_price = ?, updated_at = ? WHERE client_order_id = ?"
"INSERT INTO order_events (client_order_id, at, event, state_after, anomaly, detail) VALUES (?, ?, ?, ?, ?, ?)"

(* record_fill, inside one [write]; true when [Sqlite3.changes db] is 1: *)
"INSERT OR IGNORE INTO fills (execution_id, client_order_id, symbol, side, qty, price, at, position_qty) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"

(* load_order: *)
"SELECT client_order_id, venue_order_id, source, symbol, side, qty, kind, limit_price, state, decision_price, arrival_bid, arrival_ask, verdict, created_at, updated_at FROM orders WHERE client_order_id = ?"
"SELECT execution_id, qty, price, at, position_qty FROM fills WHERE client_order_id = ? ORDER BY at, execution_id"
"SELECT detail FROM order_events WHERE client_order_id = ? AND detail LIKE '%\"reason\"%' ORDER BY seq DESC LIMIT 1"

(* open_orders: *)
"SELECT client_order_id FROM orders WHERE state NOT IN ('rejected_pre_trade','filled','cancelled','expired','rejected_by_venue','failed') ORDER BY created_at, client_order_id"

(* recent_fills: *)
"SELECT f.execution_id, f.client_order_id, f.symbol, f.side, f.qty, f.price, f.at, f.position_qty, o.decision_price, o.arrival_bid, o.arrival_ask FROM fills f JOIN orders o ON o.client_order_id = f.client_order_id ORDER BY f.at DESC, f.execution_id DESC LIMIT ?"
```

  `load_order` builds `Order.t` directly (the record's fields are public): `request` from the row (`kind` is `Limit (Price.of_float limit_price)` when `kind = "limit"`), `state` from `Order.State.of_string`, `venue_order_id`, `filled_qty` = the sum of fill quantities, `filled_notional` = the sum of quantity x price, `execution_ids` from the fills, `reason` from the event detail's `"reason"`. `recent_orders` selects client ids newest first and maps `load_order`. The event name written is `Order.Event.name event`. The anomaly is `Order.Anomaly.to_string`, whose overfill figure Task 1 made exact (`%.17g`), so the journal's text round-trips the float (A1's ledger, line 43). Every write goes through `write`, so the version counter moves once per call.

  Declare the additions in `desk/journal.mli`, after `recent_alerts` and above `module For_testing`:

```ocaml
(** A journaled order, rebuilt: the request and state from its row, the
    filled quantity, notional and executions from its fills, the reason from
    its latest event that carries one. *)
module Order_row : sig
  type t = {
    order : Order.t;
    source : string;
    decision_price : Price.t;
    arrival : (Price.t * Price.t) option;
    verdict : Yojson.Safe.t;
    created_at : Time_ns.t;
    updated_at : Time_ns.t;
  }
end

(** A fill, with its order's decision price and arrival quote joined in. *)
module Fill_row : sig
  type t = {
    client_order_id : Ids.Client_order_id.t;
    symbol : Symbol.t;
    side : Order.Side.t;
    fill : Order.Fill.t;
    decision_price : Price.t;
    arrival : (Price.t * Price.t) option;
  }
end

(** One transaction: the orders row and its [created] event. The order
    manager calls this before the request that submits the order is sent. *)
val insert_order :
  t ->
  Order.t ->
  source:string ->
  decision_price:Price.t ->
  arrival:(Price.t * Price.t) option ->
  verdict:Yojson.Safe.t ->
  at:Time_ns.t ->
  unit

(** One transaction: the row's state, venue id, filled quantity and average
    price, and one order_events row. *)
val update_order : t -> Order.t -> event:Order.Event.t -> anomaly:Order.Anomaly.t option -> at:Time_ns.t -> unit

(** [INSERT OR IGNORE]: true when the execution id is new. *)
val record_fill : t -> Order.t -> Order.Fill.t -> bool

val load_order : t -> Ids.Client_order_id.t -> Order_row.t option

(** Non-terminal states, oldest first. *)
val open_orders : t -> Order_row.t list

(** Newest first. *)
val recent_orders : t -> limit:int -> Order_row.t list

(** Newest first. *)
val recent_fills : t -> limit:int -> Fill_row.t list
```

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 413` (408 + 5).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | tail -3
git add desk/journal.ml desk/journal.mli test/test_journal_orders.ml test/test_ohcamel.ml lib/verified.ml
git commit -F - <<'EOF'
desk: orders, their events and their fills in the journal, rebuilt from the fills rather than from a stored average, because a restart has to recover the order the machine had and not an approximation of it

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 10: Transaction cost analysis

**Files:**
- Create: `desk/tca.ml`, `test/test_tca.ml`
- Modify: `test/test_ohcamel.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: `Order.Side`.
- Produces (module `Ohcamel_desk.Tca`):
  - `Inputs.t = { symbol : Symbol.t; side : Order.Side.t; qty : float; decision : float; bid : float option; ask : float option; fill : float }`.
  - `Costs.t = { shortfall_bps : float; delay_bps : float option; slippage_bps : float option; half_spread_bps : float option; versus_model_bps : float option }`.
  - `of_fill : model_half_spread_bps:float -> Inputs.t -> Costs.t` — the formulas of spec §3.9; the four quote-dependent fields are `None` without both sides of a quote.
  - `Summary.t = { count : int; mean_shortfall_bps : float option; median_shortfall_bps : float option; weighted_shortfall_bps : float option; mean_versus_model_bps : float option }`; `summarize : (Inputs.t * Costs.t) list -> Summary.t`; `by_symbol : (Inputs.t * Costs.t) list -> (Symbol.t * Summary.t) list` (sorted by symbol).

- [ ] **Step 1: Write the failing tests.** `test/test_tca.ml`:

```ocaml
(* Costs in basis points, against a buy and a sell written out by hand.

   Positive is cost, for either side: a buy that fills above its decision
   price and a sell that fills below its decision price both cost. *)

open Core
open Ohcamel.Types
module Tca = Ohcamel_desk.Tca
module Order = Ohcamel_desk.Order

let aapl = Symbol.of_string "AAPL"
let bps = Alcotest.(option (float 1e-6))

let buy =
  { Tca.Inputs.symbol = aapl; side = Order.Side.Buy; qty = 10.0; decision = 100.00; bid = Some 100.02; ask = Some 100.06; fill = 100.07 }

let test_a_buy () =
  let c = Tca.of_fill ~model_half_spread_bps:2.0 buy in
  (* (100.07 - 100.00) / 100.00 x 10,000 = 7.0 *)
  Alcotest.(check (float 1e-6)) "shortfall" 7.0 c.Tca.Costs.shortfall_bps;
  (* mid (100.02 + 100.06) / 2 = 100.04; (100.04 - 100.00) / 100.00 x 10,000 = 4.0 *)
  Alcotest.check bps "delay" (Some 4.0) c.Tca.Costs.delay_bps;
  (* (100.07 - 100.04) / 100.04 x 10,000 = 2.998800479808... *)
  Alcotest.check bps "slippage" (Some (0.03 /. 100.04 *. 10_000.0)) c.Tca.Costs.slippage_bps;
  (* (100.06 - 100.02) / 2 / 100.04 x 10,000 = 1.999200319872... *)
  Alcotest.check bps "half spread" (Some (0.02 /. 100.04 *. 10_000.0)) c.Tca.Costs.half_spread_bps;
  (* slippage - 2.0 *)
  Alcotest.check bps "versus model" (Some ((0.03 /. 100.04 *. 10_000.0) -. 2.0)) c.Tca.Costs.versus_model_bps

let test_a_sell_is_the_mirror () =
  let sell = { buy with Tca.Inputs.side = Order.Side.Sell; bid = Some 99.94; ask = Some 99.98; fill = 99.93 } in
  let c = Tca.of_fill ~model_half_spread_bps:2.0 sell in
  (* -1 x (99.93 - 100.00) / 100.00 x 10,000 = 7.0 *)
  Alcotest.(check (float 1e-6)) "shortfall" 7.0 c.Tca.Costs.shortfall_bps;
  (* mid 99.96: -1 x (99.96 - 100.00) / 100.00 x 10,000 = 4.0 *)
  Alcotest.check bps "delay" (Some 4.0) c.Tca.Costs.delay_bps;
  (* -1 x (99.93 - 99.96) / 99.96 x 10,000 = 3.001200480... *)
  Alcotest.check bps "slippage" (Some (0.03 /. 99.96 *. 10_000.0)) c.Tca.Costs.slippage_bps

let test_without_a_quote_only_shortfall_is_known () =
  let c = Tca.of_fill ~model_half_spread_bps:2.0 { buy with Tca.Inputs.bid = None } in
  Alcotest.(check (float 1e-6)) "shortfall needs only the decision" 7.0 c.Tca.Costs.shortfall_bps;
  Alcotest.check bps "delay unknown" None c.Tca.Costs.delay_bps;
  Alcotest.check bps "versus model unknown" None c.Tca.Costs.versus_model_bps

let test_a_summary_weights_by_quantity () =
  let at shortfall qty = ({ buy with Tca.Inputs.qty }, { (Tca.of_fill ~model_half_spread_bps:2.0 buy) with Tca.Costs.shortfall_bps = shortfall }) in
  let s = Tca.summarize [ at 7.0 10.0; at 3.0 30.0 ] in
  Alcotest.(check int) "two fills" 2 s.Tca.Summary.count;
  (* mean (7 + 3) / 2 = 5; median of two = 5; weighted (7 x 10 + 3 x 30) / 40 = 160 / 40 = 4 *)
  Alcotest.check bps "mean" (Some 5.0) s.Tca.Summary.mean_shortfall_bps;
  Alcotest.check bps "median" (Some 5.0) s.Tca.Summary.median_shortfall_bps;
  Alcotest.check bps "weighted" (Some 4.0) s.Tca.Summary.weighted_shortfall_bps;
  Alcotest.check bps "an empty summary knows nothing" None (Tca.summarize []).Tca.Summary.mean_shortfall_bps;
  let msft = { buy with Tca.Inputs.symbol = Symbol.of_string "MSFT" } in
  Alcotest.(check (list (pair string int)))
    "by symbol: sorted, each summarized alone" [ ("AAPL", 2); ("MSFT", 1) ]
    (List.map
       (Tca.by_symbol [ at 7.0 10.0; (msft, Tca.of_fill ~model_half_spread_bps:2.0 msft); at 3.0 30.0 ])
       ~f:(fun (s, x) -> (Symbol.to_string s, x.Tca.Summary.count)))

let suite =
  ( "tca",
    [
      Alcotest.test_case "a buy" `Quick test_a_buy;
      Alcotest.test_case "a sell is the mirror" `Quick test_a_sell_is_the_mirror;
      Alcotest.test_case "without a quote, only shortfall is known" `Quick test_without_a_quote_only_shortfall_is_known;
      Alcotest.test_case "a summary weights by quantity" `Quick test_a_summary_weights_by_quantity;
    ] )
```

  Register after `Test_journal_orders.suite;`.

- [ ] **Step 2: Run and watch it fail.** Expected: `Unbound module Ohcamel_desk.Tca`.

- [ ] **Step 3: Implement.** `desk/tca.ml`:

```ocaml
(* What an execution cost, in basis points (design §3.9; Perold 1988).

   Shortfall is measured from the DECISION price -- the graph's mark when the
   order was proposed -- because that is the price the desk decided at, and the
   only one it can hold itself to. The venue's quote at submission splits it:
   the market's move between deciding and arriving (delay) and what the fill
   paid against the quote it arrived to (slippage). The two add to the
   shortfall to first order, and both are reported rather than one derived
   from the other.

   Positive is cost for both sides, by multiplying by the side's sign once.

   On the paper account every number here measures Alpaca's fill simulator,
   and the IEX quote is one venue's, not the national best: the page says so
   wherever these appear. *)

open Core
open Ohcamel.Types

module Inputs = struct
  type t = {
    symbol : Symbol.t;
    side : Order.Side.t;
    qty : float;
    decision : float;
    bid : float option;
    ask : float option;
    fill : float;
  }
  [@@deriving sexp_of]
end

module Costs = struct
  type t = {
    shortfall_bps : float;
    delay_bps : float option;
    slippage_bps : float option;
    half_spread_bps : float option;
    versus_model_bps : float option;
  }
  [@@deriving sexp_of]
end

let bp = 10_000.0

let of_fill ~(model_half_spread_bps : float) (i : Inputs.t) : Costs.t =
  let s = Order.Side.sign i.side in
  let shortfall_bps = s *. (i.fill -. i.decision) /. i.decision *. bp in
  match (i.bid, i.ask) with
  | Some b, Some a when Float.( > ) b 0.0 && Float.( > ) a b ->
      let mid = (a +. b) /. 2.0 in
      let slippage = s *. (i.fill -. mid) /. mid *. bp in
      {
        Costs.shortfall_bps;
        delay_bps = Some (s *. (mid -. i.decision) /. i.decision *. bp);
        slippage_bps = Some slippage;
        half_spread_bps = Some ((a -. b) /. 2.0 /. mid *. bp);
        versus_model_bps = Some (slippage -. model_half_spread_bps);
      }
  | _ -> { Costs.shortfall_bps; delay_bps = None; slippage_bps = None; half_spread_bps = None; versus_model_bps = None }

module Summary = struct
  type t = {
    count : int;
    mean_shortfall_bps : float option;
    median_shortfall_bps : float option;
    weighted_shortfall_bps : float option;
    mean_versus_model_bps : float option;
  }
  [@@deriving sexp_of]
end

let mean xs = if List.is_empty xs then None else Some (List.sum (module Float) xs ~f:Fn.id /. Float.of_int (List.length xs))

let median xs =
  match List.sort xs ~compare:Float.compare with
  | [] -> None
  | sorted ->
      let n = List.length sorted in
      if n % 2 = 1 then Some (List.nth_exn sorted (n / 2))
      else Some ((List.nth_exn sorted ((n / 2) - 1) +. List.nth_exn sorted (n / 2)) /. 2.0)

let summarize (rows : (Inputs.t * Costs.t) list) : Summary.t =
  let shortfalls = List.map rows ~f:(fun (_, c) -> c.Costs.shortfall_bps) in
  let qty = List.sum (module Float) rows ~f:(fun (i, _) -> i.Inputs.qty) in
  {
    Summary.count = List.length rows;
    mean_shortfall_bps = mean shortfalls;
    median_shortfall_bps = median shortfalls;
    weighted_shortfall_bps =
      (if Float.( > ) qty 0.0 then
         Some (List.sum (module Float) rows ~f:(fun (i, c) -> i.Inputs.qty *. c.Costs.shortfall_bps) /. qty)
       else None);
    mean_versus_model_bps = mean (List.filter_map rows ~f:(fun (_, c) -> c.Costs.versus_model_bps));
  }

let by_symbol (rows : (Inputs.t * Costs.t) list) : (Symbol.t * Summary.t) list =
  List.sort_and_group rows ~compare:(fun (a, _) (b, _) -> Symbol.compare a.Inputs.symbol b.Inputs.symbol)
  |> List.map ~f:(fun group -> ((fst (List.hd_exn group)).Inputs.symbol, summarize group))
```

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 417` (413 + 4).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | tail -3
git add desk/tca.ml test/test_tca.ml test/test_ohcamel.ml lib/verified.ml
git commit -F - <<'EOF'
desk: implementation shortfall split into delay and slippage per fill, positive as cost for both sides, because an execution the desk cannot price is an execution it cannot learn from

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 11: Alpaca paper, trading half

**Files:**
- Modify: `desk/alpaca_paper.ml`, `desk/dune` (the two websocket libraries, beside Task 5's instrumentation stanza)
- Create: `desk/trade_updates.ml`, `desk/alpaca_trade.ml`, `test/test_alpaca_trading.ml`
- Modify: `test/test_ohcamel.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes:
  - `Venue` (Venue_order, Submission, Update, Trade), `Order`, `Ids`, `Desk_time`;
  - A1's `Alpaca_paper.within` (with Task 4's `?time_source`), `request_timeout`, `get_json`, `trading_uri`, and the parsers' helpers `field`, `string_field`, `decimal`, `optional_decimal` (desk/alpaca_paper.ml:28, :81-114, :251-309);
  - `Cohttp_async.Client.call ?interrupt ?ssl_config ?headers ?chunked ?body meth uri` (_opam/lib/cohttp-async/client.mli), `Cohttp_async.Body.of_string`/`to_string`, `Cohttp.Code.string_of_method`/`code_of_status`. A1's `get_json` (desk/alpaca_paper.ml:273-309) is the pattern `request_json` follows, and it stays exactly as it is;
  - `Ohcamel.Alpaca_ws.Backoff` and `Ohcamel.Alpaca_ws.with_connection` (the market-data client's reconnect schedule and TLS connection, reused).
- Produces (in `Ohcamel_desk.Alpaca_paper`):
  - `order_request_json : Order.Request.t -> Yojson.Safe.t` — `{"symbol", "qty" (string), "side", "type", "time_in_force":"day", "client_order_id"}`, plus `"limit_price"` as a two-decimal string for a limit order.
  - `venue_order_of_json : Yojson.Safe.t -> Venue.Venue_order.t Or_error.t`.
  - `classify_submission : status:int -> body:string -> Venue.Submission.t` — 200 with a readable order: `Accepted`; 200 unreadable: `Unknown`; 400, 401, 403, 404, 409, 422, 429: `Rejected "<status>: <message>"` (Alpaca's `{"code":..,"message":..}` message when present, else the body's first 120 bytes); any other status: `Unknown`.
  - `request_json : ?span:Time_ns.Span.t -> meth:Cohttp.Code.meth -> ?body:string -> credentials:Credentials.t -> Uri.t -> (int * string) Or_error.t Deferred.t` returns the status code and body.
    - It is bounded by `within ~span` (default `request_timeout`), on `Cohttp_async.Client.call ~interrupt:abandon`, and closed the two ways A1's `get_json` is. `~interrupt` aborts a connect still in progress, and closing the body's pipe closes a connection that has answered. A peer that never sends its status line keeps its socket until the peer or the kernel ends it (A1's final review I3, and its re-review's residual 2).
    - `get_json` stays exactly as A1 left it, on `Client.get`.
  - (in `Ohcamel_desk.Alpaca_trade`, for the reason given below) `trade : credentials:Alpaca_paper.Credentials.t -> on_connected:(unit -> unit Deferred.t) -> on_event:(string -> unit) -> Venue.Trade.t` — `submit` POSTs through `request_json ~span:(Time_ns.Span.of_sec 10.0)`: an answer goes to `classify_submission`, and an `Error` -- the bound passing, or a transport exception -- is `Unknown` with its words; `cancel` DELETEs (204 is `Ok`, 422 is `Error "not cancelable: ..."`); `find_order` GETs `/v2/orders:by_client_order_id` (200 is `Some`, 404 is `None`); `open_orders` GETs `/v2/orders?status=open&limit=500&direction=asc`; all four on the same 10 s span; `updates` is fed by `Trade_updates.run`, which calls `on_connected` after each successful listen (the order manager reconciles there).
- Produces (module `Ohcamel_desk.Trade_updates`):
  - `Message.t = Authorized | Unauthorized | Listening of string list | Update of Venue.Update.t | Stream_error of string | Other`; `Message.of_json : Yojson.Safe.t -> Message.t`.
  - `run : credentials:Alpaca_paper.Credentials.t -> writer:Venue.Update.t Pipe.Writer.t -> on_connected:(unit -> unit Deferred.t) -> on_event:(string -> unit) -> unit Deferred.t` (never returns; reconnects with `Alpaca_ws.Backoff.default`).

  `Trade_updates` depends on `Alpaca_paper.Credentials` and `Alpaca_paper.trading_host`, and `Alpaca_paper.trade` calls `Trade_updates.run`: to avoid a cycle, `trade` is defined in a third module, `desk/alpaca_trade.ml` (`Ohcamel_desk.Alpaca_trade.trade` with the signature above), and the pure functions stay in `alpaca_paper.ml`.

- [ ] **Step 1: Write the failing tests.** `test/test_alpaca_trading.ml`:

```ocaml
(* Alpaca paper's trading half, pure parts, against the shapes in Alpaca's
   API reference and streaming guide.

   What matters is where a mistake would cost money in a real account and an
   honest record in a paper one: a quantity or price sent in the wrong form, a
   refusal read as an unknown (and so looked up forever), an unknown read as a
   refusal (and so sent again), and a fill whose position or execution id is
   misread. *)

open Core
open Ohcamel.Types
module Alpaca = Ohcamel_desk.Alpaca_paper
module Updates = Ohcamel_desk.Trade_updates
module Venue = Ohcamel_desk.Venue
module Order = Ohcamel_desk.Order
module Ids = Ohcamel_desk.Ids

let cid = "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1"

let request kind =
  {
    Order.Request.client_order_id = Option.value_exn (Ids.Client_order_id.of_string cid);
    symbol = Symbol.of_string "AAPL";
    side = Order.Side.Buy;
    qty = 2;
    kind;
  }

let test_an_order_is_sent_as_strings () =
  Alcotest.(check string) "market"
    {|{"symbol":"AAPL","qty":"2","side":"buy","type":"market","time_in_force":"day","client_order_id":"ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1"}|}
    (Yojson.Safe.to_string (Alpaca.order_request_json (request Order.Kind.Market)));
  Alcotest.(check string) "limit, two decimals"
    {|{"symbol":"AAPL","qty":"2","side":"buy","type":"limit","time_in_force":"day","client_order_id":"ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1","limit_price":"150.00"}|}
    (Yojson.Safe.to_string (Alpaca.order_request_json (request (Order.Kind.Limit (Price.of_float 150.0)))))

let accepted_body =
  {|{"asset_class":"us_equity","client_order_id":"ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1","filled_avg_price":null,"filled_qty":"0","id":"7b08df51-c1ac-453c-99f9-323a5f075f0d","limit_price":"150","qty":"2","side":"buy","status":"accepted","symbol":"AAPL","time_in_force":"day","type":"limit"}|}

let test_the_three_answers_to_a_submission () =
  (match Alpaca.classify_submission ~status:200 ~body:accepted_body with
  | Venue.Submission.Accepted o ->
      Alcotest.(check string) "the venue's id" "7b08df51-c1ac-453c-99f9-323a5f075f0d" o.Venue.Venue_order.id;
      Alcotest.(check (option (float 0.0))) "the limit" (Some 150.0) o.Venue.Venue_order.limit_price
  | _ -> Alcotest.fail "a 200 with an order was not accepted");
  (match Alpaca.classify_submission ~status:403 ~body:{|{"code":40310000,"message":"insufficient buying power"}|} with
  | Venue.Submission.Rejected why -> Alcotest.(check string) "a refusal, with Alpaca's words" "403: insufficient buying power" why
  | _ -> Alcotest.fail "a 403 was not a refusal");
  List.iter [ 422; 429 ] ~f:(fun status ->
      match Alpaca.classify_submission ~status ~body:"{}" with
      | Venue.Submission.Rejected _ -> ()
      | _ -> Alcotest.failf "%d was not a refusal" status);
  List.iter [ (500, "{}"); (503, ""); (200, "not json") ] ~f:(fun (status, body) ->
      match Alpaca.classify_submission ~status ~body with
      | Venue.Submission.Unknown _ -> ()
      | _ -> Alcotest.failf "%d %S was not an unknown" status body)

let test_trade_update_messages () =
  let of_string s = Updates.Message.of_json (Yojson.Safe.from_string s) in
  (match of_string {|{"stream":"authorization","data":{"status":"authorized","action":"authenticate"}}|} with
  | Updates.Message.Authorized -> ()
  | _ -> Alcotest.fail "authorized");
  (match of_string {|{"stream":"authorization","data":{"status":"unauthorized","action":"authenticate"}}|} with
  | Updates.Message.Unauthorized -> ()
  | _ -> Alcotest.fail "unauthorized");
  (match of_string {|{"stream":"listening","data":{"streams":["trade_updates"]}}|} with
  | Updates.Message.Listening [ "trade_updates" ] -> ()
  | _ -> Alcotest.fail "listening");
  match
    of_string
      {|{"stream":"trade_updates","data":{"event":"fill","execution_id":"2f63ea93-423d-4169-b3f6-3fdafc10c418","order":{"client_order_id":"ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1","filled_avg_price":"105.8988475","filled_qty":"30","id":"a5be8f5e-fdfa-41f5-a644-7a74fe947a8f","limit_price":null,"qty":"30","side":"sell","status":"filled","symbol":"XOM","type":"market"},"position_qty":"-30","price":"105.8988475","qty":"30","timestamp":"2022-04-19T17:45:05.024916716Z"}}|}
  with
  | Updates.Message.Update { Venue.Update.event = "fill"; fill = Some f; order; _ } ->
      Alcotest.(check string) "execution id" "2f63ea93-423d-4169-b3f6-3fdafc10c418" f.Order.Fill.execution_id;
      Alcotest.(check (float 1e-9)) "price" 105.8988475 (Price.to_float f.Order.Fill.price);
      Alcotest.(check (float 0.0)) "qty" 30.0 f.Order.Fill.qty;
      Alcotest.(check (option (float 0.0))) "the venue's position, signed" (Some (-30.0)) f.Order.Fill.position_qty;
      Alcotest.(check bool) "a sell" true (Order.Side.equal order.Venue.Venue_order.side Order.Side.Sell)
  | _ -> Alcotest.fail "a fill update was not read as one"

let suite =
  ( "alpaca_trading",
    [
      Alcotest.test_case "an order is sent as strings" `Quick test_an_order_is_sent_as_strings;
      Alcotest.test_case "the three answers to a submission" `Quick test_the_three_answers_to_a_submission;
      Alcotest.test_case "trade update messages" `Quick test_trade_update_messages;
    ] )
```

  Register after `Test_tca.suite;`.

- [ ] **Step 2: Run and watch it fail.** Expected: `Unbound value Alpaca.order_request_json`.

- [ ] **Step 3: Implement the pure functions** in `desk/alpaca_paper.ml`:

```ocaml
(* ---------------------------------------------------------------------- *)
(* The trading half: pure parts                                             *)
(* ---------------------------------------------------------------------- *)

(* Quantities and prices go as strings, as Alpaca's reference writes them. A
   limit price goes with two decimals: the rules refused a sub-penny limit on
   a stock above a dollar before this was reached (Rule 612), so formatting
   here rounds nothing that mattered. *)
let order_request_json (r : Order.Request.t) : Yojson.Safe.t =
  `Assoc
    ([
       ("symbol", `String (Symbol.to_string r.Order.Request.symbol));
       ("qty", `String (Int.to_string r.Order.Request.qty));
       ("side", `String (Order.Side.to_string r.Order.Request.side));
       ("type", `String (Order.Kind.to_string r.Order.Request.kind));
       ("time_in_force", `String "day");
       ("client_order_id", `String (Ids.Client_order_id.to_string r.Order.Request.client_order_id));
     ]
    @ Option.value_map (Order.Kind.limit_price r.Order.Request.kind) ~default:[] ~f:(fun p ->
          [ ("limit_price", `String (sprintf "%.2f" (Price.to_float p))) ]))

let venue_order_of_json (json : Yojson.Safe.t) : Venue.Venue_order.t Or_error.t =
  let open Or_error.Let_syntax in
  let what = "order" in
  let%bind id = string_field ~what json "id" in
  let%bind client_order_id = string_field ~what json "client_order_id" in
  let%bind symbol = string_field ~what json "symbol" in
  let%bind side_text = string_field ~what json "side" in
  let%bind side =
    match Order.Side.of_string side_text with
    | Some s -> Ok s
    | None -> Or_error.errorf "alpaca_paper: order %s has side %S" id side_text
  in
  let%bind qty = decimal ~what json "qty" in
  let%bind filled_qty = decimal ~what json "filled_qty" in
  let%bind filled_avg_price = optional_decimal ~what json "filled_avg_price" in
  let%bind limit_price = optional_decimal ~what json "limit_price" in
  let%map status = string_field ~what json "status" in
  { Venue.Venue_order.id; client_order_id; symbol = Symbol.of_string symbol; side; qty; filled_qty; filled_avg_price; status; limit_price }

(* A refusal is a status the venue uses to say "I did not take this order" --
   the request was bad, the account cannot afford it, it was rate limited.
   Anything else that is not a readable 200 is Unknown: a 5xx can arrive after
   the order was accepted, and treating it as a refusal would invite the one
   thing invariant 10 forbids, sending the order again. *)
let classify_submission ~(status : int) ~(body : string) : Venue.Submission.t =
  let message () =
    match Option.try_with (fun () -> Yojson.Safe.from_string body) with
    | Some json -> ( match field json "message" with Some (`String m) -> m | _ -> String.prefix body 120)
    | None -> String.prefix body 120
  in
  match status with
  | 200 -> (
      match Option.try_with (fun () -> Yojson.Safe.from_string body) with
      | None -> Venue.Submission.Unknown "200 with a body that is not JSON"
      | Some json -> (
          match venue_order_of_json json with
          | Ok o -> Venue.Submission.Accepted o
          | Error e -> Venue.Submission.Unknown ("200 with an unreadable order: " ^ Error.to_string_hum e)))
  | 400 | 401 | 403 | 404 | 409 | 422 | 429 -> Venue.Submission.Rejected (sprintf "%d: %s" status (message ()))
  | other -> Venue.Submission.Unknown (sprintf "%d: %s" other (message ()))
```

  `desk/trade_updates.ml` — the pure message reader and the session:

```ocaml
(* Alpaca's trade_updates stream: the venue telling the desk what happened to
   its orders.

   It is a different socket from the market-data stream the risk engine holds
   -- the paper trading host, binary frames carrying JSON -- so it does not
   spend the free plan's one data stream. Authentication is the client's
   first message; the server answers on the "authorization" stream, and a
   "listen" for trade_updates is answered on "listening".

   Reconnection reuses the market-data client's backoff. After every
   successful listen the desk reconciles against the venue's REST view, since
   an update sent while the socket was down is an update this stream will
   never deliver. *)

open Core
open Async
open Ohcamel.Types

module Message = struct
  type t = Authorized | Unauthorized | Listening of string list | Update of Venue.Update.t | Stream_error of string | Other

  let member k = function `Assoc fs -> List.Assoc.find fs k ~equal:String.equal | _ -> None
  let str = function Some (`String s) -> Some s | _ -> None
  let num = function
    | Some (`String s) -> Float.of_string_opt s
    | Some (`Float f) -> Some f
    | Some (`Int n) -> Some (Float.of_int n)
    | _ -> None

  let of_json (json : Yojson.Safe.t) : t =
    let data = member "data" json in
    match (str (member "stream" json), data) with
    | Some "authorization", Some d -> (
        match str (member "status" d) with Some "authorized" -> Authorized | _ -> Unauthorized)
    | Some "listening", Some d -> (
        match member "streams" d with
        | Some (`List xs) -> Listening (List.filter_map xs ~f:(function `String s -> Some s | _ -> None))
        | _ -> Listening [])
    | Some "trade_updates", Some d -> (
        match (str (member "event" d), Option.map (member "order" d) ~f:Alpaca_paper.venue_order_of_json) with
        | Some event, Some (Ok order) ->
            let at =
              Option.value ~default:(Time_ns.now ()) (Option.bind (str (member "timestamp" d)) ~f:Desk_time.parse)
            in
            let fill =
              match (num (member "qty" d), num (member "price" d)) with
              | Some qty, Some price when List.mem [ "fill"; "partial_fill" ] event ~equal:String.equal ->
                  Some
                    {
                      Order.Fill.execution_id =
                        Option.value (str (member "execution_id" d))
                          ~default:(order.Venue.Venue_order.id ^ ":" ^ Desk_time.rfc3339 at);
                      qty;
                      price = Price.of_float price;
                      at;
                      position_qty = num (member "position_qty" d);
                    }
              | _ -> None
            in
            Update { Venue.Update.event; order; fill; at }
        | _ -> Other)
    | _ -> (
        match (str (member "action" json), data) with
        | Some "error", Some d -> Stream_error (Option.value (str (member "error_message" d)) ~default:"stream error")
        | _ -> Other)
end
```

  The session `run` follows `lib/feed/alpaca_ws.ml`'s `run_session`/`run` shape -- four pipes over `Websocket_async.client`, a Ping answered with a Pong, a Close closing the send pipe, and `desk/dune` gaining the two websocket libraries `lib/dune` already lists: `Ohcamel.Alpaca_ws.with_connection (Uri.make ~scheme:"wss" ~host:Alpaca_paper.trading_host ~path:"/stream" ())`, send `{"action":"auth","key":..,"secret":..}` (via `Alpaca_paper.Credentials`' secrets) as the first frame, on `Authorized` send `{"action":"listen","data":{"streams":["trade_updates"]}}`, on `Listening` containing `trade_updates` call `don't_wait_for (on_connected ())` and keep reading (a read loop that waited on a reconciliation would stop answering pings while it ran, and the venue would drop the socket), write every `Update` to `writer`, treat `Unauthorized` as fatal (log with `on_event` and stop reconnecting: a key the paper host refuses will be refused again), and reconnect on any other disconnect after `Ohcamel.Alpaca_ws.Backoff.delay Ohcamel.Alpaca_ws.Backoff.default ~attempt`. Frames arrive with opcode Binary and are handled exactly as Text. `Alpaca_paper.Credentials` needs two accessors for this: `key_string : t -> string` and `secret_string : t -> string`, each a single call site of `Secret.to_string`, named so a grep finds them.

  `desk/alpaca_paper.ml` gains the trading half's transport. A1's `get_json` (desk/alpaca_paper.ml:273-309) stays exactly as it is, comment and code. Directly below it, add:

```ocaml
(* The trading half's transport: any method, a body when there is one, and the
   answer as its status code and text, for the caller to judge.

   Bounded by [within], and closed as [get_json] is, because cohttp's one-shot
   calls close a connection in only two ways: [~interrupt] aborts a connect
   still in progress, and closing the body's pipe closes a connection that has
   answered. A connection that opened and never sent its status line is out of
   reach of both, and lasts until the peer or the kernel ends it.
   [Client.Connection] reaches it no better: closing one kills its sequencer,
   and a killed sequencer cleans a connection only once the request holding it
   returns (async_kernel's throttle.ml, [kill] and [start_job]).

   [~rest:`Log], as in [get_json]: an abandoned request can still raise after
   its answer was thrown away, and raised to the main monitor that would end
   the process this bound exists to keep running.

   The URI carries no credential -- the keys go in headers -- so its path is
   safe to name in an error. *)
let request_json ?(span = request_timeout) ~(meth : Cohttp.Code.meth) ?(body : string option)
    ~(credentials : Credentials.t) (uri : Uri.t) : (int * string) Or_error.t Deferred.t =
  let what = Cohttp.Code.string_of_method meth ^ " " ^ Uri.path uri in
  within ~span ~what (fun ~abandon ->
      match%map
        Monitor.try_with ~extract_exn:true ~rest:`Log (fun () ->
            let headers =
              match body with
              | None -> Credentials.headers credentials
              | Some _ -> Cohttp.Header.add (Credentials.headers credentials) "Content-Type" "application/json"
            in
            let%bind response, answer =
              Cohttp_async.Client.call ~interrupt:abandon ~headers ~chunked:false
                ?body:(Option.map body ~f:Cohttp_async.Body.of_string)
                meth uri
            in
            upon abandon (fun () ->
                match answer with `Pipe pipe -> Pipe.close_read pipe | _ -> ());
            let%map text = Cohttp_async.Body.to_string answer in
            (Cohttp.Code.code_of_status (Cohttp.Response.status response), text))
      with
      | Error exn -> Or_error.errorf "alpaca_paper: %s failed: %s" what (Exn.to_string exn)
      | Ok answer -> Ok answer)
```

  `request_timeout`, `within`, `get_json` and `read` neither move nor change: the read side keeps A1's transport and its four messages. `call` builds the request from `meth`, `uri`, `headers` and the body's length (`~chunked:false`), so nothing here calls `Cohttp.Request.make_for_client`. No test opens a socket. The live host exercises `request_json` on its first paper order, as it exercises A1's `get_json` on every sync.

  `desk/alpaca_trade.ml` implements `trade` over `request_json`, each call with `~span:(Time_ns.Span.of_sec 10.0)`. The span is 10 s because the order manager runs one job at a time, and a request that never answers would otherwise hold every job behind it, a kill included. A bound that passes frees the job at once. It closes the request's connection if that connection is still connecting or has answered; a peer that never sends its status line keeps the socket until the peer or the kernel ends it.
  - `submit` POSTs `Yojson.Safe.to_string (Alpaca_paper.order_request_json r)` to `/v2/orders`. `Ok (status, body)` goes to `classify_submission`. An `Error` -- the bound, or a transport exception -- is `Venue.Submission.Unknown` with its words. Invariant 10 names timeout, 5xx and a dropped connection as the unknowns, and `classify_submission` already makes a 5xx and an unreadable 200 `Unknown`. What the bound adds is the one path to `Submit_unknown` for a request that never answers at all, which had none before.
  - `cancel` DELETEs `/v2/orders/<id>`. 204 is `Ok ()`; 422 is `Error "not cancelable: <body's first 120 bytes>"`; any other status is an error naming it.
  - `find_order` GETs `trading_uri ~query:[ ("client_order_id", [ id ]) ] "/v2/orders:by_client_order_id"`. 200 is `Some` via `venue_order_of_json`; 404 is `None`.
  - `open_orders` GETs `/v2/orders?status=open&limit=500&direction=asc` and reads a JSON list of orders.
  - A bound that passes on `cancel`, `find_order` or `open_orders` is `within`'s error, for example `alpaca_paper: DELETE /v2/orders/<id> did not answer within 10s`, returned as the `Error` the manager logs.

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 420` (417 + 3).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | tail -3
git add desk/dune desk/alpaca_paper.ml desk/trade_updates.ml desk/alpaca_trade.ml test/test_alpaca_trading.ml test/test_ohcamel.ml lib/verified.ml
git commit -F - <<'EOF'
desk: Alpaca paper's trading half, where a refusal and an unknown are never confused, because reading one as the other either sends an order twice or looks for one forever

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 12: The switch the desk obeys

**Files:**
- Modify: `lib/alerts.ml`
- Create: `desk/halt.ml`, `test/test_halt.ml`
- Modify: `test/test_ohcamel.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: `Alerts.attach`, `Alerts.kill_switch`, `Alerts.Kill_switch.state`/`reset`, `Alerts.firing_limits`; `Desk_time.rfc3339`.
- Produces (in `Ohcamel.Alerts`): `on_trip : t -> f:(Event.t -> unit) -> unit` — called inside stabilization, once, on the transition into `Tripped`, after the tripped event is enqueued.
- Produces (module `Ohcamel_desk.Halt`):
  - `Source.t = { tripped : unit -> (string * Time_ns.t) option; firing : unit -> string list; reset : unit -> unit }`; `Source.none`; `Source.of_alerts : Ohcamel.Alerts.t -> Source.t`.
  - `State.t = Clear | Tripped of { limit : string; at : Time_ns.t } | Halted of { why : string; at : Time_ns.t }`; `State.name : State.t -> string` (`"clear"`, `"tripped"`, `"halted"`).
  - `create : ?auto_reset_after:Time_ns.Span.t -> Source.t -> t`; `state : t -> State.t`; `reason : t -> string option`; `halt : t -> why:string -> at:Time_ns.t -> unit`; `reset : t -> unit`; `tick : t -> now:Time_ns.t -> bool` (true when it reset); `to_json : t -> now:Time_ns.t -> Yojson.Safe.t` (keys `state`, `limit`, `why`, `at`, `auto_reset_s`, `resets_in_s`).

- [ ] **Step 1: Write the failing tests.** `test/test_halt.ml`:

```ocaml
(* The switch the desk obeys, with its source in the test's hands.

   The fake source is three refs: which limit tripped it and when, which
   limits are firing, and how many resets reached the kernel. The last case
   uses the kernel's real alerts, to pin the one line lib/alerts.ml gains. *)

open Core
module Halt = Ohcamel_desk.Halt

let t0 = Time_ns.of_string_with_utc_offset "2026-09-14T14:00:00Z"
let at s = Time_ns.add t0 (Time_ns.Span.of_sec s)

let fake () =
  let tripped = ref None and firing = ref [] and resets = ref 0 in
  let source =
    {
      Halt.Source.tripped = (fun () -> !tripped);
      firing = (fun () -> !firing);
      reset =
        (fun () ->
          incr resets;
          tripped := None);
    }
  in
  (source, tripped, firing, resets)

let state t = Halt.State.name (Halt.state t)
let says t words = String.is_substring (Option.value (Halt.reason t) ~default:"") ~substring:words

let test_a_limit_trips_it_and_the_reason_names_the_limit () =
  let source, tripped, _, _ = fake () in
  let t = Halt.create source in
  Alcotest.(check string) "clear" "clear" (state t);
  Alcotest.(check (option string)) "no reason" None (Halt.reason t);
  tripped := Some ("nvda-cap", t0);
  Alcotest.(check string) "tripped" "tripped" (state t);
  Alcotest.(check bool) "the reason names the limit" true (says t "tripped by nvda-cap")

let test_a_hand_outranks_a_limit_and_a_reset_lifts_both () =
  let source, tripped, _, resets = fake () in
  let t = Halt.create source in
  tripped := Some ("nvda-cap", t0);
  Halt.halt t ~why:"the feed is lying" ~at:(at 5.0);
  Alcotest.(check string) "halted, not tripped" "halted" (state t);
  Halt.halt t ~why:"a second press" ~at:(at 9.0);
  Alcotest.(check bool) "the first press's words stand" true (says t "the feed is lying");
  Halt.reset t;
  Alcotest.(check string) "clear" "clear" (state t);
  Alcotest.(check int) "and the kernel's switch was reset too" 1 !resets

let test_the_demo_resets_ninety_seconds_after_the_limit_clears () =
  let source, tripped, firing, _ = fake () in
  let t = Halt.create ~auto_reset_after:(Time_ns.Span.of_sec 90.0) source in
  tripped := Some ("nvda-cap", t0);
  firing := [ "nvda-cap" ];
  Alcotest.(check bool) "still firing at 100 s: no reset" false (Halt.tick t ~now:(at 100.0));
  firing := [];
  Alcotest.(check bool) "clear from 110 s" false (Halt.tick t ~now:(at 110.0));
  Alcotest.(check bool) "199 s is 89 s clear" false (Halt.tick t ~now:(at 199.0));
  firing := [ "nvda-cap" ];
  Alcotest.(check bool) "firing again at 199.5 s: the clock restarts" false (Halt.tick t ~now:(at 199.5));
  firing := [];
  Alcotest.(check bool) "clear from 200 s" false (Halt.tick t ~now:(at 200.0));
  Alcotest.(check bool) "289 s is 89 s clear" false (Halt.tick t ~now:(at 289.0));
  Alcotest.(check bool) "290 s is 90 s clear: reset" true (Halt.tick t ~now:(at 290.0));
  Alcotest.(check string) "clear" "clear" (state t)

let test_nothing_resets_by_time_on_the_live_host_or_after_a_hand () =
  let source, tripped, _, _ = fake () in
  let live = Halt.create source in
  tripped := Some ("nvda-cap", t0);
  Alcotest.(check bool) "live: clear for an hour, no reset" false (Halt.tick live ~now:(at 3600.0));
  Alcotest.(check bool) "live: and a minute later" false (Halt.tick live ~now:(at 3660.0));
  Alcotest.(check string) "still tripped" "tripped" (state live);
  let source, _, _, _ = fake () in
  let demo = Halt.create ~auto_reset_after:(Time_ns.Span.of_sec 90.0) source in
  Halt.halt demo ~why:"by hand" ~at:t0;
  ignore (Halt.tick demo ~now:(at 1.0) : bool);
  Alcotest.(check bool) "demo: a hand's halt an hour later" false (Halt.tick demo ~now:(at 3600.0));
  Alcotest.(check string) "still halted" "halted" (state demo)

(* The kernel's half. aapl-cap is 100 dollars; 1 share at 150 is over it, so
   the first stabilize trips the switch and on_trip is called once; 2 shares
   keep it tripped and call nothing. Source.of_alerts reads that switch and
   resets it. *)
let test_alerts_call_on_trip_once_and_the_desk_reads_the_switch () =
  let open Ohcamel.Types in
  let module Graph = Ohcamel.Graph in
  let module Config = Ohcamel.Config in
  let aapl = Symbol.of_string "AAPL" in
  let graph =
    Graph.create
      ~instruments:[ { Instrument.symbol = aapl; sector = Sector.of_string "TECH" } ]
      ~limits:
        [ { Limit.name = "aapl-cap"; scope = Limit.Instrument aapl; kind = Limit.Gross_notional (Notional.of_float 100.0) } ]
      ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      (* No sinks: nothing is printed, and nothing would be delivered without
         the scheduler anyway. *)
      let config =
        {
          Config.Alerts.default with
          Config.Alerts.enabled = true;
          sinks = [];
          kill_switch_enabled = true;
          kill_switch_trips_on = [ "aapl-cap" ];
        }
      in
      let alerts = Option.value_exn (Or_error.ok_exn (Ohcamel.Alerts.attach ~graph ~config)) in
      let calls = ref 0 in
      Ohcamel.Alerts.on_trip alerts ~f:(fun _ -> incr calls);
      Graph.set_price graph aapl (Price.of_float 150.0);
      Graph.set_qty graph aapl (Qty.of_float 1.0);
      Graph.stabilize graph;
      Alcotest.(check int) "once, on the trip" 1 !calls;
      Graph.set_qty graph aapl (Qty.of_float 2.0);
      Graph.stabilize graph;
      Alcotest.(check int) "not again while it stays tripped" 1 !calls;
      let halt = Halt.create (Halt.Source.of_alerts alerts) in
      Alcotest.(check string) "the desk sees the trip" "tripped" (state halt);
      Halt.reset halt;
      Alcotest.(check string) "and resets the kernel's switch" "clear" (state halt))

let suite =
  ( "halt",
    [
      Alcotest.test_case "a limit trips it and the reason names the limit" `Quick
        test_a_limit_trips_it_and_the_reason_names_the_limit;
      Alcotest.test_case "a hand outranks a limit, and a reset lifts both" `Quick
        test_a_hand_outranks_a_limit_and_a_reset_lifts_both;
      Alcotest.test_case "the demo resets ninety seconds after the limit clears" `Quick
        test_the_demo_resets_ninety_seconds_after_the_limit_clears;
      Alcotest.test_case "nothing resets by time on the live host, or after a hand" `Quick
        test_nothing_resets_by_time_on_the_live_host_or_after_a_hand;
      Alcotest.test_case "alerts call on_trip once, and the desk reads the switch" `Quick
        test_alerts_call_on_trip_once_and_the_desk_reads_the_switch;
    ] )
```

  Register after `Test_alpaca_trading.suite;`.

- [ ] **Step 2: Run and watch it fail.** Expected: `Unbound module Ohcamel_desk.Halt`.

- [ ] **Step 3: Implement.**
  1. `lib/alerts.ml`: add a field to `type t`, after `mutable failed : int;`:

```ocaml
  (* Readers that must act on a trip rather than report it. See [on_trip]. *)
  mutable trip_handlers : (Event.t -> unit) list;
```

  initialise it as `trip_handlers = [];` in `attach`'s record literal, and in `attach`'s breach handler change the `Some tripped` branch to:

```ocaml
            | Some tripped ->
                if not (Pipe.is_closed writer) then
                  Pipe.write_without_pushback writer tripped;
                List.iter t.trip_handlers ~f:(fun f -> f tripped));
```

  and below `let failed t = t.failed` add:

```ocaml
(* A reader that must act on a trip rather than report it. Called inside
   stabilization, once, on the transition into Tripped; a handler may only
   schedule work, for the reason the writer above only enqueues. The kernel
   still acts on nothing: what a handler does is its own library's business,
   and this module does not know that library exists. *)
let on_trip t ~f = t.trip_handlers <- t.trip_handlers @ [ f ]
```

  Then reword the two comments in the same file that `on_trip` makes untrue. The new words must not name anything CI's `lib/` grep refuses; "the desk" is fine.
  - In the header's item 2 (lib/alerts.ml:17-22), replace from `2. THE KILL SWITCH SETS A FLAG AND NOTHING ELSE.` to `for a later conversation, not a default.` with:

```ocaml
   2. THE KILL SWITCH SETS A FLAG, AND THE KERNEL DOES NOTHING ELSE WITH IT.
      This module does not import the Alpaca client -- it cannot reach a
      trading endpoint even by mistake. What [Kill_switch.halt_new_orders]
      returns, and what [on_trip] announces, are for a reader outside the
      kernel to act on: the desk refuses new orders and cancels open ones on
      them, and nothing in lib/ places, cancels or modifies an order.
```

  - In the comment above `module Kill_switch` (lib/alerts.ml:196-200), replace from `[halt_new_orders] returns a bool.` to `cannot reach a trading endpoint.` with:

```ocaml
   [halt_new_orders] returns a bool, and [on_trip] calls back once per trip.
   Nothing in the kernel reads either to place, cancel or modify an order,
   because nothing in the kernel can: this module does not depend on
   Alpaca_ws or Alpaca_rest and cannot reach a trading endpoint. The desk,
   outside the kernel, is what obeys it.
```

  2. `desk/halt.ml`:

```ocaml
(* The switch the desk obeys (design §3.8).

   Two things stop new orders: a limit the kill switch trips on, and a person.
   The first lives in the kernel's alerts, which only ever set a flag; this
   module is where the flag gains a consequence. The second is a halt by hand,
   the POST /api/desk/kill someone sends when something is wrong that no
   limit measures.

   A HAND OUTRANKS A LIMIT. A halt by hand is reported ahead of a trip, and
   only a deliberate reset lifts it: whoever pressed it knew something the
   limits did not.

   THE DEMO RESETS ITSELF, AND ONLY THE DEMO. The public demo trips its switch
   on purpose -- nvda-cap sits 200 dollars above NVDA's exposure -- and a
   switch that stayed tripped for ever would turn its blotter into a list of
   refusals. So [auto_reset_after], passed only by the demo, resets a limit's
   trip once that limit has been clear for that long; a halt by hand is never
   reset by time. The live host passes nothing, and its switch stays where it
   was put until someone resets it.

   The source is a record of closures, so the logic is tested without a graph
   and a desk with no alerts configured still has a switch: [Source.none]
   never trips, and a hand can still halt. *)

open Core
module Alerts = Ohcamel.Alerts

module Source = struct
  type t = {
    tripped : unit -> (string * Time_ns.t) option;
    firing : unit -> string list;
    reset : unit -> unit;
  }

  let none = { tripped = (fun () -> None); firing = (fun () -> []); reset = (fun () -> ()) }

  let of_alerts (a : Alerts.t) =
    {
      tripped =
        (fun () ->
          match Alerts.Kill_switch.state (Alerts.kill_switch a) with
          | Alerts.Kill_switch.Tripped { by; at } -> Some (by, at)
          | Alerts.Kill_switch.Armed | Alerts.Kill_switch.Disarmed -> None);
      firing = (fun () -> Alerts.firing_limits a);
      reset = (fun () -> Alerts.Kill_switch.reset (Alerts.kill_switch a));
    }
end

module State = struct
  type t = Clear | Tripped of { limit : string; at : Time_ns.t } | Halted of { why : string; at : Time_ns.t }

  let name = function Clear -> "clear" | Tripped _ -> "tripped" | Halted _ -> "halted"
end

type t = {
  source : Source.t;
  auto_reset_after : Time_ns.Span.t option;
  mutable by_hand : (string * Time_ns.t) option;
  (* When the limit that tripped the switch was first seen clear, since it
     last fired. *)
  mutable clear_since : Time_ns.t option;
}

let create ?auto_reset_after source = { source; auto_reset_after; by_hand = None; clear_since = None }

let state t : State.t =
  match (t.by_hand, t.source.Source.tripped ()) with
  | Some (why, at), _ -> State.Halted { why; at }
  | None, Some (limit, at) -> State.Tripped { limit; at }
  | None, None -> State.Clear

let reason t =
  match state t with
  | State.Clear -> None
  | State.Tripped { limit; at } ->
      Some (sprintf "the kill switch was tripped by %s at %s" limit (Desk_time.rfc3339 at))
  | State.Halted { why; at } -> Some (sprintf "the desk was halted by hand at %s: %s" (Desk_time.rfc3339 at) why)

(* A second press does not overwrite the first: the first press's reason is
   the one the reset has to answer. *)
let halt t ~why ~at = if Option.is_none t.by_hand then t.by_hand <- Some (why, at)

let reset t =
  t.by_hand <- None;
  t.clear_since <- None;
  t.source.Source.reset ()

let tick t ~now =
  match (t.auto_reset_after, t.by_hand, t.source.Source.tripped ()) with
  | Some after, None, Some (limit, _) ->
      if List.mem (t.source.Source.firing ()) limit ~equal:String.equal then (
        t.clear_since <- None;
        false)
      else
        let since =
          match t.clear_since with
          | Some since -> since
          | None ->
              t.clear_since <- Some now;
              now
        in
        if Time_ns.Span.( >= ) (Time_ns.diff now since) after then (
          reset t;
          true)
        else false
  | _ ->
      t.clear_since <- None;
      false

let to_json t ~now : Yojson.Safe.t =
  let time at = `String (Desk_time.rfc3339 at) in
  let current = state t in
  let limit, why, at =
    match current with
    | State.Clear -> (`Null, `Null, `Null)
    | State.Tripped { limit; at } -> (`String limit, `Null, time at)
    | State.Halted { why; at } -> (`Null, `String why, time at)
  in
  `Assoc
    [
      ("state", `String (State.name current));
      ("limit", limit);
      ("why", why);
      ("at", at);
      ("auto_reset_s", match t.auto_reset_after with Some s -> `Float (Time_ns.Span.to_sec s) | None -> `Null);
      ( "resets_in_s",
        match (t.auto_reset_after, t.clear_since, current) with
        | Some after, Some since, State.Tripped _ ->
            `Float (Float.max 0.0 (Time_ns.Span.to_sec after -. Time_ns.Span.to_sec (Time_ns.diff now since)))
        | _ -> `Null );
    ]
```

  (`Halt.State.t` derives nothing, so the poisoned `Time_ns.sexp_of_t` is never reached. If the CI grep "The risk kernel names nothing that trades" matches a word in the new `lib/alerts.ml` comment, reword the comment; do not change the grep.)

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 425` (420 + 5).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'
git add lib/alerts.ml desk/halt.ml test/test_halt.ml test/test_ohcamel.ml lib/verified.ml
git commit -F - <<'EOF'
desk: the switch the desk obeys -- a limit's trip or a hand's halt, reset only by a request except on the demo -- because the kernel's switch set a flag and something has to be bound by it

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 13: A ticket, and what a restart learns

**Files:**
- Create: `desk/ticket.ml`, `desk/reconcile.ml`, `test/test_ticket.ml`, `test/test_reconcile.ml`
- Modify: `test/test_ohcamel.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: `Order` (all of it, including `Order.epsilon` and Task 1's rule that a venue id is kept whenever it arrives), `Ids.Client_order_id`, `Venue.Venue_order`, `Venue.Update`.
- Produces (module `Ohcamel_desk.Ticket`): `t = { symbol : Symbol.t; side : Order.Side.t; qty : int; kind : Order.Kind.t }`; `of_json : Yojson.Safe.t -> (t, string) Result.t` (the first thing that cannot be read, named by its field); `to_request : t -> client_order_id:Ids.Client_order_id.t -> Order.Request.t`.
- Produces (module `Ohcamel_desk.Reconcile`): `reconciled_prefix : string` (= `"reconciled:"`); `events_for : Order.t -> Venue.Venue_order.t option -> at:Time_ns.t -> Order.Event.t list` (a `Found` whenever the journal's order has no venue id); `fill_is_new : Order.t -> Venue.Update.t -> bool`.

- [ ] **Step 1: Write the failing tests.**
  `test/test_ticket.ml`:

```ocaml
(* A ticket, as the page posts it. A quantity of zero or less is read, not
   refused: whole_shares is a rule, and the rules report it with the rest. *)

open Core
open Ohcamel.Types
module Ticket = Ohcamel_desk.Ticket
module Order = Ohcamel_desk.Order

let parse s = Ticket.of_json (Yojson.Safe.from_string s)

let test_a_ticket_reads_as_the_page_sends_it () =
  (match parse {|{"symbol":" aapl ","side":"buy","qty":10}|} with
  | Ok t ->
      Alcotest.(check string) "symbol, trimmed and upper case" "AAPL" (Symbol.to_string t.Ticket.symbol);
      Alcotest.(check int) "qty" 10 t.Ticket.qty;
      Alcotest.(check bool) "market when no type is given" true (Order.Kind.equal t.Ticket.kind Order.Kind.Market)
  | Error e -> Alcotest.fail e);
  (match parse {|{"symbol":"MSFT","side":"sell","qty":5.0,"type":"limit","limit_price":301.25}|} with
  | Ok t ->
      Alcotest.(check bool) "a sell" true (Order.Side.equal t.Ticket.side Order.Side.Sell);
      Alcotest.(check int) "5.0 is five shares" 5 t.Ticket.qty;
      Alcotest.(check (option (float 0.0)))
        "the limit" (Some 301.25)
        (Option.map (Order.Kind.limit_price t.Ticket.kind) ~f:Price.to_float)
  | Error e -> Alcotest.fail e);
  match parse {|{"symbol":"XOM","side":"buy","qty":0}|} with
  | Ok t -> Alcotest.(check int) "zero is read, for the rules to refuse" 0 t.Ticket.qty
  | Error e -> Alcotest.fail e

let test_what_cannot_be_read_is_named () =
  List.iter
    [
      ({|[1,2]|}, "a ticket is a JSON object");
      ({|{"side":"buy","qty":1}|}, "symbol: a ticker, as a string");
      ({|{"symbol":"AAPL","side":"short","qty":1}|}, "side: buy or sell");
      ({|{"symbol":"AAPL","side":"buy","qty":1.5}|}, "qty: a whole number of shares");
      ({|{"symbol":"AAPL","side":"buy","qty":1,"type":"limit"}|}, "limit_price: a limit order needs a positive price");
      ({|{"symbol":"AAPL","side":"buy","qty":1,"limit_price":150}|}, "limit_price: only a limit order has one");
      ({|{"symbol":"AAPL","side":"buy","qty":1,"type":"stop"}|}, "type: market or limit");
    ]
    ~f:(fun (body, expected) ->
      match parse body with
      | Ok _ -> Alcotest.failf "%s was read as a ticket" body
      | Error e -> Alcotest.(check string) body expected e)

let suite =
  ( "ticket",
    [
      Alcotest.test_case "a ticket reads as the page sends it" `Quick test_a_ticket_reads_as_the_page_sends_it;
      Alcotest.test_case "what cannot be read is named" `Quick test_what_cannot_be_read_is_named;
    ] )
```

  `test/test_reconcile.ml`:

```ocaml
(* Reconciliation as a table: the journal's order, the venue's answer, and the
   state the machine reaches from the events -- with the arithmetic of every
   recovered fill beside it. *)

open Core
open Ohcamel.Types
module Order = Ohcamel_desk.Order
module Venue = Ohcamel_desk.Venue
module Reconcile = Ohcamel_desk.Reconcile
module Ids = Ohcamel_desk.Ids

let at = Time_ns.of_string_with_utc_offset "2026-09-14T15:00:00Z"
let aapl = Symbol.of_string "AAPL"
let cid = "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1"

let order ?(events = []) qty =
  let request =
    {
      Order.Request.client_order_id = Option.value_exn (Ids.Client_order_id.of_string cid);
      symbol = aapl;
      side = Order.Side.Buy;
      qty;
      kind = Order.Kind.Market;
    }
  in
  List.fold events ~init:(Order.create request) ~f:(fun o e -> fst (Order.apply o e))

let venue ?(filled = 0.0) ?avg status =
  {
    Venue.Venue_order.id = "v-1";
    client_order_id = cid;
    symbol = aapl;
    side = Order.Side.Buy;
    qty = 100.0;
    filled_qty = filled;
    filled_avg_price = avg;
    status;
    limit_price = None;
  }

let fill id qty price =
  Order.Event.Venue_fill { Order.Fill.execution_id = id; qty; price = Price.of_float price; at; position_qty = None }

let run o v = List.fold (Reconcile.events_for o v ~at) ~init:o ~f:(fun o e -> fst (Order.apply o e))
let names o v = List.map (Reconcile.events_for o v ~at) ~f:Order.Event.name
let state o = Order.State.to_string o.Order.state

let test_what_a_restart_learns () =
  let pending = order 100 in
  Alcotest.(check (list string))
    "pending, and the venue has not heard of it: an unknown, for the lookups to decide" [ "outcome_unknown" ]
    (names pending None);
  Alcotest.(check string) "submit_unknown" "submit_unknown" (state (run pending None));
  let unknown = order ~events:[ Order.Event.Outcome_unknown "timed out" ] 100 in
  Alcotest.(check (list string)) "unknown, and still not found: nothing decided on one miss" [] (names unknown None);
  Alcotest.(check (list string))
    "pending, and the venue has it" [ "outcome_unknown"; "found"; "venue_accepted" ]
    (names pending (Some (venue "new")));
  let resolved = run pending (Some (venue "new")) in
  Alcotest.(check string) "accepted" "accepted" (state resolved);
  Alcotest.(check (option string)) "with the venue's id" (Some "v-1") resolved.Order.venue_order_id;
  let acknowledged = order ~events:[ Order.Event.Acknowledged "v-1" ] 100 in
  Alcotest.(check (list string))
    "acknowledged, and the lookup finds nothing: nothing invented" [] (names acknowledged None);
  (* A fill of 30 at 100.00 that the stream delivered before the POST's answer
     was read, and then the process stopped: partially filled, with no venue
     id. The venue's 30 filled equals the journal's 30, so nothing is missing,
     and partially_filled maps to no status event: the lookup adds the id and
     nothing else. *)
  let unidentified = order ~events:[ fill "x1" 30.0 100.0 ] 100 in
  let lookup = Some (venue ~filled:30.0 ~avg:100.0 "partially_filled") in
  Alcotest.(check (list string)) "moved on without its id: the id, and nothing else" [ "found" ] (names unidentified lookup);
  Alcotest.(check (option string)) "and it is kept" (Some "v-1") (run unidentified lookup).Order.venue_order_id;
  (* Accepted with nothing filled; the venue filled all 100 at 100.30 while
     nobody listened: one recovered fill of 100 at (100 x 100.30 - 0) / 100. *)
  let accepted = order ~events:[ Order.Event.Acknowledged "v-1"; Order.Event.Venue_accepted ] 100 in
  let filled = run accepted (Some (venue ~filled:100.0 ~avg:100.30 "filled")) in
  Alcotest.(check string) "filled" "filled" (state filled);
  Alcotest.(check (option (float 1e-9))) "at the venue's average" (Some 100.30) (Order.avg_fill_price filled);
  (* 40 filled at 100.00 before the stop. The venue says 70 filled at an
     average of 100.20, then cancelled. Missing: 30 shares, whose notional is
     70 x 100.20 - 40 x 100.00 = 7,014 - 4,000 = 3,014, so 3,014 / 30 =
     100.4666... each. *)
  let partial = order ~events:[ Order.Event.Acknowledged "v-1"; fill "x1" 40.0 100.0 ] 100 in
  let answer = Some (venue ~filled:70.0 ~avg:100.20 "canceled") in
  Alcotest.(check (list string)) "a missing fill, then the cancel" [ "venue_fill"; "venue_cancelled" ] (names partial answer);
  let cancelled = run partial answer in
  Alcotest.(check string) "cancelled" "cancelled" (state cancelled);
  Alcotest.(check (float 1e-9)) "70 filled" 70.0 cancelled.Order.filled_qty;
  Alcotest.(check (float 1e-6)) "notional 7,014, the venue's" 7_014.0 cancelled.Order.filled_notional

let update ?fill cumulative =
  { Venue.Update.event = "fill"; order = venue ~filled:cumulative ~avg:100.0 "partially_filled"; fill; at }

let execution id qty = { Order.Fill.execution_id = id; qty; price = Price.of_float 100.0; at; position_qty = None }

let test_a_late_update_for_reconciled_shares_is_not_counted_twice () =
  let plain = order ~events:[ Order.Event.Acknowledged "v-1" ] 100 in
  Alcotest.(check bool)
    "no reconciliation: a new execution counts" true
    (Reconcile.fill_is_new plain (update ~fill:(execution "x1" 40.0) 40.0));
  let seen = order ~events:[ Order.Event.Acknowledged "v-1"; fill "x1" 40.0 100.0 ] 100 in
  Alcotest.(check bool) "a replay does not" false (Reconcile.fill_is_new seen (update ~fill:(execution "x1" 40.0) 40.0));
  let reconciled = run plain (Some (venue ~filled:40.0 ~avg:100.0 "partially_filled")) in
  Alcotest.(check bool)
    "the late update for the same 40: cumulative 40 is not past 40" false
    (Reconcile.fill_is_new reconciled (update ~fill:(execution "x1" 40.0) 40.0));
  Alcotest.(check bool)
    "a later 60: cumulative 100 is past 40" true
    (Reconcile.fill_is_new reconciled (update ~fill:(execution "x2" 60.0) 100.0));
  Alcotest.(check bool) "an update with no fill is not a fill" false (Reconcile.fill_is_new plain (update 0.0))

let suite =
  ( "reconcile",
    [
      Alcotest.test_case "what a restart learns" `Quick test_what_a_restart_learns;
      Alcotest.test_case "a late update for reconciled shares is not counted twice" `Quick
        test_a_late_update_for_reconciled_shares_is_not_counted_twice;
    ] )
```

  Register `Test_ticket.suite;` and `Test_reconcile.suite;` after `Test_halt.suite;`.

- [ ] **Step 2: Run and watch it fail.** Expected: `Unbound module Ohcamel_desk.Ticket`.

- [ ] **Step 3: Implement.**
  `desk/ticket.ml`:

```ocaml
(* A ticket: what a person or a strategy asks the desk to trade, before the
   desk has given it an id or a price.

   Read from the JSON the page posts. The first thing that cannot be read is
   the error -- unlike the rules, which report everything: a ticket that is
   not a ticket has one problem, and the field is the useful half of saying
   so. A quantity of zero or less IS read. Whether it is a positive whole
   number is the whole_shares rule's to say, beside every other rule. *)

open Core
open Ohcamel.Types

type t = { symbol : Symbol.t; side : Order.Side.t; qty : int; kind : Order.Kind.t }

let to_request (t : t) ~(client_order_id : Ids.Client_order_id.t) : Order.Request.t =
  { Order.Request.client_order_id; symbol = t.symbol; side = t.side; qty = t.qty; kind = t.kind }

let of_json (json : Yojson.Safe.t) : (t, string) Result.t =
  let open Result.Let_syntax in
  let%bind fields = match json with `Assoc fields -> Ok fields | _ -> Error "a ticket is a JSON object" in
  let find key = List.Assoc.find fields key ~equal:String.equal in
  let%bind symbol =
    match find "symbol" with
    | Some (`String s) when not (String.is_empty (String.strip s)) ->
        Ok (Symbol.of_string (String.uppercase (String.strip s)))
    | _ -> Error "symbol: a ticker, as a string"
  in
  let%bind side =
    match find "side" with
    | Some (`String s) -> Result.of_option (Order.Side.of_string s) ~error:"side: buy or sell"
    | _ -> Error "side: buy or sell"
  in
  let%bind qty =
    match find "qty" with
    | Some (`Int n) -> Ok n
    | Some (`Float f) when Float.is_integer f && Float.( < ) (Float.abs f) 1e9 -> Ok (Float.to_int f)
    | _ -> Error "qty: a whole number of shares"
  in
  let number = function Some (`Int n) -> Some (Float.of_int n) | Some (`Float f) -> Some f | _ -> None in
  let%map kind =
    match (find "type", number (find "limit_price")) with
    | (None | Some (`String "market")), None -> Ok Order.Kind.Market
    | (None | Some (`String "market")), Some _ -> Error "limit_price: only a limit order has one"
    | Some (`String "limit"), Some p when Float.is_finite p && Float.( > ) p 0.0 -> Ok (Order.Kind.Limit (Price.of_float p))
    | Some (`String "limit"), _ -> Error "limit_price: a limit order needs a positive price"
    | _ -> Error "type: market or limit"
  in
  { symbol; side; qty; kind }
```

  `desk/reconcile.ml`:

```ocaml
(* What a restart learns from the venue, as events the state machine already
   understands (design §3.6; invariants 10 and 11).

   A process that stopped mid-order left the journal saying what it knew:
   pending_submit if it died between the journal and the answer,
   submit_unknown if the answer never came, accepted or partially filled if
   the venue's updates stopped reaching it. The venue knows the rest. For each
   order the journal holds open, the manager looks it up by client order id
   and passes the answer through [events_for]; the machine does the rest, so a
   restart has no transitions of its own to get wrong.

   A LOOKUP THAT FINDS NOTHING decides nothing here. For an order the venue
   never acknowledged, one miss is not yet the answer invariant 10 waits for --
   a request can still be arriving -- so the order becomes, or stays, an
   unknown, and the manager's scheduled lookups decide it. For an order the
   venue once acknowledged it is a contradiction, and nothing is invented to
   cover it: no events, and the caller says so.

   FILLS THE UPDATES NEVER DELIVERED are recovered from the venue's cumulative
   quantity and average price, as one fill for the difference, under an
   execution id that says it was reconciled. The venue's order carries no
   execution ids, so this is the only honest spelling of "these shares filled
   while nobody was listening". [fill_is_new] then keeps a real update for
   those same shares, arriving late, from counting them twice. *)

open Core
open Ohcamel.Types

let reconciled_prefix = "reconciled:"

let missing_fill (o : Order.t) (v : Venue.Venue_order.t) ~(at : Time_ns.t) : Order.Event.t option =
  let missing = v.Venue.Venue_order.filled_qty -. o.Order.filled_qty in
  match v.Venue.Venue_order.filled_avg_price with
  | Some avg when Float.( > ) missing Order.epsilon ->
      let price = ((v.Venue.Venue_order.filled_qty *. avg) -. o.Order.filled_notional) /. missing in
      Some
        (Order.Event.Venue_fill
           {
             Order.Fill.execution_id =
               sprintf "%s%s:%g" reconciled_prefix v.Venue.Venue_order.id v.Venue.Venue_order.filled_qty;
             qty = missing;
             price = Price.of_float price;
             at;
             position_qty = None;
           })
  | _ -> None

let events_for (o : Order.t) (venue : Venue.Venue_order.t option) ~(at : Time_ns.t) : Order.Event.t list =
  (* A pending order first becomes what it was, an outcome nobody learned. A
     lookup that misses must leave an unknown for the scheduled lookups to
     decide, not a pending order nobody will look up again. *)
  let stopped =
    match o.Order.state with
    | Order.State.Pending_submit -> [ Order.Event.Outcome_unknown "the process stopped before the venue answered" ]
    | _ -> []
  in
  match venue with
  | None -> stopped
  | Some v ->
      (* The venue's id, whenever the journal's order has none -- not only while
         it is unacknowledged. A fill the stream delivered before the POST's
         answer was read moved the order on without one (Task 1). *)
      let found =
        stopped @ if Option.is_none o.Order.venue_order_id then [ Order.Event.Found v.Venue.Venue_order.id ] else []
      in
      let status =
        match v.Venue.Venue_order.status with
        | "new" | "accepted" | "pending_new" -> [ Order.Event.Venue_accepted ]
        | "canceled" -> [ Order.Event.Venue_cancelled ]
        | "expired" -> [ Order.Event.Venue_expired ]
        | "rejected" -> [ Order.Event.Venue_rejected "rejected by the venue" ]
        | _ -> []
      in
      found @ Option.to_list (missing_fill o v ~at) @ status

let fill_is_new (o : Order.t) (u : Venue.Update.t) : bool =
  match u.Venue.Update.fill with
  | None -> false
  | Some f ->
      (not (Set.mem o.Order.execution_ids f.Order.Fill.execution_id))
      && ((not (Set.exists o.Order.execution_ids ~f:(String.is_prefix ~prefix:reconciled_prefix)))
         || Float.( > ) u.Venue.Update.order.Venue.Venue_order.filled_qty (o.Order.filled_qty +. Order.epsilon))
```

  (Only after a reconciliation does the cumulative quantity decide; otherwise execution ids alone do, so two partial fills delivered out of order are both counted.)

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 429` (425 + 4).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'
git add desk/ticket.ml desk/reconcile.ml test/test_ticket.ml test/test_reconcile.ml test/test_ohcamel.ml lib/verified.ml
git commit -F - <<'EOF'
desk: a ticket read from the page, and what a restart learns from the venue as events the state machine already knows, because a restart with transitions of its own would be a second state machine to get wrong

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 14: The order manager: preview, propose, and the venue's answer

**Files:**
- Create: `desk/oms.ml`, `test/test_oms.ml`
- Modify: `test/desk_async/test_desk_async.ml` (Task 4's), `test/test_ohcamel.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: `Rules`, `Ohcamel.Gate`, `Order`, `Journal` (orders API), `Venue` (`Read`, `Trade`, `Update`, `Submission`), `Sim_venue` (tests), `Halt`, `Ticket`, `Reconcile.fill_is_new`, `Tca`, `Ids`, `Desk_time`; `Graph.symbols`, `Graph.price`, `Graph.feed_health`, `Graph.apply_fill`, `Graph.set_qty`, `Graph.stabilize`; `Async.Throttle.Sequencer`, `Async.Time_source`; A1's `Session_close.not_current` (desk/session_close.ml:201-203), the sentence the session close already refuses a stale book with. session_close.ml names no desk module but `Journal` and `Venue`, so naming it here makes no cycle. Not `Desk`: desk.ml names `Oms` (Task 17), so the manager reaches the desk only through its `after_fill` and `book_is_current` callbacks. bin/main.ml sets them to Task 3's `Desk.after_fill desk` and A1's `Desk.book_is_current desk` (Task 18).
- Produces (module `Ohcamel_desk.Oms`):
  - `Adv.t = From_venue | Fixed of float`.
  - `create : graph:Graph.t -> journal:Journal.t -> spec:Desk_spec.t -> read:Venue.Read.t option -> trade:(Venue.Trade.t, string) Result.t -> halt:Halt.t -> accepts_tickets:bool -> adv:Adv.t -> now:(unit -> Time_ns.t) -> rng:Random.State.t -> on_change:(unit -> unit) -> on_event:(string -> unit) -> after_fill:(unit -> unit) -> book_is_current:(unit -> bool) -> ?resolve_delays:Time_ns.Span.t list -> ?time_source:Time_source.t -> unit -> t` (default delays 2 s, 10 s, 30 s; the default time source is the wall clock, and the lookups' delays, the arrival quote's 2 s bound and `refresh_forever`'s interval are measured on it).
  - `book_is_current` answers whether the graph holds the account's book: a read of the account applied within the caller's window. While it answers false, `preview` and `propose` fail the `trading` rule with `Session_close.not_current`, so no order is gated against the book file's quantities and cash. `propose` asks again after the arrival quote.
  - `set_market : t -> session_open:bool -> adv20:(Symbol.t * float) list -> unit`; `halt : t -> Halt.t`; `journal : t -> Journal.t`; `accepts_tickets : t -> bool`; `can_trade : t -> bool` (the book enables trading, there is a trading half, and `book_is_current` answers true; the frame's `trading` is this); `open_count : t -> int`.
  - `Preview.t = { request : Order.Request.t; failures : Rules.Failure.t list; verdict : Gate.Verdict.t option; decision_price : Price.t option }`; `Preview.passed`; `Preview.reasons : t -> string list` (rule failures as `"rule: why"`, then the gate's reasons); `Preview.to_json` (keys `passed`, `symbol`, `side`, `qty`, `type`, `limit_price`, `decision_price`, `rules`, `gate`, `reasons`).
  - `preview : t -> Ticket.t -> Preview.t` — pure of effects: no journal row, no request.
  - `propose : t -> ?source:string -> Ticket.t -> (Preview.t * Order.t) Deferred.t` — the order as it stands once the venue has answered (or once refused).
  - `on_update : t -> Venue.Update.t -> unit Deferred.t` (an order with no venue id takes the update's, as a `Found`: Task 1); `run : t -> unit Deferred.t` (consumes the trading half's updates).
  - `order_json : Journal.Order_row.t -> Yojson.Safe.t`; `costs : t -> Journal.Fill_row.t list -> (Tca.Inputs.t * Tca.Costs.t) list`.

  The book's `spread_bps` (and `spread_bps_default`) is read as the modelled half-spread in basis points -- the cost of one fill crossing from mid to the far side -- which is what `Tca`'s `versus_model_bps` subtracts.

- [ ] **Step 1: Write the failing tests.**
  `test/test_oms.ml` (the main suite: `preview` needs no scheduler):

```ocaml
(* The order manager's preview, on the gate's book:

     AAPL  400 x 150 = 60,000   TECH
     MSFT  100 x 300 = 30,000   TECH
     XOM  -200 x 100 = -20,000  ENERGY

   Limits aapl-cap 80,000, tech-cap 100,000, book-cap 200,000. Every name has
   printed just now, the session is open, twenty-day volume is a fixed
   1,000,000 shares (1% of it is 10,000), and the order cap is 25,000. *)

open Core
open Ohcamel.Types
module Graph = Ohcamel.Graph
module Desk_spec = Ohcamel.Config.Book.Desk_spec
module D = Ohcamel_desk

let aapl = Symbol.of_string "AAPL"
let msft = Symbol.of_string "MSFT"
let xom = Symbol.of_string "XOM"
let money = Notional.of_float
let limit name scope n = { Limit.name; scope; kind = Limit.Gross_notional (money n) }

(* [book_is_current] is true unless a case says otherwise: every case but one
   gates an order against a book the account holds. *)
let with_oms ?(book_is_current = true) ~f =
  let graph =
    Graph.create ~starting_cash:(money 1_000_000.0)
      ~instruments:
        [
          { Instrument.symbol = aapl; sector = Sector.of_string "TECH" };
          { Instrument.symbol = msft; sector = Sector.of_string "TECH" };
          { Instrument.symbol = xom; sector = Sector.of_string "ENERGY" };
        ]
      ~limits:
        [
          limit "aapl-cap" (Limit.Instrument aapl) 80_000.0;
          limit "tech-cap" (Limit.Sector (Sector.of_string "TECH")) 100_000.0;
          limit "book-cap" Limit.Portfolio 200_000.0;
        ]
      ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      List.iter [ (aapl, 150.0, 400.0); (msft, 300.0, 100.0); (xom, 100.0, -200.0) ] ~f:(fun (s, p, q) ->
          Graph.set_qty graph s (Qty.of_float q);
          Graph.set_returns graph s [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |];
          Graph.apply_tick graph { Tick.symbol = s; price = Price.of_float p; time = Time.now () });
      Graph.set_now graph (Time.now ());
      Graph.stabilize graph;
      let journal = Or_error.ok_exn (D.Journal.open_ ~path:":memory:") in
      let venue =
        D.Sim_venue.create ~opened_at:(Time_ns.now ())
          ~marks:(fun s -> Some (Graph.price graph s))
          ~now:Time_ns.now ~half_spread_bps:(fun _ -> 5.0) ~cash:(money 1_000_000.0) ~positions:[] ()
      in
      let oms =
        D.Oms.create ~graph ~journal
          ~spec:{ Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled }
          ~read:(Some (D.Sim_venue.read venue))
          ~trade:(Ok (D.Sim_venue.trade ~auto:false venue))
          ~halt:(D.Halt.create D.Halt.Source.none) ~accepts_tickets:false ~adv:(D.Oms.Adv.Fixed 1_000_000.0)
          ~now:Time_ns.now ~rng:(Random.State.make [| 3 |]) ~on_change:ignore ~on_event:ignore ~after_fill:ignore
          ~book_is_current:(fun () -> book_is_current) ()
      in
      D.Oms.set_market oms ~session_open:true ~adv20:[];
      f oms journal)

let ticket ?(kind = D.Order.Kind.Market) symbol side qty = { D.Ticket.symbol; side; qty; kind }
let rules (p : D.Oms.Preview.t) = List.map p.D.Oms.Preview.failures ~f:(fun x -> x.D.Rules.Failure.rule)

let created (p : D.Oms.Preview.t) =
  match p.D.Oms.Preview.verdict with
  | Some v -> List.map v.Ohcamel.Gate.Verdict.created ~f:(fun m -> m.Ohcamel.Gate.Move.limit)
  | None -> []

let test_a_preview_that_takes_tech_over_its_cap_names_it_and_creates_nothing () =
  with_oms ~f:(fun oms journal ->
      (* 100 x 150 = 15,000: under the order cap and 1% of volume, so every
         rule passes. TECH 75,000 + 30,000 = 105,000 > 100,000. *)
      let p = D.Oms.preview oms (ticket aapl D.Order.Side.Buy 100) in
      Alcotest.(check bool) "refused" false (D.Oms.Preview.passed p);
      Alcotest.(check (list string)) "by no rule" [] (rules p);
      Alcotest.(check (list string)) "by tech-cap" [ "tech-cap" ] (created p);
      Alcotest.(check bool) "and the reasons name it" true
        (List.exists (D.Oms.Preview.reasons p) ~f:(String.is_substring ~substring:"tech-cap"));
      Alcotest.(check int) "nothing was journaled" 0 (List.length (D.Journal.recent_orders journal ~limit:10)))

let test_the_rules_and_the_limits_both_speak () =
  with_oms ~f:(fun oms _ ->
      (* 200 x 150 = 30,000 > 25,000: the notional rule, and no other -- 200
         shares is under 1% of 1,000,000. AAPL 600 x 150 = 90,000 > 80,000
         and TECH 90,000 + 30,000 = 120,000 > 100,000: two limits, in the
         order they are configured. One rule and two limits: three reasons. *)
      let p = D.Oms.preview oms (ticket aapl D.Order.Side.Buy 200) in
      Alcotest.(check (list string)) "the rule" [ "notional" ] (rules p);
      Alcotest.(check (list string)) "the limits" [ "aapl-cap"; "tech-cap" ] (created p);
      Alcotest.(check int) "every reason, rules first" 3 (List.length (D.Oms.Preview.reasons p)))

let test_a_preview's_json_has_exactly_these_keys () =
  with_oms ~f:(fun oms _ ->
      let json = D.Oms.Preview.to_json (D.Oms.preview oms (ticket xom D.Order.Side.Buy 10)) in
      Alcotest.(check (list string))
        "ten, in order"
        [ "passed"; "symbol"; "side"; "qty"; "type"; "limit_price"; "decision_price"; "rules"; "gate"; "reasons" ]
        (Yojson.Safe.Util.keys json);
      (* XOM -200 -> -190: every limit further inside its line *)
      Alcotest.(check bool) "passed" true (Yojson.Safe.Util.(to_bool (member "passed" json)));
      Alcotest.(check (float 1e-9)) "priced at the mark" 100.0 Yojson.Safe.Util.(to_number (member "decision_price" json)))

(* The rules refuse an order on a book that is not the account's: before the
   first read is applied, or once the last is older than two sync intervals,
   the graph may hold the book file's quantities and cash, and the gate would
   judge a book nobody holds. XOM 10 x 100 = 1,000 passes every other rule and
   every limit (XOM -200 -> -190), so the trading rule speaks alone. *)
let test_a_book_that_is_not_the_account's_is_refused_by_the_trading_rule () =
  with_oms ~book_is_current:false ~f:(fun oms _ ->
      let p = D.Oms.preview oms (ticket xom D.Order.Side.Buy 10) in
      Alcotest.(check bool) "refused" false (D.Oms.Preview.passed p);
      Alcotest.(check (list string)) "by the trading rule alone" [ "trading" ] (rules p);
      Alcotest.(check bool) "saying why" true
        (List.exists (D.Oms.Preview.reasons p) ~f:(String.is_substring ~substring:"recent read of the account"));
      (* The frame's [trading] is [can_trade], so it reads off while the rule
         would refuse. *)
      Alcotest.(check bool) "and the frame's trading reads off" false (D.Oms.can_trade oms))

let suite =
  ( "oms",
    [
      Alcotest.test_case "a preview that takes TECH over its cap names it and creates nothing" `Quick
        test_a_preview_that_takes_tech_over_its_cap_names_it_and_creates_nothing;
      Alcotest.test_case "the rules and the limits both speak" `Quick test_the_rules_and_the_limits_both_speak;
      Alcotest.test_case "a preview's JSON has exactly these keys" `Quick test_a_preview's_json_has_exactly_these_keys;
      Alcotest.test_case "a book that is not the account's is refused by the trading rule" `Quick
        test_a_book_that_is_not_the_account's_is_refused_by_the_trading_rule;
    ] )
```

  Register `Test_oms.suite;` after `Test_reconcile.suite;`.

  In `test/desk_async/test_desk_async.ml` (Task 4's file, whose dune stanza already has the preprocessor), replace the header comment and the opens -- from `(* The desk with the scheduler running, and never the wall clock.` through `module D = Ohcamel_desk` -- with:

```ocaml
(* The desk with the scheduler running, and never the wall clock.

   A case that needs time to pass creates a clock of its own (Time_source.create)
   and advances it, so a bound of thirty seconds is crossed in no time at all.
   A case sees only its own timers: moving its clock fires nothing an earlier
   case left behind, and nothing here depends on which case ran first.

   The order manager's cases each build their own book, journal, venue and
   clock. The venue has no latency and fills only when the test pumps it, so
   every fill happens where the test says; [settle] then lets the pipe and the
   sequencer finish. Three names: AAPL 100 and MSFT 200 in TECH, XOM 50 in
   ENERGY, nothing held, a million in cash, a half-spread of 5 bps, and
   twenty-day volume fixed at a million shares. *)

open Core
open Async
open Ohcamel.Types
module Graph = Ohcamel.Graph
module Desk_spec = Ohcamel.Config.Book.Desk_spec
module D = Ohcamel_desk
```

  and add the following between Task 4's `let case ...` line and its transport case:

```ocaml
let aapl = Symbol.of_string "AAPL"
let msft = Symbol.of_string "MSFT"
let xom = Symbol.of_string "XOM"
let tech = Sector.of_string "TECH"

type fixture = {
  graph : Graph.t;
  journal : D.Journal.t;
  venue : D.Sim_venue.t;
  marks : float Symbol.Table.t;
  events : string Queue.t;
  (* The case's clock: the manager's lookups wait on it, and fire only when
     the case advances it. *)
  clock : Time_source.Read_write.t;
}

(* A print: the venue's mark and the graph's price move together, and the
   feed is fresh. *)
let mark f symbol price =
  Hashtbl.set f.marks ~key:symbol ~data:price;
  Graph.apply_tick f.graph { Tick.symbol; price = Price.of_float price; time = Time.now () };
  Graph.set_now f.graph (Time.now ());
  Graph.stabilize f.graph

let tech_cap = { Limit.name = "tech-cap"; scope = Limit.Sector tech; kind = Limit.Gross_notional (Notional.of_float 100_000.0) }

let fixture ?(limits = [ tech_cap ]) () =
  let graph =
    Graph.create ~starting_cash:(Notional.of_float 1_000_000.0)
      ~instruments:
        [
          { Instrument.symbol = aapl; sector = tech };
          { Instrument.symbol = msft; sector = tech };
          { Instrument.symbol = xom; sector = Sector.of_string "ENERGY" };
        ]
      ~limits ~confidence:0.95 ~return_window:10 ()
  in
  let marks = Symbol.Table.create () in
  let venue =
    D.Sim_venue.create ~latency:Time_ns.Span.zero ~opened_at:(Time_ns.now ())
      ~marks:(fun s -> Option.map (Hashtbl.find marks s) ~f:Price.of_float)
      ~now:Time_ns.now ~half_spread_bps:(fun _ -> 5.0) ~cash:(Notional.of_float 1_000_000.0) ~positions:[] ()
  in
  let f =
    {
      graph;
      journal = Or_error.ok_exn (D.Journal.open_ ~path:":memory:");
      venue;
      marks;
      events = Queue.create ();
      clock = Time_source.create ~now:t0 ();
    }
  in
  List.iter [ (aapl, 100.0); (msft, 200.0); (xom, 50.0) ] ~f:(fun (s, p) ->
      Graph.set_returns graph s [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |];
      mark f s p);
  f

(* A manager over the fixture. [trade] replaces the venue's trading half with
   one a case has wrapped; without it the manager trades the venue directly.
   Its lookups keep production's schedule -- 2, 10 and 30 s -- on the
   fixture's clock, so they fire only when a case advances it. *)
let manager ?trade ?(halt = D.Halt.create D.Halt.Source.none) f =
  let trade = match trade with Some t -> t | None -> D.Sim_venue.trade ~auto:false f.venue in
  let oms =
    D.Oms.create ~graph:f.graph ~journal:f.journal
      ~spec:{ Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled }
      ~read:(Some (D.Sim_venue.read f.venue))
      ~trade:(Ok trade) ~halt ~accepts_tickets:true ~adv:(D.Oms.Adv.Fixed 1_000_000.0) ~now:Time_ns.now
      ~rng:(Random.State.make [| 7 |]) ~on_change:ignore
      ~on_event:(fun e -> Queue.enqueue f.events e)
      ~after_fill:ignore ~book_is_current:(fun () -> true)
      ~time_source:(Time_source.read_only f.clock)
      ()
  in
  D.Oms.set_market oms ~session_open:true ~adv20:[];
  don't_wait_for (D.Oms.run oms);
  oms

(* Every job the last step queued -- the pipe, the sequencer, the journal's
   writes -- run to the end. Nothing here waits on the wall clock. *)
let settle () = Scheduler.yield_until_no_jobs_remain ()

let pump f =
  D.Sim_venue.pump f.venue;
  settle ()

let journaled f (o : D.Order.t) =
  (Option.value_exn (D.Journal.load_order f.journal o.D.Order.request.D.Order.Request.client_order_id))
    .D.Journal.Order_row.order

let state f o = D.Order.State.to_string (journaled f o).D.Order.state
let ticket ?(kind = D.Order.Kind.Market) symbol side qty = { D.Ticket.symbol; side; qty; kind }

(* Buy 100 AAPL at a mark of 100, sell 40 at 101, sell 60 at 102; every fill
   half a spread of 5 bps from the mark.

     buy  100 at 100 x 1.0005 = 100.05     cash  - 10,005.00
     sell  40 at 101 x 0.9995 = 100.9495   cash  +  4,037.98
     sell  60 at 102 x 0.9995 = 101.949    cash  +  6,116.94
                                           net   +    149.92

   At the marks alone the trades make 40 x 1 + 60 x 2 = 160; the spreads cost
   100 x 0.05 + 40 x 0.0505 + 60 x 0.051 = 5 + 2.02 + 3.06 = 10.08; and
   160 - 10.08 = 149.92. Each fill's shortfall is 5 bps of its decision mark;
   the arrival quote's mid is the mark, so delay is 0 and slippage is 5; and
   against the book's modelled 5 bps the difference is 0. *)
let test_three_fills () =
  let f = fixture () in
  let oms = manager f in
  let%bind _, buy = D.Oms.propose oms (ticket aapl D.Order.Side.Buy 100) in
  let%bind () = pump f in
  mark f aapl 101.0;
  let%bind _, sell40 = D.Oms.propose oms (ticket aapl D.Order.Side.Sell 40) in
  let%bind () = pump f in
  mark f aapl 102.0;
  let%bind _, sell60 = D.Oms.propose oms (ticket aapl D.Order.Side.Sell 60) in
  let%bind () = pump f in
  List.iter [ buy; sell40; sell60 ] ~f:(fun o -> Alcotest.(check string) "filled" "filled" (state f o));
  Alcotest.(check (float 1e-9)) "flat" 0.0 (Qty.to_float (Graph.qty f.graph aapl));
  Alcotest.(check (float 1e-6)) "cash 1,000,000 + 149.92" 1_000_149.92 (Notional.to_float (Graph.cash f.graph));
  let fills = D.Journal.recent_fills f.journal ~limit:10 in
  Alcotest.(check int) "three fills" 3 (List.length fills);
  let costs = D.Oms.costs oms fills in
  let s = D.Tca.summarize costs in
  Alcotest.(check (option (float 1e-6))) "shortfall, quantity-weighted" (Some 5.0) s.D.Tca.Summary.weighted_shortfall_bps;
  Alcotest.(check (option (float 1e-6))) "versus the model" (Some 0.0) s.D.Tca.Summary.mean_versus_model_bps;
  List.iter costs ~f:(fun (_, c) ->
      Alcotest.(check (option (float 1e-6))) "no delay: the quote's mid is the mark" (Some 0.0) c.D.Tca.Costs.delay_bps);
  Graph.destroy f.graph;
  return ()

(* Invariant 10. The venue receives the order and the answer is lost. The
   manager must learn the order's fate by its client order id -- here from the
   venue's own update, which answers as well as a lookup -- and the venue must
   have received it exactly once. *)
let test_an_unknown_answer_is_resolved_and_never_resent () =
  let f = fixture () in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let lost =
    {
      sim with
      D.Venue.Trade.submit =
        (fun r ->
          let%map (_ : D.Venue.Submission.t) = sim.D.Venue.Trade.submit r in
          D.Venue.Submission.Unknown "timed out after 10 s");
    }
  in
  let oms = manager ~trade:lost f in
  let%bind _, o = D.Oms.propose oms (ticket aapl D.Order.Side.Buy 10) in
  Alcotest.(check string) "unknown at first" "submit_unknown" (D.Order.State.to_string o.D.Order.state);
  let%bind () = settle () in
  Alcotest.(check (option string)) "found, with the venue's id" (Some "sim-1") (journaled f o).D.Order.venue_order_id;
  Alcotest.(check string) "and accepted" "accepted" (state f o);
  let%bind () = pump f in
  Alcotest.(check string) "then filled" "filled" (state f o);
  Alcotest.(check int) "and the venue received it once" 1 (D.Sim_venue.received f.venue);
  Graph.destroy f.graph;
  return ()

```

  and in `suites` (Task 4's list, which the count case sums), add after the `transport` group:

```ocaml
    ( "orders",
      [
        case "three fills, priced and costed by hand" test_three_fills;
        case "an unknown answer is resolved, and never resent" test_an_unknown_answer_is_resolved_and_never_resent;
      ] );
```

- [ ] **Step 2: Run and watch it fail.** `make test 2>&1 | grep -E 'Error|Unbound' | head -5`. Expected: `Unbound module Ohcamel_desk.Oms`.

- [ ] **Step 3: Implement.** `desk/oms.ml`:

```ocaml
(* The order manager (design §3.6-§3.8; invariants 9-12).

   Everything that decides is elsewhere, and pure: the rules, the gate, the
   state machine, what a venue's update means, what a restart learns. This
   module is the order in which they are asked, and the one place their
   answers meet the journal, the venue and the graph.

   ONE THING AT A TIME. Every change to an order -- a proposal, a venue
   update, a cancel, a reconciliation -- runs in one sequencer, so an update
   cannot be applied before the answer that created its order has been
   journaled, and two cancels cannot cross. A proposal holds the sequencer
   while the venue answers (the transport gives up after ten seconds);
   updates wait behind it, which is the order they happened in.

   JOURNAL BEFORE WIRE (invariant 10). An order is written as pending_submit
   before the request that submits it is sent. An unknown answer is resolved
   by the client order id -- the venue's own update, or lookups two, ten and
   thirty seconds apart (42 s in all), after which an order nobody found has
   failed; a lookup that errors is tried again every minute -- and never by
   sending the order again.

   FILLS ARE FACTS (invariant 11). A fill is journaled whatever state its
   order is in. The graph's position is then set from the venue's
   position_qty when the venue gives one, and the account is re-read
   ([after_fill]), because the venue's ledger is the one that is true.
   Positions follow the fills and the read corrects them: bin/main.ml's
   [after_fill] is Desk.after_fill, which refuses any account read already
   out when the fill landed, so a read that did not see the fill never undoes
   it (A1's final review, M4).

   ITS OWN CLOCK, WHEN A TEST GIVES ONE. Every wait here -- the lookups, the
   arrival quote's bound, the session refresh -- is on [time_source], the wall
   clock unless the caller passes another, so the scheduler suite crosses 42 s
   of lookups without waiting on the wall's. *)

open Core
open Async
open Ohcamel.Types
module Graph = Ohcamel.Graph
module Gate = Ohcamel.Gate
module Desk_spec = Ohcamel.Config.Book.Desk_spec

module Adv = struct
  (* Where twenty-day volume comes from: the venue's daily bars, or a fixed
     number -- the demo's, whose venue keeps no bars, and whose page says so. *)
  type t = From_venue | Fixed of float
end

type t = {
  graph : Graph.t;
  journal : Journal.t;
  spec : Desk_spec.t;
  read : Venue.Read.t option;
  trade : (Venue.Trade.t, string) Result.t;
  halt : Halt.t;
  accepts_tickets : bool;
  adv : Adv.t;
  now : unit -> Time_ns.t;
  rng : Random.State.t;
  on_change : unit -> unit;
  on_event : string -> unit;
  after_fill : unit -> unit;
  (* Whether the graph holds the account's book: a read of the account applied
     within the caller's window. The desk answers it; this module cannot name
     the desk. *)
  book_is_current : unit -> bool;
  resolve_delays : Time_ns.Span.t list;
  time_source : Time_source.t;
  sequencer : unit Throttle.Sequencer.t;
  (* Non-terminal orders by client order id: the journal's open orders, held. *)
  mutable open_ : Order.t String.Map.t;
  mutable recent : Rules.Recent.t list;
  mutable adv20 : float Symbol.Map.t;
  mutable session_open : bool;
}

let create ~graph ~journal ~spec ~read ~trade ~halt ~accepts_tickets ~adv ~now ~rng ~on_change ~on_event
    ~after_fill ~book_is_current
    ?(resolve_delays = [ Time_ns.Span.of_sec 2.0; Time_ns.Span.of_sec 10.0; Time_ns.Span.of_sec 30.0 ])
    ?(time_source = Time_source.wall_clock ()) () =
  {
    graph;
    journal;
    spec;
    read;
    trade;
    halt;
    accepts_tickets;
    adv;
    now;
    rng;
    on_change;
    on_event;
    after_fill;
    book_is_current;
    resolve_delays;
    time_source;
    sequencer = Throttle.Sequencer.create ~continue_on_error:true ();
    open_ = String.Map.empty;
    recent = [];
    adv20 = Symbol.Map.empty;
    session_open = false;
  }

let halt t = t.halt
let journal t = t.journal
let accepts_tickets t = t.accepts_tickets
let open_count t = Map.length t.open_
(* Whether an order could go now: the book enables trading, there is a
   trading half, and the graph holds the account's book. The frame's
   [trading] is this, so the page cannot read "on" while the trading rule
   would refuse every order for a book that is not the account's. *)
let can_trade t =
  Desk_spec.equal_trading t.spec.Desk_spec.trading Desk_spec.Enabled
  && Result.is_ok t.trade
  && t.book_is_current ()

let set_market t ~session_open ~adv20 =
  t.session_open <- session_open;
  t.adv20 <- Symbol.Map.of_alist_reduce adv20 ~f:(fun _ latest -> latest)

let key (o : Order.t) = Ids.Client_order_id.to_string o.Order.request.Order.Request.client_order_id
let enqueue t f = Throttle.enqueue t.sequencer f

let context t ~(symbol : Symbol.t) : Rules.Context.t =
  let now = t.now () in
  let universe = Symbol.Set.of_list (Graph.symbols t.graph) in
  let held = Set.mem universe symbol in
  let health = Graph.feed_health t.graph in
  let listed names = List.mem names symbol ~equal:Symbol.equal in
  {
    Rules.Context.spec = t.spec;
    universe;
    (* Invariant 12 gates an order on the live graph, so the graph must hold
       the account's book. Until a read of the account has been applied, or
       once the last applied one is older than the caller's window, it may
       still hold the book file's quantities and cash. *)
    can_trade =
      (match t.trade with
      | Error why -> Error why
      | Ok _ when not (t.book_is_current ()) -> Error Session_close.not_current
      | Ok _ -> Ok ());
    halted = Halt.reason t.halt;
    session_open = t.session_open;
    (* A price of zero is a cell nobody has written, not a price. *)
    mark =
      (if held && Float.( > ) (Price.to_float (Graph.price t.graph symbol)) 0.0 then Some (Graph.price t.graph symbol)
       else None);
    stale = held && (listed health.Graph.Feed_health.stale || listed health.Graph.Feed_health.never_seen);
    adv20 = (match t.adv with Adv.Fixed n -> Some n | Adv.From_venue -> Map.find t.adv20 symbol);
    recent =
      List.filter t.recent ~f:(fun r ->
          Float.( < ) (Time_ns.Span.to_sec (Time_ns.diff now r.Rules.Recent.at)) t.spec.Desk_spec.duplicate_window_s);
    open_orders = Map.length t.open_;
    now;
  }

module Preview = struct
  type t = {
    request : Order.Request.t;
    failures : Rules.Failure.t list;
    verdict : Gate.Verdict.t option;
    decision_price : Price.t option;
  }

  let passed t = List.is_empty t.failures && match t.verdict with Some v -> v.Gate.Verdict.passed | None -> false

  let reasons t =
    List.map t.failures ~f:(fun f -> sprintf "%s: %s" f.Rules.Failure.rule f.Rules.Failure.why)
    @ Option.value_map t.verdict ~default:[] ~f:Gate.Verdict.reasons

  let jnum x = if Float.is_finite x then `Float x else `Null
  let money n = jnum (Notional.to_float n)

  let breach_json = function
    | None -> `Null
    | Some (b : Breach.t) ->
        `Assoc
          [
            ("observed", jnum b.Breach.observed);
            ("threshold", jnum b.Breach.threshold);
            ("excess", jnum b.Breach.excess);
            ("breached", `Bool b.Breach.breached);
          ]

  let moves_json moves =
    `List
      (List.map moves ~f:(fun (m : Gate.Move.t) ->
           `Assoc
             [
               ("limit", `String m.Gate.Move.limit);
               ("before", breach_json m.Gate.Move.before);
               ("after", breach_json m.Gate.Move.after);
             ]))

  let to_json t : Yojson.Safe.t =
    let r = t.request in
    `Assoc
      [
        ("passed", `Bool (passed t));
        ("symbol", `String (Symbol.to_string r.Order.Request.symbol));
        ("side", `String (Order.Side.to_string r.Order.Request.side));
        ("qty", `Int r.Order.Request.qty);
        ("type", `String (Order.Kind.to_string r.Order.Request.kind));
        ( "limit_price",
          Option.value_map (Order.Kind.limit_price r.Order.Request.kind) ~default:`Null ~f:(fun p ->
              jnum (Price.to_float p)) );
        ("decision_price", Option.value_map t.decision_price ~default:`Null ~f:(fun p -> jnum (Price.to_float p)));
        ( "rules",
          `List
            (List.map t.failures ~f:(fun f ->
                 `Assoc [ ("rule", `String f.Rules.Failure.rule); ("why", `String f.Rules.Failure.why) ])) );
        ( "gate",
          match t.verdict with
          | None -> `Null
          | Some v ->
              `Assoc
                [
                  ("passed", `Bool v.Gate.Verdict.passed);
                  ("created", moves_json v.Gate.Verdict.created);
                  ("worsened", moves_json v.Gate.Verdict.worsened);
                  ("cleared", moves_json v.Gate.Verdict.cleared);
                  ("unevaluable", moves_json v.Gate.Verdict.unevaluable);
                  ("gross_before", money v.Gate.Verdict.gross_before);
                  ("gross_after", money v.Gate.Verdict.gross_after);
                  ("equity_before", money v.Gate.Verdict.equity_before);
                  ("equity_after", money v.Gate.Verdict.equity_after);
                ] );
        ("reasons", `List (List.map (reasons t) ~f:(fun s -> `String s)));
      ]
end

(* The rules and the gate, and nothing created. The gate runs whenever there
   is a price to run it at, even after a rule has failed: someone fixing a
   ticket wants to know what the trade would do to the limits as well as why
   it cannot be sent. A market order is gated at the mark, a limit order at
   its limit (§3.7). *)
let preview t (ticket : Ticket.t) : Preview.t =
  let now = t.now () in
  let request = Ticket.to_request ticket ~client_order_id:(Ids.Client_order_id.generate ~now ~rng:t.rng) in
  let ctx = context t ~symbol:ticket.Ticket.symbol in
  let decision_price =
    match ticket.Ticket.kind with Order.Kind.Limit p -> Some p | Order.Kind.Market -> ctx.Rules.Context.mark
  in
  let verdict =
    match decision_price with
    | Some price when Set.mem ctx.Rules.Context.universe ticket.Ticket.symbol && ticket.Ticket.qty > 0 ->
        let qty = Qty.of_float (Order.Side.sign ticket.Ticket.side *. Float.of_int ticket.Ticket.qty) in
        Some (Gate.check t.graph ~fills:[ { Gate.Fill.symbol = ticket.Ticket.symbol; qty; price } ])
    | _ -> None
  in
  { Preview.request; failures = Rules.check ctx request; verdict; decision_price }

(* One event, all the way down: the machine; the journal, the fill first when
   there is one; the open set; the page. Task 15 adds the switch's clause. *)
let record t (o : Order.t) (event : Order.Event.t) : Order.t =
  let o', anomaly = Order.apply o event in
  (match event with Order.Event.Venue_fill f -> ignore (Journal.record_fill t.journal o' f : bool) | _ -> ());
  Journal.update_order t.journal o' ~event ~anomaly ~at:(t.now ());
  Option.iter anomaly ~f:(fun a -> t.on_event (sprintf "desk      %s: %s" (key o') (Order.Anomaly.to_string a)));
  t.open_ <-
    (if Order.State.is_terminal o'.Order.state then Map.remove t.open_ (key o')
     else Map.set t.open_ ~key:(key o') ~data:o');
  t.on_change ();
  o'

(* A refusal is an order too: journaled, with its reasons, in a state that
   says it never reached a venue. *)
let refuse t (p : Preview.t) ~source =
  let o = Order.create p.Preview.request in
  Journal.insert_order t.journal o ~source
    ~decision_price:(Option.value p.Preview.decision_price ~default:(Price.of_float 0.0))
    ~arrival:None ~verdict:(Preview.to_json p) ~at:(t.now ());
  record t o (Order.Event.Pre_trade_rejected (Preview.reasons p))

let rec resolve t (client : Ids.Client_order_id.t) ~(delays : Time_ns.Span.t list) : unit Deferred.t =
  let delay, rest = match delays with d :: rest -> (d, rest) | [] -> (Time_ns.Span.of_min 1.0, []) in
  let%bind () = Time_source.after t.time_source delay in
  let%bind again =
    enqueue t (fun () ->
        match (Map.find t.open_ (Ids.Client_order_id.to_string client), t.trade) with
        | Some o, Ok trade when Order.State.equal o.Order.state Order.State.Submit_unknown -> (
            match%map trade.Venue.Trade.find_order client with
            | Ok (Some v) ->
                ignore (record t o (Order.Event.Found v.Venue.Venue_order.id) : Order.t);
                false
            | Ok None when List.is_empty rest ->
                ignore (record t o Order.Event.Not_found : Order.t);
                false
            | Ok None -> true
            | Error e ->
                t.on_event
                  (sprintf "desk      %s is still unknown; the lookup failed: %s" (Ids.Client_order_id.to_string client)
                     (Error.to_string_hum e));
                true)
        | _ -> return false)
  in
  if again then resolve t client ~delays:rest else Deferred.unit

let propose t ?(source = "manual") (ticket : Ticket.t) : (Preview.t * Order.t) Deferred.t =
  enqueue t (fun () ->
      let p = preview t ticket in
      if not (Preview.passed p) then return (p, refuse t p ~source)
      else
        let request = p.Preview.request in
        let symbol = request.Order.Request.symbol in
        (* Two seconds for the arrival quote. The sequencer is held while it
           is fetched, and an order is better sent without a quote than held
           while a request hangs; its costs then carry shortfall alone. *)
        let%bind arrival =
          match t.read with
          | None -> return None
          | Some read -> (
              match%map Time_source.with_timeout t.time_source (Time_ns.Span.of_sec 2.0) (read.Venue.Read.latest_quote symbol) with
              | `Result (Ok (Some q)) -> Some (q.Venue.Quote.bid, q.Venue.Quote.ask)
              | `Result (Ok None) | `Result (Error _) | `Timeout -> None)
        in
        (* The switch may have tripped while the quote was fetched, and the
           book's last applied read may have aged past the window. *)
        match (Halt.reason t.halt, t.trade) with
        | Some why, _ ->
            let p = { p with Preview.failures = p.Preview.failures @ [ { Rules.Failure.rule = "kill_switch"; why } ] } in
            return (p, refuse t p ~source)
        | None, Error why ->
            let p = { p with Preview.failures = [ { Rules.Failure.rule = "trading"; why } ] } in
            return (p, refuse t p ~source)
        | None, Ok _ when not (t.book_is_current ()) ->
            let p = { p with Preview.failures = [ { Rules.Failure.rule = "trading"; why = Session_close.not_current } ] } in
            return (p, refuse t p ~source)
        | None, Ok trade ->
            let o = Order.create request in
            Journal.insert_order t.journal o ~source
              ~decision_price:(Option.value_exn p.Preview.decision_price)
              ~arrival ~verdict:(Preview.to_json p) ~at:(t.now ());
            t.open_ <- Map.set t.open_ ~key:(key o) ~data:o;
            t.recent <-
              { Rules.Recent.symbol; side = request.Order.Request.side; qty = request.Order.Request.qty; at = t.now () }
              :: (context t ~symbol).Rules.Context.recent;
            t.on_change ();
            let%map submission = trade.Venue.Trade.submit request in
            let o =
              match submission with
              | Venue.Submission.Accepted v -> record t o (Order.Event.Acknowledged v.Venue.Venue_order.id)
              | Venue.Submission.Rejected why -> record t o (Order.Event.Venue_rejected_submission why)
              | Venue.Submission.Unknown why ->
                  let o = record t o (Order.Event.Outcome_unknown why) in
                  don't_wait_for (resolve t request.Order.Request.client_order_id ~delays:t.resolve_delays);
                  o
            in
            (p, o))

let apply_to_graph t (o : Order.t) (f : Order.Fill.t) =
  let symbol = o.Order.request.Order.Request.symbol in
  if List.mem (Graph.symbols t.graph) symbol ~equal:Symbol.equal then (
    Graph.apply_fill t.graph
      {
        Fill.symbol;
        qty = Qty.of_float (Order.Side.sign o.Order.request.Order.Request.side *. f.Order.Fill.qty);
        price = f.Order.Fill.price;
        time = f.Order.Fill.at;
      };
    Option.iter f.Order.Fill.position_qty ~f:(fun q -> Graph.set_qty t.graph symbol (Qty.of_float q));
    Graph.stabilize t.graph);
  t.after_fill ()

let on_update t (u : Venue.Update.t) : unit Deferred.t =
  enqueue t (fun () ->
      let raw = u.Venue.Update.order.Venue.Venue_order.client_order_id in
      let known =
        match Map.find t.open_ raw with
        | Some o -> Some o
        | None ->
            Option.bind (Ids.Client_order_id.of_string raw) ~f:(fun c ->
                Option.map (Journal.load_order t.journal c) ~f:(fun r -> r.Journal.Order_row.order))
      in
      (match known with
      | None ->
          t.on_event
            (sprintf "desk      %s for an order this desk has no record of (%s %s)" u.Venue.Update.event raw
               (Symbol.to_string u.Venue.Update.order.Venue.Venue_order.symbol))
      | Some o -> (
          (* The venue's own update answers an unknown submission as well as a
             lookup would. It also gives an order that moved on without its id
             -- a fill read before the POST's answer, a reconciliation that
             took the stream's word -- the id the venue cancels by (Task 1). *)
          let o =
            if Option.is_none o.Order.venue_order_id then
              record t o (Order.Event.Found u.Venue.Update.order.Venue.Venue_order.id)
            else o
          in
          (* Failed is terminal, so nothing below revives it: its fills are
             journaled as fills after failed and the book follows the venue.
             What cannot follow is the switch, which cancels open orders only;
             so it is said, loudly. *)
          if Order.State.equal o.Order.state Order.State.Failed then
            t.on_event
              (sprintf
                 "desk      the venue reports %s, which this desk declared failed when its lookups found nothing; its \
                  fills are journaled and the book follows the venue, but the switch cannot cancel it"
                 raw);
          let o =
            match u.Venue.Update.fill with
            | Some f when Reconcile.fill_is_new o u ->
                let o = record t o (Order.Event.Venue_fill f) in
                apply_to_graph t o f;
                o
            | Some f ->
                t.on_event (sprintf "desk      %s: execution %s is already counted" raw f.Order.Fill.execution_id);
                o
            | None -> o
          in
          match (u.Venue.Update.event, Venue.Update.to_event u) with
          | ("fill" | "partial_fill"), _ -> ()
          | _, Some e -> ignore (record t o e : Order.t)
          | _, None ->
              t.on_event
                (sprintf "desk      %s: %s changes nothing the order's states describe" raw u.Venue.Update.event)));
      Deferred.unit)

let run t : unit Deferred.t =
  match t.trade with Error _ -> Deferred.unit | Ok trade -> Pipe.iter trade.Venue.Trade.updates ~f:(on_update t)

let jnum x = if Float.is_finite x then `Float x else `Null
let jstr_opt = Option.value_map ~default:`Null ~f:(fun s -> `String s)

let order_json (r : Journal.Order_row.t) : Yojson.Safe.t =
  let o = r.Journal.Order_row.order in
  let q = o.Order.request in
  `Assoc
    [
      ("client_order_id", `String (Ids.Client_order_id.to_string q.Order.Request.client_order_id));
      ("venue_order_id", jstr_opt o.Order.venue_order_id);
      ("source", `String r.Journal.Order_row.source);
      ("symbol", `String (Symbol.to_string q.Order.Request.symbol));
      ("side", `String (Order.Side.to_string q.Order.Request.side));
      ("qty", `Int q.Order.Request.qty);
      ("type", `String (Order.Kind.to_string q.Order.Request.kind));
      ( "limit_price",
        Option.value_map (Order.Kind.limit_price q.Order.Request.kind) ~default:`Null ~f:(fun p -> jnum (Price.to_float p))
      );
      ("state", `String (Order.State.to_string o.Order.state));
      ("filled_qty", jnum o.Order.filled_qty);
      ("avg_fill_price", Option.value_map (Order.avg_fill_price o) ~default:`Null ~f:jnum);
      (* A refusal with no mark was journaled at 0 -- the column is NOT NULL --
         and no price was decided. *)
      ( "decision_price",
        let p = Price.to_float r.Journal.Order_row.decision_price in
        if Float.( > ) p 0.0 then jnum p else `Null );
      ("reason", jstr_opt o.Order.reason);
      ("created_at", `String (Desk_time.rfc3339 r.Journal.Order_row.created_at));
      ("updated_at", `String (Desk_time.rfc3339 r.Journal.Order_row.updated_at));
    ]

(* The book's spread_bps is a half-spread: what one fill pays to cross from
   the mid to the far side. *)
let model_half_spread_bps t symbol =
  Option.value
    (List.Assoc.find t.spec.Desk_spec.spread_bps (Symbol.to_string symbol) ~equal:String.equal)
    ~default:t.spec.Desk_spec.spread_bps_default

let costs t (rows : Journal.Fill_row.t list) : (Tca.Inputs.t * Tca.Costs.t) list =
  List.map rows ~f:(fun (r : Journal.Fill_row.t) ->
      let inputs =
        {
          Tca.Inputs.symbol = r.Journal.Fill_row.symbol;
          side = r.Journal.Fill_row.side;
          qty = r.Journal.Fill_row.fill.Order.Fill.qty;
          decision = Price.to_float r.Journal.Fill_row.decision_price;
          bid = Option.map r.Journal.Fill_row.arrival ~f:(fun (b, _) -> Price.to_float b);
          ask = Option.map r.Journal.Fill_row.arrival ~f:(fun (_, a) -> Price.to_float a);
          fill = Price.to_float r.Journal.Fill_row.fill.Order.Fill.price;
        }
      in
      (inputs, Tca.of_fill ~model_half_spread_bps:(model_half_spread_bps t r.Journal.Fill_row.symbol) inputs))
```

  Notes for the implementer:
  - `Fill.symbol` in `apply_to_graph` is `Ohcamel.Types.Fill`, the kernel's signed fill; `Order.Fill` is the venue's.
  - `Throttle.Sequencer.create ~continue_on_error:true ()` keeps one failed job from wedging every later one. A job that raises -- a journal write raises by design -- sends its exception to the caller's monitor; under `don't_wait_for (Oms.run oms)` that stops the process, and the restart reconciles. That is the intended failure: a desk that cannot write its journal must not go on trading.
  - A refused proposal is not added to `recent`: the duplicate rule exists to stop the same order twice, and a refusal sent nothing.
  - `Time_source.t` is Async's read-only time source. Production passes nothing and gets the wall clock. The scheduler suite passes the fixture's clock, so the lookups' 2, 10 and 30 s pass without waiting on the wall's.

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 433` (429 + 4, the main suite: three preview cases and the stale book's) and `let scheduler_tests = 4` (Task 4's two and these two). The scheduler suite's four cases pass beside it: `make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'` shows both executables' lines.

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'
git add desk/oms.ml test/test_oms.ml test/desk_async/test_desk_async.ml test/test_ohcamel.ml lib/verified.ml
git commit -F - <<'EOF'
desk: the order manager -- rules, then the limits, then the journal, then the venue, one change at a time -- tested with the scheduler running in its own executable on a clock each case advances, because the main suite's promise is that it never starts one and no test waits on the wall clock

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 15: Cancels, the switch, and a restart

**Files:**
- Modify: `desk/oms.ml`, `test/desk_async/test_desk_async.ml`, `lib/verified.ml` (`scheduler_tests`)

**Interfaces:**
- Consumes: Task 14's `Oms` (with its `?time_source`); `Reconcile.events_for`; `Halt`; `Ohcamel.Alerts.on_trip`; `Journal.open_orders`; `Venue.Read.clock`/`daily_bars`; `Venue.Trade.cancel`/`find_order`.
- Produces (in `Ohcamel_desk.Oms`):
  - `cancel : t -> Ids.Client_order_id.t -> (Order.t, string) Result.t Deferred.t`.
  - `cancel_all : t -> unit Deferred.t`; `kill : t -> why:string -> unit Deferred.t` (halts by hand, then cancels every open order).
  - `watch_alerts : t -> Ohcamel.Alerts.t -> unit` (a trip cancels every open order).
  - `reconcile : t -> unit Deferred.t` (the journal's open orders, looked up and told what the venue knows; then `after_fill`).
  - `refresh : ?bars:bool -> t -> unit Deferred.t` (the session from the venue's clock; with `bars`, the default, twenty-day volume from its daily bars); `refresh_forever : t -> every:Time_ns.Span.t -> unit Deferred.t` (bars once an hour).
  - `changed : t -> unit` (asks the page for a frame).
  - `record` now cancels an order the moment the venue first gives it an id, when the switch is not clear.

No main-suite count change (433 stands): every case this task adds needs the scheduler. `scheduler_tests` 4 -> 7.

- [ ] **Step 1: Write the failing tests.**

  In `test/desk_async/test_desk_async.ml`, add above `let suites =`:

```ocaml
let limit_at_99 = D.Order.Kind.Limit (Price.of_float 99.0)
let rules (p : D.Oms.Preview.t) = List.map p.D.Oms.Preview.failures ~f:(fun x -> x.D.Rules.Failure.rule)

(* A limit buy at 99 rests: the ask is 100 x 1.0005 = 100.05, above it, and
   99 is 1% from the mark, inside the 5% collar. A halt by hand cancels it at
   the venue; the next proposal is refused, naming only the switch; after a
   reset a proposal goes through. *)
let test_a_halt_refuses_and_cancels () =
  let f = fixture () in
  let oms = manager f in
  let%bind _, resting = D.Oms.propose oms (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 50) in
  let%bind () = pump f in
  Alcotest.(check string) "resting" "accepted" (state f resting);
  let%bind () = D.Oms.kill oms ~why:"testing the switch" in
  let%bind () = pump f in
  Alcotest.(check string) "cancelled at the venue" "cancelled" (state f resting);
  let%bind p, refused = D.Oms.propose oms (ticket aapl D.Order.Side.Buy 1) in
  Alcotest.(check string) "refused" "rejected_pre_trade" (state f refused);
  Alcotest.(check (list string)) "by the switch alone" [ "kill_switch" ] (rules p);
  D.Halt.reset (D.Oms.halt oms);
  let%bind _, placed = D.Oms.propose oms (ticket aapl D.Order.Side.Buy 2) in
  let%bind () = pump f in
  Alcotest.(check string) "after a reset, filled" "filled" (state f placed);
  Graph.destroy f.graph;
  return ()

(* The kernel's switch, armed on aapl-cap at 20,000. A resting buy of 50 at
   99 is 4,950, well inside it. Then the book's AAPL is set to 300 x 100 =
   30,000, over the cap, and the trip cancels the order with nobody asking. *)
let test_a_limit's_trip_cancels_the_open_orders () =
  let aapl_cap =
    { Limit.name = "aapl-cap"; scope = Limit.Instrument aapl; kind = Limit.Gross_notional (Notional.of_float 20_000.0) }
  in
  let f = fixture ~limits:[ aapl_cap ] () in
  let config =
    {
      Ohcamel.Config.Alerts.default with
      Ohcamel.Config.Alerts.enabled = true;
      sinks = [];
      kill_switch_enabled = true;
      kill_switch_trips_on = [ "aapl-cap" ];
    }
  in
  let alerts = Option.value_exn (Or_error.ok_exn (Ohcamel.Alerts.attach ~graph:f.graph ~config)) in
  let oms = manager ~halt:(D.Halt.create (D.Halt.Source.of_alerts alerts)) f in
  D.Oms.watch_alerts oms alerts;
  let%bind _, resting = D.Oms.propose oms (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 50) in
  let%bind () = pump f in
  Alcotest.(check string) "resting" "accepted" (state f resting);
  Graph.set_qty f.graph aapl (Qty.of_float 300.0);
  Graph.stabilize f.graph;
  let%bind () = pump f in
  Alcotest.(check string) "cancelled by the trip" "cancelled" (state f resting);
  Alcotest.(check string) "and the switch reads tripped" "tripped" (D.Halt.State.name (D.Halt.state (D.Oms.halt oms)));
  Graph.destroy f.graph;
  return ()

(* A process killed mid-order. Two managers over one journal and one venue
   never hear back: the venue received the first's order, and never the
   second's. A third manager -- the restart -- reconciles: the first order is
   found and then fills; the second becomes an unknown and fails when the
   restart's lookups all miss -- 2, 10 and 30 s apart on the fixture's clock,
   42 s in all, and not a moment sooner; nothing was sent twice. The two that
   never hear back get update pipes of their own that are already closed, so
   neither can take the venue's updates from the restart. *)
let test_a_restart_reconciles () =
  let f = fixture () in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let answer_lost =
    {
      sim with
      D.Venue.Trade.submit =
        (fun r ->
          don't_wait_for (Deferred.ignore_m (sim.D.Venue.Trade.submit r));
          Deferred.never ());
      updates = Pipe.empty ();
    }
  in
  let never_sent = { answer_lost with D.Venue.Trade.submit = (fun _ -> Deferred.never ()) } in
  let first = manager ~trade:answer_lost f and second = manager ~trade:never_sent f in
  don't_wait_for (Deferred.ignore_m (D.Oms.propose first (ticket aapl D.Order.Side.Buy 10)));
  don't_wait_for (Deferred.ignore_m (D.Oms.propose second (ticket msft D.Order.Side.Buy 5)));
  let%bind () = settle () in
  let states () =
    D.Journal.recent_orders f.journal ~limit:10
    |> List.map ~f:(fun r ->
           let o = r.D.Journal.Order_row.order in
           (Symbol.to_string o.D.Order.request.D.Order.Request.symbol, D.Order.State.to_string o.D.Order.state))
    |> List.sort ~compare:(Tuple2.compare ~cmp1:String.compare ~cmp2:String.compare)
  in
  Alcotest.(check (list (pair string string)))
    "both journaled before the wire, neither answered"
    [ ("AAPL", "pending_submit"); ("MSFT", "pending_submit") ]
    (states ());
  Alcotest.(check int) "the venue received one" 1 (D.Sim_venue.received f.venue);
  let restarted = manager f in
  let%bind () = D.Oms.reconcile restarted in
  let%bind () = pump f in
  (* The reconciliation's own lookup ran at the clock's start, t0, and missed
     MSFT. Its scheduled lookups follow at t0 + 2 s, t0 + 2 + 10 = 12 s and
     t0 + 12 + 30 = 42 s. advance_by_alarms stops at each alarm's own time and
     runs its jobs (wait_for) before it moves on, so each delay is measured
     from the lookup before it. *)
  let%bind () = Time_source.advance_by_alarms ~wait_for:settle f.clock ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 41.0)) in
  let%bind () = settle () in
  Alcotest.(check (list (pair string string)))
    "at 41 s: found and filled; the other still unknown, one lookup to go"
    [ ("AAPL", "filled"); ("MSFT", "submit_unknown") ]
    (states ());
  let%bind () = Time_source.advance_by_alarms ~wait_for:settle f.clock ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 42.0)) in
  let%bind () = settle () in
  Alcotest.(check (list (pair string string)))
    "at 42 s the last lookup misses: failed"
    [ ("AAPL", "filled"); ("MSFT", "failed") ]
    (states ());
  Alcotest.(check int) "and nothing was sent again" 1 (D.Sim_venue.received f.venue);
  Graph.destroy f.graph;
  return ()
```

  and add three cases to the `"orders"` group in `suites`, after Task 14's two:

```ocaml
        case "a halt refuses new orders and cancels the open ones" test_a_halt_refuses_and_cancels;
        case "a limit's trip cancels the open orders" test_a_limit's_trip_cancels_the_open_orders;
        case "a restart reconciles against the venue" test_a_restart_reconciles;
```

- [ ] **Step 2: Run and watch it fail.** Expected: `Unbound value D.Oms.kill`.

- [ ] **Step 3: Implement.** In `desk/oms.ml`:
  1. Replace `let record ...` (the whole function, and its comment) with this pair, and move `let refuse`, `let rec resolve` and `let propose` below it unchanged:

```ocaml
(* One event, all the way down: the machine; the journal, the fill first when
   there is one; the open set; the page. And the switch: an order the venue
   first gives an id while the switch is not clear -- a kill that landed while
   its request was in flight, an unknown found after a trip -- is cancelled at
   once, by that id. *)
let rec record t (o : Order.t) (event : Order.Event.t) : Order.t =
  let o', anomaly = Order.apply o event in
  (match event with Order.Event.Venue_fill f -> ignore (Journal.record_fill t.journal o' f : bool) | _ -> ());
  Journal.update_order t.journal o' ~event ~anomaly ~at:(t.now ());
  Option.iter anomaly ~f:(fun a -> t.on_event (sprintf "desk      %s: %s" (key o') (Order.Anomaly.to_string a)));
  t.open_ <-
    (if Order.State.is_terminal o'.Order.state then Map.remove t.open_ (key o')
     else Map.set t.open_ ~key:(key o') ~data:o');
  t.on_change ();
  (* Only the event that first gives the order a venue id: every later
     transition of the same order would queue another cancel for an order
     already pending_cancel, which the venue refuses and the log would print
     in the middle of a kill. *)
  if
    Option.is_some (Halt.reason t.halt)
    && Option.is_none o.Order.venue_order_id
    && Option.is_some o'.Order.venue_order_id
    && not (Order.State.is_terminal o'.Order.state)
  then don't_wait_for (Deferred.ignore_m (cancel t o'.Order.request.Order.Request.client_order_id));
  o'

and cancel t (client : Ids.Client_order_id.t) : (Order.t, string) Result.t Deferred.t =
  enqueue t (fun () ->
      match (Map.find t.open_ (Ids.Client_order_id.to_string client), t.trade) with
      | None, _ -> return (Error "no open order has that id")
      | Some _, Error why -> return (Error why)
      | Some o, Ok trade -> (
          match o.Order.venue_order_id with
          | None ->
              return
                (Error
                   (sprintf "the order is %s, and the venue has not given it an id to cancel by yet"
                      (Order.State.to_string o.Order.state)))
          | Some id -> (
              let o =
                if Order.State.equal o.Order.state Order.State.Pending_cancel then o
                else record t o Order.Event.Cancel_requested
              in
              match%map trade.Venue.Trade.cancel id with
              | Ok () -> Ok o
              | Error e ->
                  (* The order stays pending_cancel: the venue's next update --
                     a fill, most likely, if it could not be cancelled -- says
                     what became of it. *)
                  t.on_event
                    (sprintf "desk      the venue did not cancel %s: %s" (key o) (Error.to_string_hum e));
                  Error (Error.to_string_hum e))))
```

  2. At the end of the file, add:

```ocaml
let changed t = t.on_change ()

(* Every open order the venue has an id for. One without an id yet is
   cancelled by [record] the moment the venue gives it one. *)
let cancel_all t : unit Deferred.t =
  Deferred.List.iter ~how:`Sequential (Map.data t.open_) ~f:(fun o ->
      match o.Order.venue_order_id with
      | None -> Deferred.unit
      | Some _ -> Deferred.ignore_m (cancel t o.Order.request.Order.Request.client_order_id))

let kill t ~why : unit Deferred.t =
  Halt.halt t.halt ~why ~at:(t.now ());
  t.on_event ("desk      HALTED by hand: " ^ why);
  t.on_change ();
  cancel_all t

(* The other half of §3.8. The switch already refuses new orders through
   [Halt.reason]; a trip must also cancel the open ones. The handler runs
   inside stabilization, so it only schedules. *)
let watch_alerts t (alerts : Ohcamel.Alerts.t) =
  Ohcamel.Alerts.on_trip alerts ~f:(fun event ->
      upon Deferred.unit (fun () ->
          t.on_event
            (sprintf "desk      the kill switch tripped on %s: cancelling every open order"
               event.Ohcamel.Alerts.Event.limit_name);
          t.on_change ();
          don't_wait_for (cancel_all t)))

(* On start, and after every reconnection of the venue's update stream: the
   journal's open orders, each looked up by client order id and told what the
   venue knows (Reconcile.events_for). Then the account is re-read, because
   fills nobody heard about moved it. *)
let reconcile t : unit Deferred.t =
  enqueue t (fun () ->
      let rows = Journal.open_orders t.journal in
      t.open_ <-
        String.Map.of_alist_reduce
          (List.map rows ~f:(fun r -> (key r.Journal.Order_row.order, r.Journal.Order_row.order)))
          ~f:(fun _ latest -> latest);
      match t.trade with
      | Error _ -> Deferred.unit
      | Ok trade ->
          let%map () =
            Deferred.List.iter ~how:`Sequential rows ~f:(fun r ->
                let o = r.Journal.Order_row.order in
                match%map trade.Venue.Trade.find_order o.Order.request.Order.Request.client_order_id with
                | Error e ->
                    t.on_event (sprintf "desk      reconcile: %s could not be looked up: %s" (key o) (Error.to_string_hum e))
                | Ok venue ->
                    let events = Reconcile.events_for o venue ~at:(t.now ()) in
                    if Option.is_none venue && Option.is_some o.Order.venue_order_id then
                      t.on_event (sprintf "desk      reconcile: the venue has no order %s, which it once acknowledged" (key o));
                    let o = List.fold events ~init:o ~f:(record t) in
                    (* An order nobody has found yet is decided by the lookups
                       a timed-out submission gets, not by this one miss. *)
                    if Order.State.equal o.Order.state Order.State.Submit_unknown then
                      don't_wait_for (resolve t o.Order.request.Order.Request.client_order_id ~delays:t.resolve_delays))
          in
          if not (List.is_empty rows) then t.after_fill ();
          t.on_event
            (sprintf "desk      reconciled %d open order%s against the venue" (List.length rows)
               (if List.length rows = 1 then "" else "s")))

let refresh ?(bars = true) t : unit Deferred.t =
  match t.read with
  | None -> Deferred.unit
  | Some read ->
      let%bind () =
        match%map read.Venue.Read.clock () with
        | Ok c -> t.session_open <- c.Venue.Session_clock.is_open
        | Error e ->
            t.session_open <- false;
            t.on_event ("desk      the venue's clock is unavailable, so the session reads closed: " ^ Error.to_string_hum e)
      in
      let%map () =
        match t.adv with
        | Adv.Fixed _ -> Deferred.unit
        | Adv.From_venue when not bars -> Deferred.unit
        | Adv.From_venue -> (
            match%map read.Venue.Read.daily_bars (Graph.symbols t.graph) ~days:20 with
            | Error e -> t.on_event ("desk      twenty-day volume unavailable: " ^ Error.to_string_hum e)
            | Ok by_symbol ->
                (* Fewer than ten sessions is not a twenty-day average; the adv
                   rule then refuses the name, which is its job. *)
                t.adv20 <-
                  Map.filter_map by_symbol ~f:(fun bars ->
                      let recent = List.drop bars (Int.max 0 (List.length bars - 20)) in
                      if List.length recent < 10 then None
                      else
                        Some
                          (List.sum (module Float) recent ~f:(fun b -> b.Venue.Bar.volume)
                          /. Float.of_int (List.length recent))))
      in
      t.on_change ()

let refresh_forever t ~every : unit Deferred.t =
  let rec loop n =
    let%bind () = refresh t ~bars:(n % 60 = 0) in
    let%bind () = Time_source.after t.time_source every in
    loop (n + 1)
  in
  loop 0
```

- [ ] **Step 4: Run and watch it pass.** Set `let scheduler_tests = 7` (4 + these three). `make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'`: the main suite's count unchanged at 433, the scheduler suite's seven cases passing (Task 4's two, Task 14's two, these three).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'
git add desk/oms.ml test/desk_async/test_desk_async.ml lib/verified.ml
git commit -F - <<'EOF'
desk: cancels, the switch that cancels too, and a restart that asks the venue what happened -- because a halt that leaves orders resting and a restart that guesses are the two ways a desk loses track of its own risk

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 16: The routes, and who may use them

**Files:**
- Create: `desk/desk_routes.ml`, `test/test_desk_routes.ml`
- Modify: `desk/oms.ml` (JSON for fills and costs), `test/test_ohcamel.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: `Ohcamel.Server` (`Request`, `extension`, `respond_json`, `dispatch`, `create`); `Oms` (`preview`, `propose`, `cancel`, `kill`, `halt`, `changed`, `journal`, `costs`); `Halt`; `Ticket`; `Journal.recent_sessions`, `Journal.recent_fills`.
- Produces (in `Ohcamel_desk.Oms`): `venue_name : t -> string`; `fills_json : t -> Journal.Fill_row.t list -> Yojson.Safe.t`; `tca_json : t -> Yojson.Safe.t` (keys `note`, `overall`, `by_symbol`).
- Produces (module `Ohcamel_desk.Desk_routes`):
  - `host = [ `Demo | `Live ]`.
  - `Protection.check : host:host -> Server.Request.t -> (unit, Cohttp.Code.status_code * string) Result.t`.
  - `extensions : host:host -> oms:Oms.t -> Server.extension list` — `/api/desk/tca`, `/api/desk/sessions`, `/api/desk/preview`, `/api/desk/orders`, `/api/desk/cancel`, `/api/desk/kill`, `/api/desk/kill/reset`, in that order.

- [ ] **Step 1: Write the failing tests.** `test/test_desk_routes.ml`:

```ocaml
(* The desk's routes, dispatched through the server with no scheduler: every
   answer below is determined before it is returned, because the paths tested
   here -- protection, a preview, a malformed ticket, a reset -- wait on
   nothing. The routes that wait on a venue are the async suite's. *)

open Core
open Ohcamel.Types
module Server = Ohcamel.Server
module Graph = Ohcamel.Graph
module Desk_spec = Ohcamel.Config.Book.Desk_spec
module D = Ohcamel_desk

let aapl = Symbol.of_string "AAPL"

let request ?(meth = `POST) ?(headers = []) ?(body = "") path =
  { Server.Request.meth; path; headers = Cohttp.Header.of_list headers; body }

let from_this_site = [ ("X-OhCamel-Desk", "1"); ("Sec-Fetch-Site", "same-origin") ]

let dispatched server (r : Server.Request.t) =
  match Async.Deferred.peek (Server.dispatch server r) with
  | None -> Alcotest.failf "%s did not answer without the scheduler" r.Server.Request.path
  | Some (response, body) ->
      let text =
        match body with
        | `String s -> s
        | `Empty -> ""
        | `Strings ss -> String.concat ss
        | `Pipe _ -> Alcotest.failf "%s answered with a pipe" r.Server.Request.path
      in
      (Cohttp.Code.code_of_status (Cohttp.Response.status response), text)

let with_routes ~(host : D.Desk_routes.host) ~f =
  let graph =
    Graph.create ~starting_cash:(Notional.of_float 1_000_000.0)
      ~instruments:[ { Instrument.symbol = aapl; sector = Sector.of_string "TECH" } ]
      ~limits:[] ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.set_returns graph aapl [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |];
      Graph.apply_tick graph { Tick.symbol = aapl; price = Price.of_float 150.0; time = Time.now () };
      Graph.set_now graph (Time.now ());
      Graph.stabilize graph;
      let journal = Or_error.ok_exn (D.Journal.open_ ~path:":memory:") in
      let venue =
        D.Sim_venue.create ~opened_at:(Time_ns.now ())
          ~marks:(fun _ -> Some (Price.of_float 150.0))
          ~now:Time_ns.now ~half_spread_bps:(fun _ -> 5.0) ~cash:(Notional.of_float 1_000_000.0) ~positions:[] ()
      in
      let oms =
        D.Oms.create ~graph ~journal
          ~spec:{ Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled }
          ~read:(Some (D.Sim_venue.read venue))
          ~trade:(Ok (D.Sim_venue.trade ~auto:false venue))
          ~halt:(D.Halt.create D.Halt.Source.none)
          ~accepts_tickets:(Poly.equal host `Live) ~adv:(D.Oms.Adv.Fixed 1_000_000.0) ~now:Time_ns.now
          ~rng:(Random.State.make [| 11 |]) ~on_change:ignore ~on_event:ignore ~after_fill:ignore
          ~book_is_current:(fun () -> true) ()
      in
      D.Oms.set_market oms ~session_open:true ~adv20:[];
      let server =
        Server.create ~extensions:(D.Desk_routes.extensions ~host ~oms) ~mode:host ~graph ~factor:"SYNTHETIC" ()
      in
      f server oms)

let test_who_may_change_the_desk () =
  let check what expected ~host r =
    let got =
      match D.Desk_routes.Protection.check ~host r with
      | Ok () -> 200
      | Error (status, _) -> Cohttp.Code.code_of_status status
    in
    Alcotest.(check int) what expected got
  in
  let p = "/api/desk/orders" in
  check "the demo: 405, whatever the request says" 405 ~host:`Demo (request ~headers:from_this_site p);
  check "live, a GET: 405" 405 ~host:`Live (request ~meth:`GET ~headers:from_this_site p);
  check "live, no header: 403" 403 ~host:`Live (request ~headers:[ ("Sec-Fetch-Site", "same-origin") ] p);
  check "live, the header with another value: 403" 403 ~host:`Live
    (request ~headers:[ ("X-OhCamel-Desk", "yes"); ("Sec-Fetch-Site", "same-origin") ] p);
  check "live, the header and same-origin, any case: allowed" 200 ~host:`Live
    (request ~headers:[ ("x-ohcamel-desk", "1"); ("sec-fetch-site", "same-origin") ] p);
  check "live, the header and an Origin that is the Host: allowed" 200 ~host:`Live
    (request ~headers:[ ("X-OhCamel-Desk", "1"); ("Origin", "https://live.example.com"); ("Host", "live.example.com") ] p);
  check "live, with a port on both: allowed" 200 ~host:`Live
    (request ~headers:[ ("X-OhCamel-Desk", "1"); ("Origin", "http://localhost:8099"); ("Host", "localhost:8099") ] p);
  check "live, a foreign Origin: 403" 403 ~host:`Live
    (request ~headers:[ ("X-OhCamel-Desk", "1"); ("Origin", "https://elsewhere.example"); ("Host", "live.example.com") ] p);
  check "live, cross-site: 403" 403 ~host:`Live
    (request ~headers:[ ("X-OhCamel-Desk", "1"); ("Sec-Fetch-Site", "cross-site") ] p)

let test_the_demo_refuses_orders_and_answers_previews () =
  with_routes ~host:`Demo ~f:(fun server _ ->
      let body = {|{"symbol":"AAPL","side":"buy","qty":10}|} in
      let code, text = dispatched server (request ~body "/api/desk/orders") in
      Alcotest.(check int) "an order: 405" 405 code;
      Alcotest.(check bool) "with a sentence pointing at the preview" true
        (String.is_substring text ~substring:"/api/desk/preview");
      let code, text = dispatched server (request ~body "/api/desk/preview") in
      Alcotest.(check int) "a preview: 200" 200 code;
      let json = Yojson.Safe.from_string text in
      (* 10 x 150 = 1,500: every rule passes, and a book with no limits has
         nothing to breach. *)
      Alcotest.(check bool) "passed" true Yojson.Safe.Util.(to_bool (member "passed" json));
      Alcotest.(check (float 1e-9)) "priced at the mark" 150.0 Yojson.Safe.Util.(to_number (member "decision_price" json)))

let test_a_ticket_that_cannot_be_read_is_a_400_naming_the_field () =
  with_routes ~host:`Demo ~f:(fun server _ ->
      let code, text = dispatched server (request ~body:{|{"symbol":"AAPL","side":"sideways","qty":10}|} "/api/desk/preview") in
      Alcotest.(check int) "400" 400 code;
      Alcotest.(check string) "the field" {|{"error":"side: buy or sell"}|} text;
      let code, _ = dispatched server (request ~body:"not json" "/api/desk/preview") in
      Alcotest.(check int) "not JSON: 400" 400 code;
      let code, _ = dispatched server (request ~meth:`GET "/api/desk/preview") in
      Alcotest.(check int) "a GET: 405" 405 code)

let test_a_reset_must_say_so () =
  with_routes ~host:`Live ~f:(fun server oms ->
      let halt = D.Oms.halt oms in
      D.Halt.halt halt ~why:"a test" ~at:(Time_ns.now ());
      let code, _ = dispatched server (request ~headers:from_this_site ~body:"{}" "/api/desk/kill/reset") in
      Alcotest.(check int) "no confirmation: 400" 400 code;
      Alcotest.(check string) "still halted" "halted" (D.Halt.State.name (D.Halt.state halt));
      let code, _ = dispatched server (request ~body:{|{"confirm":"reset"}|} "/api/desk/kill/reset") in
      Alcotest.(check int) "confirmed, but not from this site: 403" 403 code;
      let code, text = dispatched server (request ~headers:from_this_site ~body:{|{"confirm":"reset"}|} "/api/desk/kill/reset") in
      Alcotest.(check int) "confirmed: 200" 200 code;
      Alcotest.(check string) "clear" "clear" Yojson.Safe.Util.(to_string (member "state" (Yojson.Safe.from_string text))))

let suite =
  ( "desk_routes",
    [
      Alcotest.test_case "who may change the desk" `Quick test_who_may_change_the_desk;
      Alcotest.test_case "the demo refuses orders and answers previews" `Quick
        test_the_demo_refuses_orders_and_answers_previews;
      Alcotest.test_case "a ticket that cannot be read is a 400 naming the field" `Quick
        test_a_ticket_that_cannot_be_read_is_a_400_naming_the_field;
      Alcotest.test_case "a reset must say so" `Quick test_a_reset_must_say_so;
    ] )
```

  Register `Test_desk_routes.suite;` after `Test_oms.suite;`.

- [ ] **Step 2: Run and watch it fail.** Expected: `Unbound module Ohcamel_desk.Desk_routes`.

- [ ] **Step 3: Implement.**
  1. At the end of `desk/oms.ml`:

```ocaml
let venue_name t = Option.value_map t.read ~default:"none" ~f:(fun r -> r.Venue.Read.name)

let fills_json t (rows : Journal.Fill_row.t list) : Yojson.Safe.t =
  `List
    (List.map2_exn rows (costs t rows) ~f:(fun (r : Journal.Fill_row.t) (_, (c : Tca.Costs.t)) ->
         let f = r.Journal.Fill_row.fill in
         let opt = Option.value_map ~default:`Null ~f:jnum in
         `Assoc
           [
             ("execution_id", `String f.Order.Fill.execution_id);
             ("client_order_id", `String (Ids.Client_order_id.to_string r.Journal.Fill_row.client_order_id));
             ("symbol", `String (Symbol.to_string r.Journal.Fill_row.symbol));
             ("side", `String (Order.Side.to_string r.Journal.Fill_row.side));
             ("qty", jnum f.Order.Fill.qty);
             ("price", jnum (Price.to_float f.Order.Fill.price));
             ("at", `String (Desk_time.rfc3339 f.Order.Fill.at));
             ("decision_price", jnum (Price.to_float r.Journal.Fill_row.decision_price));
             ("arrival_bid", opt (Option.map r.Journal.Fill_row.arrival ~f:(fun (b, _) -> Price.to_float b)));
             ("arrival_ask", opt (Option.map r.Journal.Fill_row.arrival ~f:(fun (_, a) -> Price.to_float a)));
             ("shortfall_bps", jnum c.Tca.Costs.shortfall_bps);
             ("delay_bps", opt c.Tca.Costs.delay_bps);
             ("slippage_bps", opt c.Tca.Costs.slippage_bps);
             ("half_spread_bps", opt c.Tca.Costs.half_spread_bps);
             ("versus_model_bps", opt c.Tca.Costs.versus_model_bps);
           ]))

(* Design §7: say whose fills these are. On the paper account a cost measures
   Alpaca's simulator against one venue's quote; on the demo it is the
   simulated half-spread, by construction. *)
let tca_note t =
  match venue_name t with
  | "simulated" ->
      "The demo's venue fills every order half a spread from the mark, so these costs are that half-spread, by construction."
  | _ ->
      "On the paper account every cost here measures Alpaca's fill simulator, against IEX's quote -- one venue's, not the national best."

let tca_json t : Yojson.Safe.t =
  let rows = costs t (Journal.recent_fills t.journal ~limit:500) in
  let opt = Option.value_map ~default:`Null ~f:jnum in
  let summary (s : Tca.Summary.t) =
    `Assoc
      [
        ("count", `Int s.Tca.Summary.count);
        ("mean_shortfall_bps", opt s.Tca.Summary.mean_shortfall_bps);
        ("median_shortfall_bps", opt s.Tca.Summary.median_shortfall_bps);
        ("weighted_shortfall_bps", opt s.Tca.Summary.weighted_shortfall_bps);
        ("mean_versus_model_bps", opt s.Tca.Summary.mean_versus_model_bps);
      ]
  in
  `Assoc
    [
      ("note", `String (tca_note t));
      ("overall", summary (Tca.summarize rows));
      ("by_symbol", `Assoc (List.map (Tca.by_symbol rows) ~f:(fun (s, x) -> (Symbol.to_string s, summary x))));
    ]
```

  2. `desk/desk_routes.ml`:

```ocaml
(* The desk's routes (design §3.11).

   The read routes and the preview answer on both hosts. A preview creates
   nothing, so the public demo lets anyone ask what a trade would do to its
   limits.

   THE ROUTES THAT CHANGE THE DESK ARE PROTECTED BY WHERE A REQUEST CAME
   FROM, NOT BY A SECRET. On the live host Caddy's basic auth decides who
   reaches the engine at all; what is left is a page on another site getting a
   signed-in browser to post here. A cross-site form cannot set a custom
   header, the live host publishes no CORS header, and a browser labels its
   own requests with Sec-Fetch-Site and Origin -- so a request must carry
   X-OhCamel-Desk: 1 and say it came from this site, or it is refused. A token
   handed to the page would be handed to anyone past the password, and would
   stop nothing this does not (§8.1).

   On the demo host each of those routes answers 405, with a sentence saying
   what can be done instead. *)

open Core
open Async
module Server = Ohcamel.Server

type host = [ `Demo | `Live ]

module Protection = struct
  let header = "X-OhCamel-Desk"
  let authority host = function Some port -> sprintf "%s:%d" host port | None -> host

  let check ~(host : host) (r : Server.Request.t) : (unit, Cohttp.Code.status_code * string) Result.t =
    match host with
    | `Demo ->
        Error
          ( `Method_not_allowed,
            "the public demo takes no orders; POST a ticket to /api/desk/preview to see what the rules and the limits \
             would say, which creates nothing" )
    | `Live ->
        let get = Cohttp.Header.get r.Server.Request.headers in
        if not (Poly.equal r.Server.Request.meth `POST) then Error (`Method_not_allowed, "this route takes a POST")
        else if not (Option.equal String.equal (get header) (Some "1")) then
          Error (`Forbidden, "a request that changes the desk must carry X-OhCamel-Desk: 1")
        else
          let same_origin = Option.equal String.equal (get "sec-fetch-site") (Some "same-origin") in
          let origin_is_host =
            match (get "origin", get "host") with
            | Some origin, Some host -> (
                let uri = Uri.of_string origin in
                match Uri.host uri with
                | Some h -> String.equal (String.lowercase (authority h (Uri.port uri))) (String.lowercase host)
                | None -> false)
            | _ -> false
          in
          if same_origin || origin_is_host then Ok ()
          else Error (`Forbidden, "a request that changes the desk must come from this site")
end

let respond_error server status message =
  Server.respond_json ~status server (Yojson.Safe.to_string (`Assoc [ ("error", `String message) ]))

let respond server json = Server.respond_json server (Yojson.Safe.to_string json)
let body_json (r : Server.Request.t) = Option.try_with (fun () -> Yojson.Safe.from_string r.Server.Request.body)

let field (r : Server.Request.t) name =
  match body_json r with Some (`Assoc fields) -> List.Assoc.find fields name ~equal:String.equal | _ -> None

let with_ticket server r ~f =
  match body_json r with
  | None -> respond_error server `Bad_request "the body is not JSON"
  | Some json -> ( match Ticket.of_json json with Ok ticket -> f ticket | Error why -> respond_error server `Bad_request why)

let protected ~host ~f server r =
  match Protection.check ~host r with Ok () -> f server r | Error (status, why) -> respond_error server status why

(* The newest 250 session closes, oldest first: a year of sessions, the
   window phase A5 validates over. Bounded, because this route answers anyone
   on the demo host, which records a session every five minutes -- the reason
   A1's final review (M5) bounded /api/desk's own queries. *)
let sessions_json journal : Yojson.Safe.t =
  let num x = if Float.is_finite x then `Float x else `Null in
  `List
    (List.map (Journal.recent_sessions journal ~limit:250) ~f:(fun s ->
         `Assoc
           [
             ("date", `String (Date.to_string s.Journal.Session.date));
             ("equity_close", num s.Journal.Session.equity_close);
             ("cash_close", num s.Journal.Session.cash_close);
             ("gross_close", num s.Journal.Session.gross_close);
             ("net_close", num s.Journal.Session.net_close);
           ]))

let extensions ~(host : host) ~(oms : Oms.t) : Server.extension list =
  let switch () = Halt.to_json (Oms.halt oms) ~now:(Time_ns.now ()) in
  [
    {
      Server.path = "/api/desk/tca";
      purpose = "what each fill cost in basis points, overall and by symbol";
      handle = (fun server _ -> respond server (Oms.tca_json oms));
    };
    {
      Server.path = "/api/desk/sessions";
      purpose = "the newest 250 session closes, oldest first";
      handle = (fun server _ -> respond server (sessions_json (Oms.journal oms)));
    };
    {
      Server.path = "/api/desk/preview";
      purpose = "POST a ticket: the rules' and the limits' answer to it, creating nothing (both hosts)";
      handle =
        (fun server r ->
          if not (Poly.equal r.Server.Request.meth `POST) then
            respond_error server `Method_not_allowed "POST a ticket to preview it"
          else with_ticket server r ~f:(fun ticket -> respond server (Oms.Preview.to_json (Oms.preview oms ticket))));
    };
    {
      Server.path = "/api/desk/orders";
      purpose = "POST a ticket: the rules, the limits, the journal, the venue (live host only)";
      handle =
        protected ~host ~f:(fun server r ->
            with_ticket server r ~f:(fun ticket ->
                let%bind p, order = Oms.propose oms ~source:"ticket" ticket in
                let refused = Order.State.equal order.Order.state Order.State.Rejected_pre_trade in
                Server.respond_json server
                  ~status:(if refused then `Unprocessable_entity else `OK)
                  (Yojson.Safe.to_string
                     (`Assoc
                       [
                         ( "client_order_id",
                           `String (Ids.Client_order_id.to_string order.Order.request.Order.Request.client_order_id) );
                         ("state", `String (Order.State.to_string order.Order.state));
                         ("reason", Option.value_map order.Order.reason ~default:`Null ~f:(fun s -> `String s));
                         ("preview", Oms.Preview.to_json p);
                       ]))));
    };
    {
      Server.path = "/api/desk/cancel";
      purpose = "POST {client_order_id}: cancel one open order (live host only)";
      handle =
        protected ~host ~f:(fun server r ->
            match Option.bind (field r "client_order_id") ~f:(function `String s -> Ids.Client_order_id.of_string s | _ -> None) with
            | None -> respond_error server `Bad_request "client_order_id: one of this desk's order ids"
            | Some id -> (
                match%bind Oms.cancel oms id with
                | Ok o -> respond server (`Assoc [ ("state", `String (Order.State.to_string o.Order.state)) ])
                | Error why -> respond_error server `Conflict why));
    };
    {
      Server.path = "/api/desk/kill";
      purpose = "POST {why}: halt the desk and cancel every open order; positions are not touched (live host only)";
      handle =
        protected ~host ~f:(fun server r ->
            let why =
              match field r "why" with
              | Some (`String s) when not (String.is_empty (String.strip s)) -> String.prefix (String.strip s) 200
              | _ -> "no reason given"
            in
            let%bind () = Oms.kill oms ~why in
            respond server (switch ()));
    };
    {
      Server.path = "/api/desk/kill/reset";
      purpose = {|POST {"confirm":"reset"}: lift the halt (live host only)|};
      handle =
        protected ~host ~f:(fun server r ->
            match field r "confirm" with
            | Some (`String "reset") ->
                Halt.reset (Oms.halt oms);
                Oms.changed oms;
                respond server (switch ())
            | _ -> respond_error server `Bad_request {|a reset must say so: {"confirm":"reset"}|});
    };
  ]
```

  Notes for the implementer: `Server.respond_json` takes `?status`, then the server, then the body (lib/server.ml:1371), so `~status` may sit anywhere before the body. `Journal.Session`'s fields are `date`, `equity_close`, `cash_close`, `gross_close`, `net_close` and `recorded_at` (desk/journal.ml:183-190).

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 437` (433 + 4).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'
git add desk/desk_routes.ml desk/oms.ml test/test_desk_routes.ml test/test_ohcamel.ml lib/verified.ml
git commit -F - <<'EOF'
desk: the routes -- previews for anyone, orders and the switch only from this site on the live host -- because a page past the password is still a page another site can post through

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 17: What the desk says, and what the switch is wired to

**Files:**
- Modify: `desk/desk.ml`, `lib/graph.ml`, `lib/server.ml`
- Modify: `test/test_desk.ml`, `test/test_server.ml`, `lib/verified.ml`

**Interfaces:**
- Consumes: `Oms` (`can_trade`, `halt`, `accepts_tickets`, `open_count`, `order_json`, `fills_json`, `tca_json`); `Halt`; `Journal.open_orders`/`recent_orders`/`recent_fills`; A1's `Desk.create ~on_first_sync` and its `synced` field, and Task 3's read-number fields (desk/desk.ml), beside which the new field goes.
- Produces:
  - `Desk.set_oms : t -> Oms.t -> unit`. The frame's desk object (spec §3.11) has sixteen keys, in order: `status`, `reason`, `venue`, `trading`, `kill_switch` (`"clear"`, `"tripped"`, `"halted"`, or null with no manager), `tickets` (`"accepted"`, `"preview only"`, `"none"`), `journal`, `version`, `equity`, `cash`, `session_pnl`, `open_orders`, `unmanaged`, `sessions`, `last_sync`, `last_error`. `/api/desk` adds, after A1's keys, `switch` (`Halt.to_json`, or null), `orders` (`{open, recent}` of `Oms.order_json` rows), `fills` (the twenty most recent, costed), `tca` (`Oms.tca_json`, or null).
  - `Graph.topology ?alerts ?kill_switch_wired_to t` and `Server.create ?kill_switch_wired_to`: the `kill_switch` reader's `wired_to` is the given name (§3.8: `"desk.submit"` whenever a desk is attached), null otherwise.

- [ ] **Step 1: Write the failing tests.**
  1. In `test/test_desk.ml`, change `test_the_frame's_desk_object_has_exactly_these_keys` to expect sixteen:

```ocaml
let test_the_frame's_desk_object_has_exactly_these_keys () =
  with_desk ~f:(fun desk _ _ ->
      Alcotest.(check (list string)) "sixteen, in order"
        [
          "status"; "reason"; "venue"; "trading"; "kill_switch"; "tickets"; "journal"; "version"; "equity"; "cash";
          "session_pnl"; "open_orders"; "unmanaged"; "sessions"; "last_sync"; "last_error";
        ]
        (Yojson.Safe.Util.keys (Desk.summary_json desk)))
    ()
```

  (and its case name in `suite` to "the frame's desk object has exactly these sixteen keys"), then add above `let suite`:

```ocaml
(* With an order manager attached the desk says whether it can trade, what the
   switch reads and whether this host takes tickets; /api/desk lists the
   journal's orders. The order is journaled directly, as pending_submit --
   the manager's open set does not hold it, which is why the frame's count
   stays 0 while /api/desk's list, which reads the journal, holds one. *)
let test_with_a_manager_the_desk_says_what_it_can_do () =
  with_desk ~f:(fun desk graph journal ->
      let venue =
        Ohcamel_desk.Sim_venue.create ~opened_at:at ~marks:(fun _ -> None) ~now:(fun () -> at)
          ~half_spread_bps:(fun _ -> 5.0) ~cash:Notional.zero ~positions:[] ()
      in
      let oms =
        Ohcamel_desk.Oms.create ~graph ~journal
          ~spec:{ Config.Book.Desk_spec.default with Config.Book.Desk_spec.trading = Config.Book.Desk_spec.Enabled }
          ~read:(Some (Ohcamel_desk.Sim_venue.read venue))
          ~trade:(Ok (Ohcamel_desk.Sim_venue.trade ~auto:false venue))
          ~halt:(Ohcamel_desk.Halt.create Ohcamel_desk.Halt.Source.none)
          ~accepts_tickets:false ~adv:(Ohcamel_desk.Oms.Adv.Fixed 1_000_000.0) ~now:(fun () -> at)
          ~rng:(Random.State.make [| 5 |]) ~on_change:ignore ~on_event:ignore ~after_fill:ignore
          ~book_is_current:(fun () -> true) ()
      in
      Desk.set_oms desk oms;
      let s = Desk.summary_json desk in
      Alcotest.(check bool) "trading" true (Yojson.Safe.Util.to_bool (field s "trading"));
      Alcotest.(check string) "the switch" "clear" (Yojson.Safe.Util.to_string (field s "kill_switch"));
      Alcotest.(check string) "tickets" "preview only" (Yojson.Safe.Util.to_string (field s "tickets"));
      Ohcamel_desk.Halt.halt (Ohcamel_desk.Oms.halt oms) ~why:"a test" ~at;
      Alcotest.(check string) "halted" "halted" (Yojson.Safe.Util.to_string (field (Desk.summary_json desk) "kill_switch"));
      let request =
        {
          Ohcamel_desk.Order.Request.client_order_id =
            Option.value_exn (Ohcamel_desk.Ids.Client_order_id.of_string "ohc-01M2B0CWJ0ZZZZZZZZZZZZZZZ1");
          symbol = aapl;
          side = Ohcamel_desk.Order.Side.Buy;
          qty = 10;
          kind = Ohcamel_desk.Order.Kind.Market;
        }
      in
      Journal.insert_order journal (Ohcamel_desk.Order.create request) ~source:"manual"
        ~decision_price:(Price.of_float 150.0) ~arrival:None ~verdict:`Null ~at;
      let b = Desk.body_json desk in
      Alcotest.(check int) "the frame's count is the manager's" 0
        (Yojson.Safe.Util.to_int (field (Desk.summary_json desk) "open_orders"));
      Alcotest.(check int) "/api/desk lists the journal's open order" 1
        (List.length (Yojson.Safe.Util.to_list (field (field b "orders") "open")));
      Alcotest.(check string) "and the switch, whole" "halted"
        (Yojson.Safe.Util.to_string (field (field b "switch") "state")))
    ()
```

  Register it in `suite`. (`with_desk ?venue ?on_first_sync ~f ()` calls `f desk graph journal`; `at`, `aapl`, `field` and `Config` are defined above it in test/test_desk.ml.)
  2. In `test/test_server.ml`, above its `let suite`:

```ocaml
(* §3.8 of the desk design: with a desk attached, the kill switch is wired to
   the desk's submit path, and the topology says so. Every other reader stays
   wired to nothing. *)
let test_with_a_desk_the_switch_is_wired_to_desk_submit () =
  with_graph
    ~f:(fun graph ->
      let str j k = match field_exn j k with `String s -> s | _ -> "<not a string>" in
      let server = Server.create ~kill_switch_wired_to:"desk.submit" ~mode:`Demo ~graph ~factor:"SYNTHETIC" () in
      let _, body = dispatched server "/api/graph" in
      match field_exn (Yojson.Safe.from_string body) "outside" with
      | `List outs ->
          List.iter outs ~f:(fun o ->
              let expected = if String.equal (str o "name") "kill_switch" then `String "desk.submit" else `Null in
              Alcotest.(check string)
                (str o "name" ^ " is wired to")
                (Yojson.Safe.to_string expected)
                (Yojson.Safe.to_string (field_exn o "wired_to")))
      | _ -> Alcotest.fail "no outside list")
    ()
```

  Register it in `suite`. (`with_graph`, `dispatched` and `field_exn` exist in this file; `str` is local to the test, as it is in the two tests that already use one.)

- [ ] **Step 2: Run and watch it fail.** Expected: `Unbound value Desk.set_oms` or `This argument cannot be applied with label ~kill_switch_wired_to`.

- [ ] **Step 3: Implement.**
  1. `lib/graph.ml`: `let topology ?(alerts = false) ?kill_switch_wired_to (t : t) : Topology.t =`; in the `kill_switch` reader, `wired_to = kill_switch_wired_to;`; and in the comment above `let outside` (lib/graph.ml:2200-2202, where it wraps across three lines), replace `the kill switch reads the tracker and is wired to nothing -- [wired_to = None] is the invariant on the wire.` with `the kill switch reads the tracker and is wired to what the caller names: nothing, unless a desk that obeys it is attached -- the kernel still acts on nothing itself.`
  2. `lib/server.ml`: `create` gains `?(kill_switch_wired_to : string option)` beside `?alerts`, and the memoised topology becomes `Graph.topology ~alerts:(Option.is_some alerts) ?kill_switch_wired_to graph`.
  3. `desk/desk.ml`: add `mutable oms : Oms.t option;` to `t` after Task 3's `fill_read` (initialised `oms = None` in `create`, after ``fill_read = `Idle``), then:

```ocaml
(* The order manager is made after the desk -- its fills re-read the account
   through [sync] -- and handed back here, so the frame can say what the desk
   can do. *)
let set_oms t oms = t.oms <- Some oms
```

  In `summary_fields`, replace `("trading", `Bool false);` with:

```ocaml
    ("trading", `Bool (match t.oms with Some o -> Oms.can_trade o | None -> false));
    ("kill_switch", match t.oms with Some o -> `String (Halt.State.name (Halt.state (Oms.halt o))) | None -> `Null);
    ( "tickets",
      `String (match t.oms with Some o when Oms.accepts_tickets o -> "accepted" | Some _ -> "preview only" | None -> "none") );
```

  and directly after the `session_pnl` pair add `("open_orders", `Int (match t.oms with Some o -> Oms.open_count o | None -> 0));`. In `body_json`, append after `last_forecasts`:

```ocaml
        ("switch", match t.oms with Some o -> Halt.to_json (Oms.halt o) ~now:(Time_ns.now ()) | None -> `Null);
        ( "orders",
          `Assoc
            [
              ("open", `List (List.map (Journal.open_orders t.journal) ~f:Oms.order_json));
              ("recent", `List (List.map (Journal.recent_orders t.journal ~limit:20) ~f:Oms.order_json));
            ] );
        ( "fills",
          match t.oms with Some o -> Oms.fills_json o (Journal.recent_fills t.journal ~limit:20) | None -> `List [] );
        ("tca", match t.oms with Some o -> Oms.tca_json o | None -> `Null);
```

  Update A1's `extensions` purpose string for `/api/desk` to `"the desk: its venue, the account, positions held and unmanaged, orders, fills and their costs, the switch, recorded sessions"`.

- [ ] **Step 4: Run and watch it pass.** Set `let tests = 439` (437 + 2).

- [ ] **Step 5: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'
git add desk/desk.ml lib/graph.ml lib/server.ml test/test_desk.ml test/test_server.ml lib/verified.ml
git commit -F - <<'EOF'
desk: the frame says whether the desk can trade, what the switch reads and who may send a ticket, and the topology wires the switch to desk.submit, because Figure 1 drew a switch wired to nothing and that is no longer true

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 18: Both hosts attach the order manager

**Files:**
- Modify: `bin/main.ml`, `deploy/smoke.sh`

**Interfaces:**
- Consumes: everything above; A1's `run_live` and `run_demo` desk blocks as they stand at `cf79764` (bin/main.ml:1225-1320 and :1485-1500): the journal line, `Desk.create ... ~on_first_sync` (the restore on the live host, a no-op on the demo), the first sync bounded at 20 s, and `Session_close.run_forever ~book_is_current`; Task 3's `Desk.after_fill`; A1's `Desk.book_is_current desk ~now ~within`; run_live's `let sync_every = Time_ns.Span.of_min 1.0 in` (bin/main.ml:1300, inside the `Reads read` arm), which moves above the order manager; run_demo's awaited first sync (bin/main.ml:1499) and its 15 s syncs (:1596-1597).
- Produces: `ohcamel serve` attaches an order manager to the desk -- trading when the book enables it and the paper key pair loads, previewing otherwise -- reconciles before serving (for at most 20 s; a reconciliation still running then goes on in the manager's queue, and orders wait behind it), consumes Alpaca's trade updates, refreshes the session and twenty-day volume, serves the desk's routes, and wires the switch to `desk.submit`. `ohcamel demo` does the same on the simulated venue with tickets preview-only, a switch that resets itself 90 s after its limit clears, and its own trader proposing an order every 45 s. On both hosts the `trading` rule refuses an order while the desk's last applied read of the account is older than two sync intervals: two minutes on the live host, 30 s on the demo. The six credential-free modes print byte-for-byte what they printed before.

- [ ] **Step 1: Capture the six modes before touching `bin/main.ml`.**

```bash
mkdir -p /tmp/a2-gate && eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | tail -3 && for m in synthetic stress backtest backtest-crisis options garch; do ./_build/default/bin/main.exe $m > /tmp/a2-gate/$m.before 2>&1; done; ls -la /tmp/a2-gate
```

- [ ] **Step 2: `run_live`.**
  1. Where A1 matches `Ohcamel_desk.Alpaca_paper.Credentials.load ~data:config.Config.credentials` to build `venue`, bind it first -- `let trading_credentials = Ohcamel_desk.Alpaca_paper.Credentials.load ~data:config.Config.credentials in` -- and match on `trading_credentials`.
  2. Directly after A1's first-sync block (`let%bind () = match%map Clock_ns.with_timeout (Time_ns.Span.of_sec 20.0) (Ohcamel_desk.Desk.sync desk) with ...`, bin/main.ml:1284-1297), and before A1's `(match venue with | Ohcamel_desk.Desk.Reads read -> ...` block (the minute sync and `Session_close.run_forever`), add:

```ocaml
      (* The minute sync's interval, bound here rather than in the venue match
         below, because the order manager measures the book against it as the
         session close does: an order is refused while the last applied read
         of the account is older than two intervals. *)
      let sync_every = Time_ns.Span.of_min 1.0 in
      (* The order manager (design §3.6-§3.8). Trading needs a book that
         enables it, a paper key pair the paper host accepts, and a graph that
         holds the account's book; without the first two, the manager still
         previews, and the page says in a sentence why nothing can be sent.
         The trade-update stream reconciles every time it reconnects: an update
         sent while it was down is an update it will never deliver. A fill the
         manager applies goes to Desk.after_fill, which refuses an account read
         already out when the fill landed and starts one that sees it. *)
      let module Desk_spec = Config.Book.Desk_spec in
      let halt =
        Ohcamel_desk.Halt.create
          (match alerts with Some a -> Ohcamel_desk.Halt.Source.of_alerts a | None -> Ohcamel_desk.Halt.Source.none)
      in
      let reconcile_on_connect = ref (fun () -> Deferred.unit) in
      let trade =
        match (book.Config.Book.desk.Desk_spec.trading, trading_credentials) with
        | Desk_spec.Disabled, _ -> Error "the book does not enable trading; add (desk ((trading enabled))) to book.sexp"
        | Desk_spec.Enabled, Error e -> Error (Error.to_string_hum e)
        | Desk_spec.Enabled, Ok credentials ->
            Ok
              (Ohcamel_desk.Alpaca_trade.trade ~credentials
                 ~on_connected:(fun () -> !reconcile_on_connect ())
                 ~on_event:(fun e -> live_line ("trade     " ^ e)))
      in
      let oms =
        Ohcamel_desk.Oms.create ~graph ~journal ~spec:book.Config.Book.desk
          ~read:(match venue with Ohcamel_desk.Desk.Reads r -> Some r | Ohcamel_desk.Desk.Unavailable _ -> None)
          ~trade ~halt ~accepts_tickets:true ~adv:Ohcamel_desk.Oms.Adv.From_venue ~now:Time_ns.now
          ~rng:(Random.State.make_self_init ()) ~on_change:(fun () -> !notify ()) ~on_event:live_line
          ~after_fill:(fun () -> Ohcamel_desk.Desk.after_fill desk)
          ~book_is_current:(fun () ->
            Ohcamel_desk.Desk.book_is_current desk ~now:(Time_ns.now ())
              ~within:(Time_ns.Span.scale sync_every 2.0))
          ()
      in
      reconcile_on_connect := (fun () -> Ohcamel_desk.Oms.reconcile oms);
      Ohcamel_desk.Desk.set_oms desk oms;
      Option.iter alerts ~f:(Ohcamel_desk.Oms.watch_alerts oms);
      live_line
        (match trade with
        | Ok _ -> "desk      trading ON -- Alpaca paper; every order passes the rules and the limits before the venue"
        | Error why -> "desk      trading off -- " ^ why);
      (* Awaited, so the first blotter is the venue's; and bounded, as the
         first sync is, because a paper host that hangs costs ten seconds a
         lookup and the listener, with the healthcheck, is not up until this
         returns. A reconciliation past the bound goes on in the order
         manager's queue: proposals are enqueued behind it in the same
         sequencer, so none is sent before it finishes. *)
      let%bind () =
        match%map Clock_ns.with_timeout (Time_ns.Span.of_sec 20.0) (Ohcamel_desk.Oms.reconcile oms) with
        | `Result () -> ()
        | `Timeout ->
            live_line
              "desk      reconciliation did not finish in 20 s; it goes on in the order manager's queue, and orders wait behind it"
      in
      don't_wait_for (Ohcamel_desk.Oms.run oms);
      don't_wait_for (Ohcamel_desk.Oms.refresh_forever oms ~every:(Time_ns.Span.of_min 1.0));
```

  3. In A1's `(match venue with | Ohcamel_desk.Desk.Reads read -> ...` arm, delete `let sync_every = Time_ns.Span.of_min 1.0 in` (bin/main.ml:1300). The binding above the order manager now serves the minute sync, `Session_close.run_forever`'s `book_is_current` and the manager's; a second binding would shadow it with the same value and leave two places to change.
  4. In `http`'s `Server.create`, change A1's `~extensions:(Ohcamel_desk.Desk.extensions desk)` to `~extensions:(Ohcamel_desk.Desk.extensions desk @ Ohcamel_desk.Desk_routes.extensions ~host:`Live ~oms)` and add `~kill_switch_wired_to:"desk.submit"`.

- [ ] **Step 3: `run_demo`.**
  1. Directly above A1's `let journal = Or_error.ok_exn (Ohcamel_desk.Journal.open_ ~path:":memory:") in`, add `let demo_desk_spec = { Config.Book.Desk_spec.default with Config.Book.Desk_spec.trading = Config.Book.Desk_spec.Enabled } in`, and pass `~spec:demo_desk_spec` to A1's `Ohcamel_desk.Desk.create` in place of `Ohcamel.Config.Book.Desk_spec.default` (its `~on_first_sync:(fun () -> ())` and the comment above it stay). Change A1's `Ohcamel_desk.Sim_venue.read venue` there to use the same `venue` value (unchanged) -- the trading half below is made from it too.
  2. After `let%bind alerts = ...` and before `let quiet, _, _, _ = List.last_exn book in`, add:

```ocaml
  (* The order manager, on the simulated venue. Trading is on, because the
     venue is in this process; tickets are not, because this host is public --
     the page previews, and the demo's own trader below places the orders.
     The switch resets itself ninety seconds after nvda-cap clears, which the
     page says happens only here. *)
  let halt =
    Ohcamel_desk.Halt.create ~auto_reset_after:(Time_ns.Span.of_sec 90.0)
      (match alerts with Some a -> Ohcamel_desk.Halt.Source.of_alerts a | None -> Ohcamel_desk.Halt.Source.none)
  in
  let oms =
    Ohcamel_desk.Oms.create ~graph ~journal ~spec:demo_desk_spec
      ~read:(Some (Ohcamel_desk.Sim_venue.read venue))
      ~trade:(Ok (Ohcamel_desk.Sim_venue.trade venue))
      ~halt ~accepts_tickets:false ~adv:(Ohcamel_desk.Oms.Adv.Fixed 2_000_000.0) ~now:Time_ns.now
      ~rng:(Random.State.make_self_init ()) ~on_change:(fun () -> !notify ())
      ~on_event:(fun e -> printf "  %s\n%!" e)
      ~after_fill:(fun () -> Ohcamel_desk.Desk.after_fill desk)
      (* Two of the demo's fifteen-second syncs. The first sync was awaited
         above, so the trader's first proposal finds a current book. *)
      ~book_is_current:(fun () ->
        Ohcamel_desk.Desk.book_is_current desk ~now:(Time_ns.now ()) ~within:(Time_ns.Span.of_sec 30.0))
      ()
  in
  Ohcamel_desk.Desk.set_oms desk oms;
  Option.iter alerts ~f:(Ohcamel_desk.Oms.watch_alerts oms);
  don't_wait_for (Ohcamel_desk.Oms.run oms);
  let%bind () = Ohcamel_desk.Oms.refresh oms ~bars:false in
  don't_wait_for (Ohcamel_desk.Oms.refresh_forever oms ~every:(Time_ns.Span.of_min 1.0));
```

  3. In the demo's `Server.create`, change A1's `~extensions:(Ohcamel_desk.Desk.extensions desk)` to `~extensions:(Ohcamel_desk.Desk.extensions desk @ Ohcamel_desk.Desk_routes.extensions ~host:`Demo ~oms)` and add `~kill_switch_wired_to:"desk.submit"`.
  4. Replace the alerts banner's second line -- `it sets a flag and nothing else. Nothing here places orders.` -- with `a trip refuses new orders and cancels open ones, and resets 90 s after the limit clears.`, and A1's desk banner line with `printf "  desk        simulated venue; journal in memory; a session every 5 min; its own trader every 45 s; tickets preview only\n";`.
  5. After the staleness-clock timer, before `Deferred.never ()`, add:

```ocaml
  (* The demo's switch resets itself; this is the clock it resets by. *)
  Clock_ns.every' (Time_ns.Span.of_sec 1.0) (fun () ->
      if Ohcamel_desk.Halt.tick halt ~now:(Time_ns.now ()) then (
        printf "  desk      the switch reset itself: its limit has been clear for 90 s (the demo only)\n%!";
        !notify ());
      Deferred.unit);
  (* The demo's own trader. Nobody can place an order on the public host, so
     without this the blotter would stay empty. Every forty-five seconds it
     proposes a small market order through exactly the rules and the limits a
     ticket passes. Every fourth is a buy of ten NVDA, whose cap sits 200
     dollars above its exposure, so the page regularly shows the limits
     refusing a trade and naming the limit. *)
  let proposals = ref 0 in
  let nvda = Symbol.of_string "NVDA" in
  Clock_ns.every' (Time_ns.Span.of_sec 45.0) (fun () ->
      incr proposals;
      let symbol, side, qty =
        if !proposals % 4 = 0 && List.exists tickable ~f:(fun (s, _, _, _) -> Symbol.equal s nvda) then
          (nvda, Ohcamel_desk.Order.Side.Buy, 10)
        else
          let symbol, _, _, _ = List.nth_exn tickable (Random.State.int rng (List.length tickable)) in
          ( symbol,
            (if Random.State.bool rng then Ohcamel_desk.Order.Side.Buy else Ohcamel_desk.Order.Side.Sell),
            1 + Random.State.int rng 20 )
      in
      Deferred.ignore_m
        (Ohcamel_desk.Oms.propose oms ~source:"demo" { Ohcamel_desk.Ticket.symbol; side; qty; kind = Ohcamel_desk.Order.Kind.Market }));
```

- [ ] **Step 4: The six modes did not move.**

```bash
eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | tail -3 && for m in synthetic stress backtest backtest-crisis options garch; do ./_build/default/bin/main.exe $m > /tmp/a2-gate/$m.after 2>&1; if cmp -s /tmp/a2-gate/$m.before /tmp/a2-gate/$m.after; then echo "GATE ok $m"; else echo "GATE FAIL $m"; diff /tmp/a2-gate/$m.before /tmp/a2-gate/$m.after | head -20; fi; done
```

  Expected: six `GATE ok` lines (a garch timing line is the only permitted difference; say so if it occurs).

- [ ] **Step 5: The smoke suite.** In `deploy/smoke.sh`:
  1. `EXPECTED_ROUTES` gains, after `/api/desk`: ` /api/desk/tca /api/desk/sessions /api/desk/preview /api/desk/orders /api/desk/cancel /api/desk/kill /api/desk/kill/reset`.
  2. Directly after A1's `4a'. The desk` block, add:

```bash
# ---------------------------------------------------------------------------
# 4a''. The desk's routes, as each host allows them
#
# The preview answers on the demo and creates nothing; an order is refused
# there with a 405. On the live host this suite has no password and sends no
# order: section 6 asserts the host refuses anonymous callers on the orders
# route with the rest.
# ---------------------------------------------------------------------------
if command -v python3 >/dev/null 2>&1 && [ "${ops_mode:-}" = "demo" ]; then
	ticket='{"symbol":"AAPL","side":"buy","qty":1}'
	code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 -X POST -H 'Content-Type: application/json' -d "$ticket" "$BASE/api/desk/orders" 2>/dev/null)
	[ "$code" = "405" ] && ok "POST /api/desk/orders        405 on the demo host" \
		|| no "POST /api/desk/orders        $code, expected 405" "the public demo did not refuse an order"
	preview=$(curl -sS --max-time 15 -X POST -H 'Content-Type: application/json' -d "$ticket" "$BASE/api/desk/preview" 2>/dev/null | python3 -c '
import json, sys
try:
    p = json.load(sys.stdin)
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
if not isinstance(p.get("passed"), bool) or not isinstance(p.get("rules"), list) or "gate" not in p:
    print("SHAPE keys=%r" % sorted(p.keys())); raise SystemExit
print("OK %s %d" % ("passes" if p["passed"] else "refused", len(p.get("reasons") or [])))
' 2>/dev/null)
	case "$preview" in
	OK*) read -r _ pverdict preasons <<<"$preview"; ok "POST /api/desk/preview       $pverdict, $preasons reasons, nothing created" ;;
	*)   no "POST /api/desk/preview       malformed" "${preview:-no response}" ;;
	esac
elif [ "${ops_mode:-}" = "live" ]; then
	meh "POST /api/desk/*             the live host's desk routes sit behind its password; section 6 covers them"
else
	meh "POST /api/desk/*             python3 unavailable or the mode unknown; the desk's routes not checked"
fi
```

  3. In section 6, the live loop becomes `for path in / /ops /api/ops /api/snapshot /api/health /api/desk /api/desk/orders; do`, and its comment's `Six paths, not one.` becomes `Seven paths, not one.`

- [ ] **Step 6: Run it.** The demo, on a port nobody uses:

```bash
lsof -nP -iTCP:8099 -sTCP:LISTEN; (./_build/default/bin/main.exe demo 8099 > /tmp/a2-demo.log 2>&1 &) ; curl -sS --retry 30 --retry-connrefused --retry-delay 1 -m 40 -o /dev/null http://localhost:8099/api/desk; curl -sS -m 5 -X POST -H 'Content-Type: application/json' -d '{"symbol":"AAPL","side":"buy","qty":1}' -w ' %{http_code}\n' http://localhost:8099/api/desk/orders; curl -sS -m 5 -X POST -H 'Content-Type: application/json' -d '{"symbol":"NVDA","side":"buy","qty":10}' http://localhost:8099/api/desk/preview | python3 -m json.tool | head -30; curl -sS -m 5 http://localhost:8099/api/graph | python3 -c 'import json,sys; print([o for o in json.load(sys.stdin)["outside"] if o["name"]=="kill_switch"][0]["wired_to"])'; sleep 100; curl -sS -m 5 http://localhost:8099/api/desk | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["trading"], d["kill_switch"], d["tickets"], len(d["orders"]["recent"]), "orders", len(d["fills"]), "fills")'; ./deploy/smoke.sh http://localhost:8099 2>&1 | tail -15; pkill -f "main.exe demo 8099"; lsof -nP -iTCP:8099 -sTCP:LISTEN
```

  Expected: the order POST answers 405 with a sentence; the NVDA preview is refused naming `nvda-cap` (or passes, if NVDA has drifted down since start -- say which); `desk.submit`; after 100 s, `True`, a switch state, `preview only`, at least two orders; the smoke suite's desk lines pass. (`smoke.sh` takes the base URL as its first argument; without `--expect-sha` its build check is skipped.) Nothing left listening.

- [ ] **Step 7: Format and commit.**

```bash
make fmt; make fmt; eval $(opam env --switch=$PWD --set-switch) && dune build @fmt && make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'
git add bin/main.ml deploy/smoke.sh
git commit -F - <<'EOF'
desk: both hosts attach the order manager -- Alpaca paper on the live host when the book enables it, the simulated venue and its own trader on the demo -- because an order manager nothing calls has placed no orders

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 19: The page: the switch, the ticket, the blotter, the fills

**Files:**
- Modify: `web/index.html`, `web/dashboard.js`, `web/page.css`

**Interfaces:**
- Consumes: the frame's `desk` object (Task 17's sixteen keys); `/api/desk`'s `switch`, `orders`, `fills`, `tca`, `unmanaged_positions`, `tickets`; `POST /api/desk/preview`, `/api/desk/orders`, `/api/desk/kill`, `/api/desk/kill/reset`; `window.OhCamelFormat` (`money`, `el`).
- Produces: inside A1's `#desksec`, a switch line (with halt and reset buttons where tickets are accepted), a ticket (preview everywhere, place only where tickets are accepted), a blotter of open and recent orders with their reasons, and a table of recent fills with their costs and the note saying whose fills they are.

No test count change.

- [ ] **Step 1: The markup.** In `web/index.html`, inside `#desksec`, directly after A1's `<div id="deskunmanaged" ...></div>`:

```html
    <div id="deskswitch" class="desk-switch" hidden></div>
    <form id="ticket" class="ticket" autocomplete="off" hidden>
      <span class="lbl">ticket <i id="ticketnote">— the rules, then the limits</i></span>
      <select id="tsym" aria-label="symbol"></select>
      <select id="tside" aria-label="side"><option value="buy">buy</option><option value="sell">sell</option></select>
      <input id="tqty" type="number" min="1" step="1" value="10" aria-label="shares">
      <select id="ttype" aria-label="order type"><option value="market">market</option><option value="limit">limit</option></select>
      <input id="tlimit" type="number" min="0" step="0.01" placeholder="limit" aria-label="limit price" disabled>
      <button type="button" id="tpreview">preview</button>
      <button type="button" id="tplace" hidden>place</button>
      <div id="ticketout" class="ticket-out" aria-live="polite"></div>
    </form>
    <table id="blotter" class="blotter"></table>
    <table id="fills" class="fills"></table>
    <p id="tcanote" class="desk-note"></p>
```

- [ ] **Step 2: The script.** In `web/dashboard.js`:
  1. Replace A1's comment above `renderDesk` (web/dashboard.js:581-585, from `// The frame carries thirteen fields` to `so a list keyed on it stayed the first one.`) with:

```js
  // The frame carries sixteen fields and nothing a table needs a second
  // request for, except what /api/desk lists: the names the venue holds
  // outside the book, the switch whole, the blotter and the fills. Those are
  // fetched when something they show has moved -- the key built at the end of
  // renderDesk -- and not on every frame.
```

  In A1's `renderDesk`, add three rows to `rows` directly after the `session P&L` row:

```js
      ["trading", d.trading ? "on" : "off"],
      ["kill switch", d.kill_switch === null ? "—" : d.kill_switch],
      ["open orders", String(d.open_orders)],
```

  2. Replace A1's `var deskHeldKey = null, deskHeldWant = null, deskHeldInFlight = false;` (web/dashboard.js:586) with `var deskKey = null, deskWant = null, deskInFlight = false;`. Then replace the end of `renderDesk` -- from `var u = document.getElementById("deskunmanaged");` to the function's closing brace (:616-639) -- with the following. A1's reviewed behaviour stays: one request at a time; the key taken only after a fetch has drawn; an answer a newer frame overtook dropped; the list's text set before it is shown. The key only grows.

```js
    renderTicketControls(d, s);
    // /api/desk is asked again when anything it draws has moved: a sync (the
    // unmanaged count, last_sync), a journal write (the version: an order, a
    // fill, a session), the switch, or the open-order count. The last two live
    // in memory, not the journal, so the version alone would miss a trip; and
    // a sync writes nothing to the journal, so the version alone would miss
    // that too (A1's reason for its key). One request at a time; the key is
    // taken only once a fetch has drawn, so a failed one is asked again by a
    // later frame; an answer a newer frame overtook is dropped.
    deskWant = [d.unmanaged, d.last_sync, d.version, d.kill_switch, d.open_orders].join("|");
    if (deskWant === deskKey || deskInFlight) return;
    var asked = deskWant;
    deskInFlight = true;
    fetch("/api/desk").then(function (r) { return r.json(); }).then(function (b) {
      if (asked !== deskWant || !b) return;
      renderDeskBody(b);
      deskKey = asked;
    }).catch(function () { /* the frame's fields above still stand */ })
      .then(function () { deskInFlight = false; });
  }

  function renderDeskBody(b) {
    var u = document.getElementById("deskunmanaged");
    var held = b.unmanaged_positions;
    if (!Array.isArray(held) || held.length === 0) { u.hidden = true; u.textContent = ""; }
    else {
      // The text first, then shown, as A1's page did.
      u.textContent = "held by the venue, not in the book: " + held.map(function (p) {
        return p.symbol + " " + p.qty;
      }).join(", ");
      u.hidden = false;
    }
    renderSwitch(b);
    renderBlotter(b.orders || { open: [], recent: [] });
    renderFills(b.fills || [], b.tca);
  }

  // The header a cross-site form cannot set. The browser adds Sec-Fetch-Site
  // and Origin itself, and the live host refuses a request without them.
  function deskPost(path, body, protectedRoute) {
    var headers = { "Content-Type": "application/json" };
    if (protectedRoute) headers["X-OhCamel-Desk"] = "1";
    return fetch(path, { method: "POST", headers: headers, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); });
  }

  function renderSwitch(b) {
    var box = document.getElementById("deskswitch"), sw = b.switch, F = window.OhCamelFormat;
    if (!box) return;
    if (!sw) { box.hidden = true; return; }
    box.hidden = false;
    box.textContent = "";
    box.className = "desk-switch " + sw.state;
    var line = sw.state === "clear" ? "kill switch clear: orders may be sent"
      : sw.state === "tripped" ? "kill switch TRIPPED by " + sw.limit + ": new orders refused, open orders cancelled, positions untouched"
      : "desk HALTED by hand (" + sw.why + "): new orders refused, open orders cancelled, positions untouched";
    box.appendChild(F.el("span", "desk-switch-line", line));
    if (sw.state === "tripped" && sw.auto_reset_s !== null)
      box.appendChild(F.el("span", "desk-switch-note", sw.resets_in_s !== null
        ? "· resets itself in " + Math.ceil(sw.resets_in_s) + " s — the demo only"
        : "· resets itself " + sw.auto_reset_s + " s after " + sw.limit + " clears — the demo only"));
    if (b.tickets !== "accepted") return;
    var btn = document.createElement("button");
    btn.type = "button";
    if (sw.state === "clear") {
      btn.textContent = "halt the desk";
      btn.onclick = function () {
        var why = window.prompt("Why halt the desk? Every open order will be cancelled; positions stay.");
        if (why === null) return;
        deskPost("/api/desk/kill", { why: why }, true).then(function () { deskKey = null; });
      };
    } else {
      btn.textContent = "reset";
      btn.onclick = function () {
        if (!window.confirm("Reset the kill switch? New orders will be allowed again.")) return;
        deskPost("/api/desk/kill/reset", { confirm: "reset" }, true).then(function () { deskKey = null; });
      };
    }
    box.appendChild(btn);
  }

  var ticketReady = false;
  function renderTicketControls(d, s) {
    var form = document.getElementById("ticket");
    if (!form) return;
    form.hidden = d.status !== "enabled" || d.tickets === "none";
    if (form.hidden) return;
    document.getElementById("tplace").hidden = d.tickets !== "accepted";
    document.getElementById("ticketnote").textContent = d.tickets === "accepted"
      ? "— the rules, then the limits, then the venue"
      : "— preview only on this host: the rules and the limits answer, and nothing is sent";
    if (ticketReady) return;
    ticketReady = true;
    var sym = document.getElementById("tsym");
    s.positions.forEach(function (p) {
      var o = document.createElement("option");
      o.value = p.symbol; o.textContent = p.symbol;
      sym.appendChild(o);
    });
    var type = document.getElementById("ttype"), limit = document.getElementById("tlimit");
    type.onchange = function () { limit.disabled = type.value !== "limit"; };
    document.getElementById("tpreview").onclick = function () { sendTicket(false); };
    document.getElementById("tplace").onclick = function () {
      if (!window.confirm("Send this order to the venue? It passes the rules and the limits first.")) return;
      sendTicket(true);
    };
  }

  function ticketBody() {
    var body = {
      symbol: document.getElementById("tsym").value,
      side: document.getElementById("tside").value,
      qty: Number(document.getElementById("tqty").value),
      type: document.getElementById("ttype").value
    };
    if (body.type === "limit") body.limit_price = Number(document.getElementById("tlimit").value);
    return body;
  }

  function sendTicket(placing) {
    var out = document.getElementById("ticketout"), F = window.OhCamelFormat;
    out.textContent = placing ? "sending…" : "asking…";
    deskPost(placing ? "/api/desk/orders" : "/api/desk/preview", ticketBody(), placing).then(function (res) {
      var b = res.body, p = placing ? b.preview : b;
      out.textContent = "";
      if (b.error) { out.appendChild(F.el("div", "ticket-bad", b.error)); return; }
      var head = placing
        ? (b.state === "rejected_pre_trade" ? "refused before the venue" : "sent: " + b.state.replace(/_/g, " "))
        : (p.passed ? "would pass the rules and the limits" : "would be refused");
      out.appendChild(F.el("div", p.passed ? "ticket-ok" : "ticket-bad", head));
      (p.reasons || []).forEach(function (r) { out.appendChild(F.el("div", "ticket-reason", r)); });
      if (p.gate) {
        out.appendChild(F.el("div", "ticket-gate",
          "gross " + F.money(p.gate.gross_before) + " → " + F.money(p.gate.gross_after) +
          " · equity " + F.money(p.gate.equity_before) + " → " + F.money(p.gate.equity_after) +
          (p.gate.cleared.length ? " · clears " + p.gate.cleared.map(function (m) { return m.limit; }).join(", ") : "")));
      }
      if (placing) deskKey = null;
    }).catch(function (e) { out.textContent = "no answer: " + e.message; });
  }

  function renderBlotter(orders) {
    var t = document.getElementById("blotter"), F = window.OhCamelFormat;
    if (!t) return;
    t.textContent = "";
    var seen = {};
    var rows = orders.open.concat(orders.recent).filter(function (o) {
      if (seen[o.client_order_id]) return false;
      seen[o.client_order_id] = true;
      return true;
    }).slice(0, 20);
    if (rows.length === 0) {
      var empty = document.createElement("tr");
      empty.appendChild(F.el("td", "k", "no orders yet"));
      t.appendChild(empty);
      return;
    }
    var head = document.createElement("tr");
    ["time", "symbol", "side", "qty", "type", "state", "filled", "avg", "why"].forEach(function (h) {
      head.appendChild(F.el("th", "", h));
    });
    t.appendChild(head);
    rows.forEach(function (o) {
      var tr = document.createElement("tr");
      tr.className = "order " + o.state;
      tr.appendChild(F.el("td", "k", o.created_at.slice(11, 19)));
      tr.appendChild(F.el("td", "k", o.symbol));
      tr.appendChild(F.el("td", "k", o.side));
      tr.appendChild(F.el("td", "v num", String(o.qty)));
      tr.appendChild(F.el("td", "k", o.type === "limit" && o.limit_price !== null ? "limit " + o.limit_price.toFixed(2) : "market"));
      tr.appendChild(F.el("td", "k state", o.state.replace(/_/g, " ")));
      tr.appendChild(F.el("td", "v num", o.filled_qty === null ? "—" : String(o.filled_qty)));
      tr.appendChild(F.el("td", "v num", o.avg_fill_price === null ? "—" : o.avg_fill_price.toFixed(2)));
      tr.appendChild(F.el("td", "k reason", o.reason || (o.source === "demo" ? "the demo's trader" : "")));
      t.appendChild(tr);
    });
  }

  function bps(x) { return x === null || x === undefined ? "—" : x.toFixed(1); }

  function renderFills(fills, tca) {
    var t = document.getElementById("fills"), note = document.getElementById("tcanote"), F = window.OhCamelFormat;
    if (!t || !note) return;
    t.textContent = "";
    if (fills.length === 0) { note.textContent = ""; return; }
    var head = document.createElement("tr");
    ["time", "symbol", "side", "qty", "price", "shortfall", "delay", "slippage", "½ spread", "vs model"].forEach(function (h) {
      head.appendChild(F.el("th", "", h));
    });
    t.appendChild(head);
    fills.forEach(function (f) {
      var tr = document.createElement("tr");
      [f.at.slice(11, 19), f.symbol, f.side].forEach(function (x) { tr.appendChild(F.el("td", "k", x)); });
      tr.appendChild(F.el("td", "v num", String(f.qty)));
      tr.appendChild(F.el("td", "v num", f.price.toFixed(2)));
      [f.shortfall_bps, f.delay_bps, f.slippage_bps, f.half_spread_bps, f.versus_model_bps].forEach(function (x) {
        tr.appendChild(F.el("td", "v num", bps(x)));
      });
      t.appendChild(tr);
    });
    var o = tca && tca.overall;
    note.textContent = (o
      ? "Basis points; positive is cost. " + o.count + " fills: shortfall mean " + bps(o.mean_shortfall_bps) +
        ", median " + bps(o.median_shortfall_bps) + ", quantity-weighted " + bps(o.weighted_shortfall_bps) + ". "
      : "") + (tca ? tca.note : "");
  }
```

  (The frame's `positions` entries carry `symbol`, as `renderPositions` reads them; if they name it otherwise, follow `renderPositions`.)

  3. In the alerts panel's kill-switch banner (web/dashboard.js:440-442), replace `"This sets a flag and nothing else; no order is placed or cancelled by this system. "` with `"New orders are refused and every open order is cancelled; positions are not touched. "`. The desk now obeys the switch on both hosts. The banner's next sentence stays.
  4. In A1's `renderDesk`, the venue note (web/dashboard.js:593): `", read side"` becomes `(d.trading ? ", trading" : ", read side")`. `trading` is in the frame from Task 17, and the live venue trades from Task 18.
  5. The comment above `renderAlerts` (web/dashboard.js:420-421), `// Phase 4 state. The page reports it and cannot change it: no route on the` / `// server arms, trips or resets anything.`, becomes:

```js
  // Phase 4 state, and the desk's switch. The kernel's alerts are reported
  // here; the desk's own routes halt and reset the desk (renderSwitch), on the
  // live host only.
```

  `/api/desk/kill` and `/api/desk/kill/reset` exist from Task 16, and `Halt.reset` resets the kernel's switch, so the old sentence is untrue.

- [ ] **Step 3: The styles.** At the end of A1's `/* ---- the desk ---- */` block in `web/page.css`:

```css
  .desk-switch { margin: 10px 0; font-size: 12px; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
  .desk-switch.tripped .desk-switch-line, .desk-switch.halted .desk-switch-line { color: var(--over); font-weight: 600; }
  .desk-switch-note, .ticket-reason, .ticket-gate, .desk-note { color: var(--unknown); }
  .desk-switch button, .ticket button { font: inherit; font-size: 12px; padding: 2px 10px; border: 1px solid currentColor; background: transparent; color: inherit; cursor: pointer; }
  .ticket { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 12px 0 6px; }
  .ticket .lbl, .ticket-out { width: 100%; }
  .ticket select, .ticket input { font: inherit; font-size: 12px; padding: 2px 4px; max-width: 7em; }
  .ticket-out { font-size: 12px; }
  .ticket-bad { color: var(--over); }
  table.blotter, table.fills { margin-top: 10px; font-size: 12px; }
  table.blotter th, table.fills th { text-align: left; font-weight: 400; color: var(--unknown); }
  tr.order.rejected_pre_trade td.state, tr.order.rejected_by_venue td.state, tr.order.failed td.state { color: var(--over); }
  td.k.reason { white-space: normal; max-width: 28em; }
  .desk-note { font-size: 11px; margin-top: 6px; }
```

- [ ] **Step 4: Look at it.** Build, run the demo on 8099, and check the script parses and the page carries what it should:

```bash
eval $(opam env --switch=$PWD --set-switch) && dune build 2>&1 | tail -3 && (command -v node >/dev/null && node --check web/dashboard.js && echo "JS parses"); (./_build/default/bin/main.exe demo 8099 > /tmp/a2-page.log 2>&1 &); curl -sS --retry 30 --retry-connrefused --retry-delay 1 -m 40 http://localhost:8099/ | grep -c 'id="ticket"\|id="blotter"\|id="fills"\|id="deskswitch"'; sleep 100; curl -sS -m 5 http://localhost:8099/api/desk | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["switch"]["state"], len(d["orders"]["recent"]), "orders", len(d["fills"]), "fills", d["tca"]["note"][:40])'; pkill -f "main.exe demo 8099"
```

  Expected: `JS parses` (when node exists), `4`, then a switch state, at least two orders, fills with a note. If a browser is available to you, open http://localhost:8099 before stopping the demo, preview a ticket, and confirm the blotter and the fills render with no console errors; say in the report what you looked at.

- [ ] **Step 5: Commit.**

```bash
make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'
git add web/index.html web/dashboard.js web/page.css
git commit -F - <<'EOF'
web: the desk on the page -- the switch and who may press it, a ticket that previews anywhere and places only where it may, the blotter with every refusal's reason, and each fill's cost -- because an order manager nobody can see is one nobody can check

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 20: The documents say what is true now

**Files:**
- Modify: `README.md`, `docs/overview.md`, `docs/status.md`, `web/index.html`, `lib/verified.ml`

**Interfaces:**
- Consumes: the behaviour Tasks 1-19 built; `lib/verified.ml`'s `tests` (439 since Task 17); the scheduler suite's seven cases (`scheduler_tests = 7`: Task 4's two, Task 14's two, Task 15's three); `make coverage`. Line numbers below are the files' at `cf79764`, except status.md's history table and *Next*: `52e1fbe` added one row above those, so they are cited at that commit. Tasks 4 and 5 edited some nearby lines, so find each sentence by its words.
- Produces: documents that describe the order manager as it is, including what it cannot do. Every sentence that A2 made untrue is replaced, and the count, the scheduler suite and the coverage figure are the final tree's.

- [ ] **Step 1: Measure the final tree.** Run `make test 2>&1 | grep -E 'tests run|Test Successful'`: both executables pass: the main suite runs 439 and the scheduler suite 7. Then run `make coverage 2>&1 | grep 'Coverage:'`. In `lib/verified.ml`, set `coverage_covered`, `coverage_lines`, `coverage_pct` and `dated` from that line, as Task 5 did, and update the comment's quotient and its two roundings. If the figure is below 60%, stop and report; do not lower the floor.

- [ ] **Step 2: README.**
  1. The last paragraph of `## What happens when a limit breaks` (README.md:1261-1268, from `The kill switch sets a flag and is wired to nothing.` to `who meant it.`) becomes:

```markdown
The kill switch is still a flag in the kernel. `lib/alerts.ml` does not import the Alpaca client and could not reach a trading endpoint by mistake. But something now obeys the flag: the desk, outside the kernel. A trip refuses every new order and cancels every open one, and it never flattens a position. A risk system that closes a book by itself is a different and far more dangerous project than this one; the switch stops the desk adding risk and leaves what is held to a person. On the live host only a deliberate reset lifts it.
```

  2. Directly after that section, before `## Building it`, add:

```markdown
## Orders

Every order the desk sends has passed two checks, in this order, and the page shows both answers.

**The rules** (`desk/rules.ml`) ask whether the order should exist. Every failing rule is reported, not only the first:
- the symbol is in the book's universe, and the quantity is a positive whole number;
- the book enables trading, the desk has a trading half, and the book is the account's -- read within the last two sync intervals;
- the kill switch is clear, and the regular session is open;
- the name has a mark that is not stale;
- a limit price is a whole cent (Rule 612) and within the book's collar of the mark;
- quantity × mark is within the order cap;
- the quantity is within the book's share of twenty-day volume, and unknown volume refuses;
- the same order was not proposed within the duplicate window;
- fewer orders are open than the cap.

**The limits** (`lib/gate.ml`) answer what the order would do to the book, on a fork of the live graph with the fill applied -- so no second implementation of exposure or of any limit exists to drift. An order that takes a clear limit over its line, or leaves a breached limit further over, is refused naming the limit. One that brings a breached limit closer to its line passes: a desk must be able to trade out of a breach.

Then the order is written to the journal as `pending_submit` **before** the request that sends it. An answer that never comes is resolved by looking the client order id up, never by sending the order again. The request is bounded at ten seconds. The bound closes a connection that is still connecting or has answered; one held open before its status line lasts until the peer or the kernel ends it. Fills are journaled whatever state their order is in. The book's position follows each fill and is set from the venue's own figure, and the account is read again after it; a read that was already out when the fill landed is refused, so it cannot undo the fill. A restart reconciles every open order against the venue before serving.

**The kill switch** refuses every new order and cancels every open one when a limit it trips on is breached, or when someone halts the desk by hand. It does not flatten positions. On the live host only a deliberate reset lifts it. On the demo it resets itself 90 s after its limit clears, and the page says that only the demo does this.

**Costs** (`desk/tca.ml`, after Perold 1988) are measured per fill from the decision price. Each is split into delay (the market's move before the order arrived) and slippage against the arrival quote's mid, shown beside the quoted half-spread and the difference from the book's modelled half-spread. On the paper account every cost measures Alpaca's fill simulator against IEX's quote -- one venue's, not the national best.

| Route | Host | What it does |
|---|---|---|
| `GET /api/desk/tca` | both | costs per fill, overall and by symbol |
| `GET /api/desk/sessions` | both | the newest 250 recorded session closes |
| `POST /api/desk/preview` | both | the rules' and the limits' answer to a ticket; creates nothing |
| `POST /api/desk/orders` | live | a ticket through the rules, the limits, the journal and the venue |
| `POST /api/desk/cancel` | live | cancel one open order |
| `POST /api/desk/kill` | live | halt the desk and cancel every open order |
| `POST /api/desk/kill/reset` | live | lift the halt; the body must say `{"confirm":"reset"}` |

The live routes require the header `X-OhCamel-Desk: 1` and a request the browser labels as from this site (`Sec-Fetch-Site: same-origin`, or an `Origin` equal to the `Host`), behind the host's password. The demo answers each with 405 and a sentence. Its own trader proposes a small order every 45 seconds, so the blotter has something to show, including refusals.

Limits, stated:
- whole shares; market and limit orders; day orders; regular hours;
- Alpaca paper only, by construction: the trading host is a constant, and a key that does not begin `PK` is refused before a request is sent.
```

  3. The demo transcript under *Running it* (README.md:973-974) quotes the old banner. `it sets a flag and nothing else. Nothing here places orders.` becomes `a trip refuses new orders and cancels open ones, and resets 90 s after the limit clears.`, which is what Task 18's banner prints.
  4. `## What's verified` (README.md:1099-1100, as Task 4 left it): `` `make test` runs 381 tests, all hermetic — no network, no credentials, and nothing that waits on the wall clock. `` becomes `` `make test` runs 439 tests, plus seven in `test/desk_async` that run with the scheduler -- the order manager's cases, the transport's bound and that suite's own count -- all hermetic — no network, no credentials, and nothing that waits on the wall clock: the scheduler's cases move a clock of their own. ``
  5. `### Coverage, and what it is not measuring`: the figure, the date, the badge (README.md:4) and the per-file table take Step 1's measurement, placed as Task 5 placed them.
  6. `## What this is not` (README.md:1294-1295): `There is no order routing and no execution — nothing here places, cancels or simulates a trade.` becomes `Orders go to one place, Alpaca's paper account, and only after the rules and the book's limits pass them (see *Orders*). Nothing here can reach a live-money endpoint, and the public demo trades a venue simulated in this process.` The sentence after it (README.md:1295-1296), `Persistence is one journal, `desk/journal.ml`: a session's close, its marks and a VaR forecast per estimator.`, becomes `Persistence is one journal, `desk/journal.ml`: every order, its events and its fills, and each session's close, marks and VaR forecasts.`
  7. The paragraph before `## Watching it` (README.md:1029-1031): `The dashboard is read-only and unauthenticated, and should be bound to localhost. There is nothing to authorise because no route mutates anything, and nothing in this codebase sends a message or takes an action on its own.` becomes `The dashboard is unauthenticated at the engine, and should be bound to localhost or put behind a password, as the live host's proxy does. Four routes change the desk -- orders, cancel, kill and its reset -- and each refuses a request the page itself did not send (see *Orders*); on the public demo each answers 405.` The demo's trader and a trip's cancels both act on their own, so the old last clause is untrue too.

- [ ] **Step 3: `docs/overview.md`.**
  1. *What it computes* is a table (overview.md:62-72).
     - In its `Limits and alerts` row, `a kill-switch flag` becomes `a kill switch the desk obeys`.
     - After that row, add: `| Orders | The rules, then the book's limits on a fork of the live graph; the journal before the wire; reconciliation against the venue on restart; each fill's cost in basis points. The public demo previews and never takes an order | [`rules.ml`](../desk/rules.ml), [`gate.ml`](../lib/gate.ml), [`oms.ml`](../desk/oms.ml), [`tca.ml`](../desk/tca.ml) |`
  2. **Alerts and the kill switch** (overview.md:85-91): `It trips on limits you name and sets `halt_new_orders = true`, and nothing else happens. There is no order-placement code anywhere in the repository for it to stop, and that is intentional.` becomes `It trips on limits you name; the desk then refuses every new order and cancels every open one, and leaves positions alone. On the live host a person can also halt the desk by hand.`
  3. The route table (overview.md:170-183) gains the seven desk routes after `/api/desk`, each described as in the README's *Orders* table. `No route changes anything.` (overview.md:185) becomes `Four routes change the desk -- orders, cancel, kill and its reset -- on the live host only, and only for a request the page itself sends; on the public demo each answers 405.`
  4. *How it's checked* (overview.md:201): `**381 tests**, all hermetic` becomes `**439 tests**, plus seven scheduler cases in `test/desk_async`, all hermetic`. The coverage bullet takes Step 1's figure.
  5. *What it doesn't do* (overview.md:219-220): `- **No trading.** Nothing places, cancels or simulates an order, and the kill switch is a flag wired to nothing.` becomes `- **Paper trading only.** Orders go to Alpaca's paper account, after the rules and the limits; the demo trades a venue simulated in this process and takes orders only from its own trader.`
  6. The opening section (overview.md:34): `It computes and reports. It never trades.` becomes `It computes and reports, and a desk built around it places paper orders, each one judged by the engine first.`
  7. *What it doesn't do*, the next bullet (overview.md:221-223): `**One journal.** A SQLite journal (`desk/journal.ml`) holds each session's close, its marks and a VaR forecast per estimator.` becomes `**One journal.** A SQLite journal (`desk/journal.ml`) holds every order, its events and its fills, and each session's close, marks and VaR forecasts.` The rest of the bullet stays.

- [ ] **Step 4: `docs/status.md`.**
  1. (status.md:32-33) `It computes and reports. It never places, cancels or simulates an order, and that is a design invariant rather than a missing feature.` becomes `It computes and reports, and a desk built around it places paper orders: every one passes the rules and the book's limits first, and the kernel itself still cannot place one -- a library boundary, invariant 6.`
  2. (status.md:121) `and a kill switch that sets a flag and does nothing else.` becomes `and a kill switch the desk obeys: a trip refuses new orders and cancels open ones.`
  3. *The interface* (status.md:171-173): `Read-only, unauthenticated at the engine, gated at the proxy for the live host. No route mutates anything today; the first planned feature (see *Next*) would be the first that does.` becomes `Unauthenticated at the engine, gated at the proxy for the live host. Four routes change the desk -- `/api/desk/orders`, `/api/desk/cancel`, `/api/desk/kill`, `/api/desk/kill/reset` -- on the live host only, and only for a request carrying `X-OhCamel-Desk: 1` that the browser labels as from this site; on the public demo each answers 405.` The route table (status.md:175-190) gains the seven routes after `/api/desk`.
  4. *What is verified* (status.md:196): `**381 hermetic tests**` becomes `**439 hermetic tests**, plus seven scheduler cases in `test/desk_async`,`. The coverage bullet (status.md:204-206) takes Step 1's figure.
  5. *The invariants* (status.md:267-268): `(10 to 12 bind the order path phase A2 builds; nothing in the repository places, cancels or simulates an order today)` becomes `(10 to 12 bind the order path, `desk/oms.ml`)`.
  6. *What it is not* (status.md:279): `- No order routing, no execution, no simulated fills.` becomes `- Paper orders only, to Alpaca's paper account; the demo's venue is simulated in this process. Whole shares, market and limit orders, day orders, regular hours.` The next bullet (status.md:280), `Persistence is one journal (`desk/journal.ml`, SQLite): each session's close, its marks and a VaR forecast per estimator.`, becomes `Persistence is one journal (`desk/journal.ml`, SQLite): every order, its events and its fills, and each session's close, marks and VaR forecasts.` The rest of that bullet stays.
  7. *How it got here* (status.md:292-302 at `52e1fbe`, whose last row is 2026-09-13's A1 deploy) is a two-column table (When | What). Add, after that row, the row `| <merge date> | Phase A2 merged: the rules and the pre-trade gate on a fork of the live graph; the order manager (the journal before the wire, a timed-out submission resolved by lookup, reconciliation on restart, and no order while the book is not the account's); Alpaca paper's trading half behind a ten-second bound on every request (it closes a request still connecting or already answered; a peer that never sends its status line keeps its socket until the peer or the kernel ends it); the kill switch wired to the desk; previews for anyone and orders only from the live host's own page; costs per fill; the ticket and the blotter. Acceptance on the live host -- one paper order filled -- waits for the owner: the basic-auth password and market hours |`.
  8. *Next* (status.md:311-315 at `52e1fbe`): delete the `**A2**` bullet.

  The *Deployed* row is the deploy's business, not this task's.

- [ ] **Step 5: `web/index.html`.** At index.html:299, `There is no order routing and no execution. Nothing here places, cancels or simulates a trade, and the kill switch sets a flag that is wired to nothing.` becomes `Orders go only to Alpaca's paper account, and only after the rules and the book's limits pass them; this public page previews and takes no order, and its desk trades a venue simulated in this process. The kill switch refuses new orders and cancels open ones, and never touches a position.` The hermetic sentence at :291 stays as Task 4 left it. In the same paragraph at :299, `Persistence is one journal: each session's close, its marks and a VaR forecast per estimator.` becomes `Persistence is one journal: every order, its events and its fills, and each session's close, marks and VaR forecasts.`

- [ ] **Step 6: Check and commit.**

```bash
grep -nE '[0-9]{3} (hermetic )?tests' README.md docs/overview.md docs/status.md; grep -n 'let tests' lib/verified.ml
grep -n 'let scheduler_tests' lib/verified.ml; grep -nE 'seven (scheduler )?(cases|in)' README.md docs/overview.md docs/status.md
grep -rnE 'order-placement code|wired to nothing|places, cancels or|No trading|No route (mutates|changes)|sets a flag and (nothing|does nothing)|Nothing here places orders|5 ms|never trades|no route mutates|nothing to authori|read-only and unauthenticated|a VaR forecast per estimator\.|arms, trips or resets' README.md docs/overview.md docs/status.md web/index.html web/dashboard.js
make test 2>&1 | grep -E 'tests run|Test Successful|FAIL'
git add README.md docs/overview.md docs/status.md web/index.html lib/verified.ml
git commit -F - <<'EOF'
docs: the order manager as it is -- the rules, the limits, the journal before the wire, the switch the desk obeys, the costs, the routes and who may use them, and what it cannot do -- with the count and the coverage measured on the final tree, because every sentence that said nothing here trades is now false and a desk that trades needs its operating manual beside it

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

  Expected:
  - the first line's greps print 439 wherever a count is stated, beside `let tests = 439`;
  - the second line's print `let scheduler_tests = 7`, and "seven" in each of the three count sentences (README.md, docs/overview.md, docs/status.md);
  - the third grep prints nothing except, at most, dated rows of docs/status.md's history table, which stay as written;
  - both executables pass.

---

## Acceptance (spec §5, row A2)

| Criterion | Where it is shown |
|---|---|
| a hand-computable three-fill P&L with costs | Task 14, async suite: "three fills, priced and costed by hand" (149.92 net, 5 bps each, 0 versus the model) |
| an order breaching `tech-cap` rejected naming it | Task 6, `gate`: "a buy that takes TECH over its cap fails, naming it"; Task 14, `oms`: "a preview that takes TECH over its cap names it and creates nothing" |
| a tripped switch rejects and cancels | Task 15, async suite: "a halt refuses new orders and cancels the open ones" and "a limit's trip cancels the open orders" |
| a process killed mid-order reconciles on restart | Task 15, async suite: "a restart reconciles against the venue" (the unfound order still unknown at 41 s and failed at 42 s, on the test's clock) |
| one paper order filled on the live host | after the deploy, with the owner (password, market hours); recorded in `docs/status.md` when done |

## Rulings this plan makes (copy into the ledger at pre-flight)

- `tick` is a rule beyond the spec's table: a sub-penny limit on a stock at or above $1 would be refused by the venue after the journal had recorded it as sent.
- The simulated venue fills a limit when the quote on the order's side reaches it, not when the mark crosses it: otherwise a limit at the mark fills half a spread better than a market order.
- The spec's single venue record is two: `Venue.Read.t` (A1) and `Venue.Trade.t` (here), so a desk that can read an account and not trade it is a value rather than a stub.
- A preview runs the gate even when a rule has failed, so a ticket's author sees both answers.
- The book's `spread_bps` (and `spread_bps_default`) is read as a half-spread -- what one fill pays from the mid to the far side -- which is what cost analysis subtracts from arrival slippage.
- Every request a sequencer job waits on is bounded: ten seconds for Alpaca's submit, cancel, lookup and open-orders requests, and two seconds for the arrival quote on the manager's time source. The Alpaca bound runs through `Alpaca_paper.within`, which frees the job at once and closes a request still connecting or already answered; a peer that never sends its status line keeps its socket until the peer or the kernel ends it. (Plan review finding 4; A1's final review I3 and its re-review's residual 2; plan review 2, finding 1.)
- An order declared failed that the venue later reports is journaled with its fills (as fills after failed) and logged loudly; the switch cannot cancel it. (Plan review finding 9.)
- Reconciliation recovers fills the updates never delivered as one fill per order under a `reconciled:` execution id; after that, a real update counts only past the venue's cumulative quantity.
- An unknown submission is declared not found only after the last scheduled lookup finds nothing -- 2 + 10 + 30 = 42 s by default. A reconciliation, on restart or on a reconnect, never declares it on one lookup: it hands the order to the same schedule. (Plan review finding 8; pinned at 41 s and 42 s by Task 15's restart case.)
- The scheduler suite's cases are counted apart, in `lib/verified.ml`'s `let scheduler_tests = N`, which test/desk_async asserts against its own registry, counting its own count case: 2 at Task 4, 4 at Task 14, 7 at Task 15. Bump it in the same commit that adds a case there. `/api/reports` serves `tests` alone, and the documents name both counts. (Amendment decision 6; plan review 2.)
- An order is refused by the `trading` rule, with `Session_close.not_current`'s sentence, unless the desk's last applied read of the account is within two sync intervals: two minutes on the live host, 30 s on the demo. The manager cannot name `Desk`, so it takes a `~book_is_current` predicate, and the check sits in the rules' context and in `propose`'s re-check after the arrival quote. The frame's `trading` field is `Oms.can_trade`, which also requires the book to be current, so the page reads "off" while this rule would refuse every order. (Plan review 2, finding 2; invariant 12.)
- The startup reconciliation on the live host is awaited for at most 20 s, as the first sync is. A reconciliation still running then goes on in the order manager's queue, and proposals wait behind it in the same sequencer. (Plan review 2, finding 7.)

Taken from A1's final review and ledger (Tasks 1-5):
- A venue's order id is kept whenever it arrives, in any state where the order has none. The same id again changes nothing; a different id is illegal and the first stands; a failed order keeps the id with the anomaly beside it; an order refused before the wire takes none. A `Found` settles `Pending_submit` as an acknowledgement does. (M3; ledger line 44.)
- An overfill's figure is printed with `%.17g` in `Order.Anomaly.to_string` itself, so the log and the journal spell it one way. (Ledger line 43.)
- `desk/journal.mli` hides the handle; tests reach it through `Journal.For_testing`. A file journal that SQLite will not put in WAL mode is refused at open, and a failed open closes its handle. (M8, M2, M1.)
- The book's reads are ordered by a per-desk read number. A read that started before one of the desk's own fills is refused, and `Desk.after_fill` starts the read that corrects the book. Positions follow the desk's fills, and the account read corrects them. (Ledger line 128; M4.)
- `Desk.after_fill` keeps at most one read in flight and one owed, however many fills land. Each read is two of Alpaca's 200 requests a minute, the budget a submit, a cancel and a kill's cancels share, and a 429 on a submit is a refusal. The read that finally applies still started after the last fill. (Plan review 2, finding 6.)
- No test waits on the wall clock. The scheduler suite advances a `Time_source` of its own, and the order manager and `Alpaca_paper.within` take `?time_source`, whose default is the wall clock. (Re-review residuals 6 and 7.)
- `request_json` is built on `Cohttp_async.Client.call ~interrupt:abandon`, following A1's `get_json`, which stays exactly as it is: `~rest:`Log`, and the response body's pipe closed on abandon. The bound closes a request that is still connecting or has answered; a peer that never sends its status line keeps its socket until the peer or the kernel ends it. `Client.Connection` would not close that one either, because a killed sequencer cleans a connection only once the request holding it returns (async_kernel throttle.ml:151-158, :176-186). Amendment decision 8, which moved `get_json` onto a shared connection, is reversed. (I3's residual; plan review 2, finding 1, the reviewer's fallback.)
- `/api/desk/sessions` answers the newest 250 closes, not the whole table. (M5's reason, applied to A2's own route.)
- Coverage covers `lib/` and `desk/` from Task 5. The CI floor stays 60% against the combined figure, and a figure below it stops the task rather than moving the floor. (The ledger's A1 coverage ruling.)

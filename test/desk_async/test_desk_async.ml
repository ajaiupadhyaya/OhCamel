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

let t0 = Time_ns.of_string_with_utc_offset "2026-09-14T14:00:00Z"

let case name test =
  Alcotest.test_case name `Quick (fun () -> Thread_safe.block_on_async_exn test)

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
  Graph.apply_tick f.graph
    { Tick.symbol; price = Price.of_float price; time = Time.now () };
  Graph.set_now f.graph (Time.now ());
  Graph.stabilize f.graph

let tech_cap =
  {
    Limit.name = "tech-cap";
    scope = Limit.Sector tech;
    kind = Limit.Gross_notional (Notional.of_float 100_000.0);
  }

let fixture ?(limits = [ tech_cap ]) () =
  let graph =
    Graph.create
      ~starting_cash:(Notional.of_float 1_000_000.0)
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
      ~now:Time_ns.now
      ~half_spread_bps:(fun _ -> 5.0)
      ~cash:(Notional.of_float 1_000_000.0)
      ~positions:[] ()
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
  List.iter
    [ (aapl, 100.0); (msft, 200.0); (xom, 50.0) ]
    ~f:(fun (s, p) ->
      Graph.set_returns graph s
        [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |];
      mark f s p);
  f

(* Every manager this suite builds gets a seed of its own, in the order they
   are built. A client order id is 48 bits of millisecond and 80 bits from
   this rng (desk/ids.ml), so two managers over one journal built from the
   same seed -- the restart case builds four -- mint the SAME id for two
   orders proposed inside one millisecond, and the journal's unique index on
   client_order_id refuses the second. Counted rather than randomised: the
   cases run in the registry's order, so the ids are the same on every run. *)
let managers_built = ref 0

(* A manager over the fixture. [trade] replaces the venue's trading half with
   one a case has wrapped; without it the manager trades the venue directly.
   Its lookups keep production's schedule -- 2, 10 and 30 s -- on the
   fixture's clock, so they fire only when a case advances it. *)
let manager ?trade ?(halt = D.Halt.create D.Halt.Source.none) f =
  let trade =
    match trade with Some t -> t | None -> D.Sim_venue.trade ~auto:false f.venue
  in
  incr managers_built;
  let oms =
    D.Oms.create ~graph:f.graph ~journal:f.journal
      ~spec:{ Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled }
      ~read:(Some (D.Sim_venue.read f.venue))
      ~trade:(Ok trade) ~halt ~accepts_tickets:true ~adv:(D.Oms.Adv.Fixed 1_000_000.0)
      ~now:Time_ns.now
      ~rng:(Random.State.make [| 7; !managers_built |])
      ~on_change:ignore
      ~on_event:(fun e -> Queue.enqueue f.events e)
      ~after_fill:ignore
      ~book_is_current:(fun () -> true)
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
  (Option.value_exn
     (D.Journal.load_order f.journal o.D.Order.request.D.Order.Request.client_order_id))
    .D.Journal.Order_row.order

let state f o = D.Order.State.to_string (journaled f o).D.Order.state

let ticket ?(kind = D.Order.Kind.Market) symbol side qty =
  { D.Ticket.symbol; side; qty; kind }

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
  List.iter [ buy; sell40; sell60 ] ~f:(fun o ->
      Alcotest.(check string) "filled" "filled" (state f o));
  Alcotest.(check (float 1e-9)) "flat" 0.0 (Qty.to_float (Graph.qty f.graph aapl));
  Alcotest.(check (float 1e-6))
    "cash 1,000,000 + 149.92" 1_000_149.92
    (Notional.to_float (Graph.cash f.graph));
  let fills = D.Journal.recent_fills f.journal ~limit:10 in
  Alcotest.(check int) "three fills" 3 (List.length fills);
  let costs = D.Oms.costs oms fills in
  let s = D.Tca.summarize costs in
  Alcotest.(check (option (float 1e-6)))
    "shortfall, quantity-weighted" (Some 5.0) s.D.Tca.Summary.weighted_shortfall_bps;
  Alcotest.(check (option (float 1e-6)))
    "versus the model" (Some 0.0) s.D.Tca.Summary.mean_versus_model_bps;
  List.iter costs ~f:(fun (_, c) ->
      Alcotest.(check (option (float 1e-6)))
        "no delay: the quote's mid is the mark" (Some 0.0) c.D.Tca.Costs.delay_bps);
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
  Alcotest.(check string)
    "unknown at first" "submit_unknown"
    (D.Order.State.to_string o.D.Order.state);
  let%bind () = settle () in
  Alcotest.(check (option string))
    "found, with the venue's id" (Some "sim-1") (journaled f o).D.Order.venue_order_id;
  Alcotest.(check string) "and accepted" "accepted" (state f o);
  let%bind () = pump f in
  Alcotest.(check string) "then filled" "filled" (state f o);
  Alcotest.(check int) "and the venue received it once" 1 (D.Sim_venue.received f.venue);
  Graph.destroy f.graph;
  return ()

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
    D.Alpaca_paper.within ~time_source ~span:D.Alpaca_paper.request_timeout
      ~what:"GET /v2/account" (fun ~abandon ->
        abandoned := Some abandon;
        Deferred.never ())
  in
  let answered_abandon = ref None in
  let%bind answered =
    D.Alpaca_paper.within ~time_source ~span:D.Alpaca_paper.request_timeout
      ~what:"GET /v2/clock" (fun ~abandon ->
        answered_abandon := Some abandon;
        return (Ok 42))
  in
  Alcotest.(check (option int))
    "an answer inside the bound is that answer, the 42 given" (Some 42)
    (Result.ok answered);
  let settle () = Scheduler.yield_until_no_jobs_remain () in
  (* 14:00:00 + 30 s - 1 ns = 14:00:29.999999999 *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle clock
      ~to_:(Time_ns.add t0 Time_ns.Span.(D.Alpaca_paper.request_timeout - nanosecond))
  in
  let%bind () = settle () in
  Alcotest.(check bool)
    "nothing at 1 ns short of 30 s" false
    (Deferred.is_determined never);
  (* 14:00:00 + 30 s *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle clock
      ~to_:(Time_ns.add t0 D.Alpaca_paper.request_timeout)
  in
  let%map () = settle () in
  (match Deferred.peek never with
  | Some (Error e) ->
      (* the bound, 30 s, as Time_ns.Span.to_string_hum prints it *)
      Alcotest.(check string)
        "the request and the bound, and nothing else"
        "alpaca_paper: GET /v2/account did not answer within 30s" (Error.to_string_hum e)
  | Some (Ok _) -> Alcotest.fail "a Deferred that never fills answered"
  | None -> Alcotest.fail "the bound had not fired at 30 s on the test's clock");
  Alcotest.(check bool)
    "the request was told it is abandoned" true
    (Option.value_map !abandoned ~default:false ~f:Deferred.is_determined);
  (* And the other half of the bound: [within] fills [abandon] only where it
     times out, so the request that answered at 42 is never told to close a
     connection it already finished with -- not even now, past 30 s. A
     [default] of true would let a missing ref pass as a success, so it fails. *)
  Alcotest.(check bool)
    "a request that answered is never abandoned, even past its bound" false
    (Option.value_map !answered_abandon ~default:true ~f:Deferred.is_determined)

let limit_at_99 = D.Order.Kind.Limit (Price.of_float 99.0)

let rules (p : D.Oms.Preview.t) =
  List.map p.D.Oms.Preview.failures ~f:(fun x -> x.D.Rules.Failure.rule)

(* A limit buy at 99 rests: the ask is 100 x 1.0005 = 100.05, above it, and
   99 is 1% from the mark, inside the 5% collar. A second, 40 at 99, is still
   on its way to the venue when a halt by hand lands. The next proposal is
   refused, naming only the switch; after a reset a proposal goes through.

   Neither cancel goes as asked, and the desk cannot tell the two apart: the
   adapter answers both with the same error. The resting order's first DELETE
   never reaches the venue. The flying order has no venue id when the kill
   lands, so the kill skips it and [record] cancels it the moment its id
   arrives; that DELETE does reach the venue, and its answer is lost. Under the
   halt each is sent again on the lookups' schedule, first at t0 + 2 s: the
   resting order's retry cancels it, and the flying order's finds it already
   cancelled and sends nothing. The venue numbers orders as it receives them,
   so the resting order is sim-1 and the flying one sim-2. *)
let test_a_halt_refuses_and_cancels () =
  let f = fixture () in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let hold = ref None and deletes = ref [] in
  let sent id = List.count !deletes ~f:(String.equal id) in
  let lost = Or_error.error_string "timed out after 10 s" in
  let wrapped =
    {
      sim with
      D.Venue.Trade.submit =
        (fun r ->
          match !hold with
          | None -> sim.D.Venue.Trade.submit r
          | Some gate ->
              let%bind () = Ivar.read gate in
              sim.D.Venue.Trade.submit r);
      cancel =
        (fun id ->
          deletes := id :: !deletes;
          match (id, sent id) with
          | "sim-1", 1 -> return lost
          | "sim-2", 1 ->
              let%map (_ : unit Or_error.t) = sim.D.Venue.Trade.cancel id in
              lost
          | _ -> sim.D.Venue.Trade.cancel id);
    }
  in
  let oms = manager ~trade:wrapped f in
  let%bind _, resting =
    D.Oms.propose oms (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 50)
  in
  let%bind () = pump f in
  Alcotest.(check string) "resting" "accepted" (state f resting);
  let gate = Ivar.create () in
  hold := Some gate;
  let in_flight = D.Oms.propose oms (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 40) in
  let%bind () = settle () in
  (* The proposal is journaled and waiting on the venue, so the halt is in
     force before the venue answers it. *)
  let killed = D.Oms.kill oms ~why:"testing the switch" in
  Ivar.fill_exn gate ();
  let%bind _, flying = in_flight in
  let%bind () = killed in
  let%bind () = pump f in
  (* t0, the clock not yet moved *)
  Alcotest.(check (option string))
    "the flying order's id arrived under the halt" (Some "sim-2")
    (journaled f flying).D.Order.venue_order_id;
  Alcotest.(check string)
    "and it was cancelled by that id at once" "cancelled" (state f flying);
  Alcotest.(check string)
    "the resting order's DELETE never arrived: pending_cancel" "pending_cancel"
    (state f resting);
  (* one DELETE each so far *)
  Alcotest.(check (pair int int))
    "DELETEs for sim-1 and sim-2" (1, 1)
    (sent "sim-1", sent "sim-2");
  let%bind p, refused = D.Oms.propose oms (ticket aapl D.Order.Side.Buy 1) in
  Alcotest.(check string) "refused" "rejected_pre_trade" (state f refused);
  Alcotest.(check (list string)) "by the switch alone" [ "kill_switch" ] (rules p);
  (* 14:00:00 + 2 s - 1 ns: the first retry is not yet due *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 Time_ns.Span.(of_sec 2.0 - nanosecond))
  in
  let%bind () = settle () in
  Alcotest.(check string)
    "1 ns short of 2 s, still pending_cancel" "pending_cancel" (state f resting);
  (* 14:00:00 + 2 s, the first of the 2, 10 and 30 s delays *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 2.0))
  in
  let%bind () = settle () in
  Alcotest.(check string) "at 2 s the retry cancels it" "cancelled" (state f resting);
  (* sim-1: the lost one, then the retry = 2. sim-2: still 1, because its
     retry found the order out of the open set and sent nothing. *)
  Alcotest.(check (pair int int))
    "DELETEs for sim-1 and sim-2" (2, 1)
    (sent "sim-1", sent "sim-2");
  D.Halt.reset (D.Oms.halt oms);
  let%bind _, placed = D.Oms.propose oms (ticket aapl D.Order.Side.Buy 2) in
  let%bind () = pump f in
  Alcotest.(check string) "after a reset, filled" "filled" (state f placed);
  (* 14:00:00 + 42 s, past every delay on the schedule: a DELETE that was
     answered ends its retries, so nothing more is sent *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 42.0))
  in
  let%bind () = settle () in
  Alcotest.(check (pair int int)) "and no more DELETEs" (2, 1) (sent "sim-1", sent "sim-2");
  Graph.destroy f.graph;
  return ()

(* The kernel's switch, armed on aapl-cap at 20,000. A resting buy of 50 at
   99 is 4,950, well inside it. Then the book's AAPL is set to 300 x 100 =
   30,000, over the cap, and the trip cancels the order with nobody asking. *)
let test_a_limit's_trip_cancels_the_open_orders () =
  let aapl_cap =
    {
      Limit.name = "aapl-cap";
      scope = Limit.Instrument aapl;
      kind = Limit.Gross_notional (Notional.of_float 20_000.0);
    }
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
  let alerts =
    Option.value_exn (Or_error.ok_exn (Ohcamel.Alerts.attach ~graph:f.graph ~config))
  in
  let oms = manager ~halt:(D.Halt.create (D.Halt.Source.of_alerts alerts)) f in
  D.Oms.watch_alerts oms alerts;
  let%bind _, resting =
    D.Oms.propose oms (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 50)
  in
  let%bind () = pump f in
  Alcotest.(check string) "resting" "accepted" (state f resting);
  Graph.set_qty f.graph aapl (Qty.of_float 300.0);
  Graph.stabilize f.graph;
  let%bind () = pump f in
  Alcotest.(check string) "cancelled by the trip" "cancelled" (state f resting);
  Alcotest.(check string)
    "and the switch reads tripped" "tripped"
    (D.Halt.State.name (D.Halt.state (D.Oms.halt oms)));
  Graph.destroy f.graph;
  return ()

(* A process killed mid-order. Three managers over one journal and one venue
   never hear back: the venue received the first's and the third's orders,
   and never the second's. A fourth manager -- the restart -- reconciles: the
   first order is found and then fills; the second becomes an unknown and
   fails when the restart's lookups all miss -- 2, 10 and 30 s apart on the
   fixture's clock, 42 s in all, and not a moment sooner. The third, a limit
   buy of 20 XOM at 49 that rests (the ask is 50 x 1.0005 = 50.025, above it;
   49 is 2% from the mark, inside the collar), is the lookup that FAILS: the
   restart's first question about it errors, and it goes onto the same
   schedule, whose first lookup finds it. Nothing was sent twice. The three
   that never hear back get update pipes of their own that are already
   closed, so none can take the venue's updates from the restart. *)
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
  let never_sent =
    { answer_lost with D.Venue.Trade.submit = (fun _ -> Deferred.never ()) }
  in
  let first = manager ~trade:answer_lost f
  and second = manager ~trade:never_sent f
  and third = manager ~trade:answer_lost f in
  don't_wait_for
    (Deferred.ignore_m (D.Oms.propose first (ticket aapl D.Order.Side.Buy 10)));
  don't_wait_for
    (Deferred.ignore_m (D.Oms.propose second (ticket msft D.Order.Side.Buy 5)));
  don't_wait_for
    (Deferred.ignore_m
       (D.Oms.propose third
          (ticket
             ~kind:(D.Order.Kind.Limit (Price.of_float 49.0))
             xom D.Order.Side.Buy 20)));
  let%bind () = settle () in
  let states () =
    D.Journal.recent_orders f.journal ~limit:10
    |> List.map ~f:(fun r ->
        let o = r.D.Journal.Order_row.order in
        ( Symbol.to_string o.D.Order.request.D.Order.Request.symbol,
          D.Order.State.to_string o.D.Order.state ))
    |> List.sort ~compare:(Tuple2.compare ~cmp1:String.compare ~cmp2:String.compare)
  in
  Alcotest.(check (list (pair string string)))
    "all three journaled before the wire, none answered"
    [ ("AAPL", "pending_submit"); ("MSFT", "pending_submit"); ("XOM", "pending_submit") ]
    (states ());
  (* AAPL and XOM; MSFT's request never left *)
  Alcotest.(check int) "the venue received two" 2 (D.Sim_venue.received f.venue);
  (* The restart's socket, and every lookup it makes counted by the order's
     symbol. XOM's first lookup errors, as a request that timed out would. *)
  let venue = D.Sim_venue.trade ~auto:false f.venue in
  let lookups = Symbol.Table.create () in
  let lookups_of s = Option.value (Hashtbl.find lookups s) ~default:0 in
  let counted =
    {
      venue with
      D.Venue.Trade.find_order =
        (fun c ->
          let symbol =
            (Option.value_exn (D.Journal.load_order f.journal c))
              .D.Journal.Order_row.order
              .D.Order.request
              .D.Order.Request.symbol
          in
          Hashtbl.incr lookups symbol;
          if Symbol.equal symbol xom && lookups_of xom = 1 then
            return (Or_error.error_string "timed out after 10 s")
          else venue.D.Venue.Trade.find_order c);
    }
  in
  let looked_up () = (lookups_of aapl, lookups_of msft, lookups_of xom) in
  let restarted = manager ~trade:counted f in
  let%bind () = D.Oms.reconcile restarted in
  let%bind () = pump f in
  (* t0: one lookup each, the reconciliation's own. AAPL was found, and the
     pump filled it. MSFT missed and XOM's lookup errored, and a miss and an
     error decide the same thing: an unknown, for the schedule. *)
  Alcotest.(check (list (pair string string)))
    "at t0: found and filled; a miss and an error both unknown"
    [ ("AAPL", "filled"); ("MSFT", "submit_unknown"); ("XOM", "submit_unknown") ]
    (states ());
  Alcotest.(check (triple int int int)) "lookups at t0" (1, 1, 1) (looked_up ());
  (* The scheduled lookups follow at t0 + 2 s, t0 + 2 + 10 = 12 s and
     t0 + 12 + 30 = 42 s. advance_by_alarms stops at each alarm's own time and
     runs its jobs (wait_for) before it moves on, so each delay is measured
     from the lookup before it. *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 41.0))
  in
  let%bind () = settle () in
  Alcotest.(check (list (pair string string)))
    "at 41 s: MSFT still unknown, one lookup to go; XOM found at 2 s"
    [ ("AAPL", "filled"); ("MSFT", "submit_unknown"); ("XOM", "submitted") ]
    (states ());
  (* AAPL 1: found at t0, never scheduled. MSFT 1 + 2: t0, 2 s, 12 s.
     XOM 1 + 1: t0 (the error), 2 s (found), and a found order leaves the
     schedule. *)
  Alcotest.(check (triple int int int)) "lookups at 41 s" (1, 3, 2) (looked_up ());
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 42.0))
  in
  let%bind () = settle () in
  Alcotest.(check (list (pair string string)))
    "at 42 s the last lookup misses: failed"
    [ ("AAPL", "filled"); ("MSFT", "failed"); ("XOM", "submitted") ]
    (states ());
  (* MSFT 1 + 3: t0, 2 s, 12 s, 42 s; the others unchanged *)
  Alcotest.(check (triple int int int)) "lookups at 42 s" (1, 4, 2) (looked_up ());
  (* still AAPL and XOM alone *)
  Alcotest.(check int) "and nothing was sent again" 2 (D.Sim_venue.received f.venue);
  Graph.destroy f.graph;
  return ()

let suites =
  [
    ( "transport",
      [
        case "a request that never answers is an error at its bound"
          test_a_request_that_never_answers_is_an_error_at_its_bound;
      ] );
    ( "orders",
      [
        case "three fills, priced and costed by hand" test_three_fills;
        case "an unknown answer is resolved, and never resent"
          test_an_unknown_answer_is_resolved_and_never_resent;
        case "a halt refuses new orders and cancels the open ones"
          test_a_halt_refuses_and_cancels;
        case "a limit's trip cancels the open orders"
          test_a_limit's_trip_cancels_the_open_orders;
        case "a restart reconciles against the venue" test_a_restart_reconciles;
      ] );
  ]

(* The registry counts itself against lib/verified.ml's [scheduler_tests], as
   test_ohcamel.ml counts itself against [tests]. The +1 is this case. It needs
   no scheduler. *)
let test_the_count_is_the_dated_one () =
  let registered =
    1 + List.sum (module Int) suites ~f:(fun (_, cases) -> List.length cases)
  in
  Alcotest.(check int)
    (sprintf "lib/verified.ml says %d scheduler tests; the registry holds %d"
       Ohcamel.Verified.scheduler_tests registered)
    Ohcamel.Verified.scheduler_tests registered

let () =
  Alcotest.run "ohcamel desk, with the scheduler"
    (suites
    @ [
        ( "verified",
          [
            Alcotest.test_case "the scheduler suite's count is the dated one" `Quick
              test_the_count_is_the_dated_one;
          ] );
      ])

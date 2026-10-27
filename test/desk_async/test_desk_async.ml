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

(* [extra]: more names, each with its sector and first print, for a case
   that needs more than three (Task 10's waves). *)
let fixture ?(limits = [ tech_cap ]) ?(extra = []) () =
  let graph =
    Graph.create
      ~starting_cash:(Notional.of_float 1_000_000.0)
      ~instruments:
        ([
           { Instrument.symbol = aapl; sector = tech };
           { Instrument.symbol = msft; sector = tech };
           { Instrument.symbol = xom; sector = Sector.of_string "ENERGY" };
         ]
        @ List.map extra ~f:(fun (symbol, sector, _) ->
            { Instrument.symbol; sector = Sector.of_string sector }))
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
    ([ (aapl, 100.0); (msft, 200.0); (xom, 50.0) ]
    @ List.map extra ~f:(fun (s, _, p) -> (s, p)))
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
   one a case has wrapped, and [read] its read half; without them the manager
   uses the venue directly. [now] is the wall clock unless a case gives the
   manager its own, and the book is the account's unless a case says when it
   stops being so. [on_change] is the page's nudge, a no-op unless a case
   makes it raise; [on_event] runs after each line is kept in [f.events], for
   a case whose log must raise. Its lookups keep production's schedule -- 2, 10 and 30 s --
   on the fixture's clock, so they fire only when a case advances it. [run] is
   started unless a case starts it itself, to watch what it returns. *)
let manager ?trade ?read ?(now = Time_ns.now) ?(book_is_current = fun () -> true)
    ?(halt = D.Halt.create D.Halt.Source.none) ?(on_change = ignore) ?(on_event = ignore)
    ?(run = true) ?(incubation_cap = 0.0)
    ?(max_order_notional = Desk_spec.default.Desk_spec.max_order_notional) f =
  let trade =
    match trade with Some t -> t | None -> D.Sim_venue.trade ~auto:false f.venue
  in
  let read = match read with Some r -> r | None -> D.Sim_venue.read f.venue in
  incr managers_built;
  let oms =
    D.Oms.create ~graph:f.graph ~journal:f.journal
      ~spec:
        {
          Desk_spec.default with
          Desk_spec.trading = Desk_spec.Enabled;
          incubation_cap;
          max_order_notional;
        }
      ~read:(Some read) ~trade:(Ok trade) ~halt ~accepts_tickets:true
      ~adv:(D.Oms.Adv.Fixed 1_000_000.0) ~now
      ~rng:(Random.State.make [| 7; !managers_built |])
      ~on_change
      ~on_event:(fun e ->
        Queue.enqueue f.events e;
        on_event e)
      ~after_fill:ignore ~book_is_current
      ~time_source:(Time_source.read_only f.clock)
      ()
  in
  D.Oms.set_market oms ~session_open:true ~adv20:[];
  if run then don't_wait_for (D.Oms.run oms);
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
        (fun permit r ->
          let%map (_ : D.Venue.Submission.t) = sim.D.Venue.Trade.submit permit r in
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
   arrives; that DELETE does reach the venue, and its answer is lost. Before
   any retry, the venue reports 20 of the resting order's 50 filled, which
   moves it from pending_cancel to partially_filled: a fill must not end the
   cancel's retries, or the other 30 rest at the venue through the halt. Under
   the halt each is sent again on the lookups' schedule, first at t0 + 2 s:
   the resting order's retry cancels what is left of it, and the flying
   order's finds it already cancelled and sends nothing. The venue numbers
   orders as it receives them, so the resting order is sim-1 and the flying
   one sim-2. *)
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
        (fun permit r ->
          match !hold with
          | None -> sim.D.Venue.Trade.submit permit r
          | Some gate ->
              let%bind () = Ivar.read gate in
              sim.D.Venue.Trade.submit permit r);
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
  (* The venue's word, on its own stream: 20 of sim-1's 50 at its limit of
     99. The simulated venue fills whole orders only, so the partial fill is
     written onto its stream by hand; its own book still reads the order as
     new, as Alpaca's reads a partly filled order it will still cancel. *)
  let sim_1 =
    Option.value_exn
      (D.Sim_venue.find_now f.venue
         resting.D.Order.request.D.Order.Request.client_order_id)
  in
  D.Sim_venue.emit f.venue
    {
      D.Venue.Update.event = "partial_fill";
      order =
        {
          sim_1 with
          D.Venue.Venue_order.status = "partially_filled";
          filled_qty = 20.0;
          filled_avg_price = Some 99.0;
        };
      fill =
        Some
          {
            D.Order.Fill.execution_id = "sim-1-partial";
            qty = 20.0;
            price = Price.of_float 99.0;
            at = Time_ns.now ();
            (* nothing held before, so the 20 bought are the position *)
            position_qty = Some 20.0;
          };
      at = Time_ns.now ();
    };
  let%bind () = settle () in
  Alcotest.(check string)
    "a fill under the halt: partially_filled" "partially_filled" (state f resting);
  (* 14:00:00 + 2 s - 1 ns: the first retry is not yet due *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 Time_ns.Span.(of_sec 2.0 - nanosecond))
  in
  let%bind () = settle () in
  Alcotest.(check string)
    "1 ns short of 2 s, still partially_filled" "partially_filled" (state f resting);
  (* 14:00:00 + 2 s, the first of the 2, 10 and 30 s delays *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 2.0))
  in
  let%bind () = settle () in
  Alcotest.(check string)
    "at 2 s the retry cancels the rest of it" "cancelled" (state f resting);
  (* the 20 the venue reported stay filled; the other 50 - 20 = 30 were
     cancelled *)
  Alcotest.(check (float 1e-9))
    "20 filled, kept" 20.0 (journaled f resting).D.Order.filled_qty;
  (* A retry that ends under the switch says so, unless its order left the
     open set: one line for sim-1, whose retry the venue confirmed, and none
     for sim-2, whose retry found it already cancelled. *)
  let stopped o =
    Queue.count f.events ~f:(fun line ->
        String.is_substring line ~substring:"stopped sending the cancel of"
        && String.is_substring line
             ~substring:
               (D.Ids.Client_order_id.to_string
                  o.D.Order.request.D.Order.Request.client_order_id))
  in
  Alcotest.(check (pair int int))
    "the lines saying a retry stopped, for sim-1 and sim-2" (1, 0)
    (stopped resting, stopped flying);
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

(* A raise after the halt, through the kill route itself. A limit buy of 50
   AAPL at 99 rests; then the page's nudge raises -- once, the first time it
   is called after the order rests, which is inside the kill, just after the
   halt is set -- and so does the log line that reports it. The person who
   pressed halt is answered 200 with the switch as it reads and a fixed
   sentence, never the exception's own words, which go to the log; and the
   open order is still cancelled. *)
let test_a_raise_after_the_halt_is_answered_and_the_cancels_still_go () =
  let f = fixture () in
  let armed = ref false in
  let on_change () =
    if !armed then (
      armed := false;
      failwith "boom at /var/lib/ohcamel/secret")
  in
  (* And the log line that reports the raise raises in its turn (fix round 1,
     M3), after it is kept: the answer must not depend on the log. *)
  let on_event line =
    if String.is_substring line ~substring:"kill raised" then
      failwith "the log is broken too"
  in
  let oms = manager ~on_change ~on_event f in
  let%bind _, resting =
    D.Oms.propose oms (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 50)
  in
  let%bind () = pump f in
  Alcotest.(check string) "resting" "accepted" (state f resting);
  let server =
    Ohcamel.Server.create
      ~extensions:(D.Desk_routes.extensions ~host:`Live ~oms)
      ~mode:`Live ~graph:f.graph ~factor:"SYNTHETIC" ()
  in
  armed := true;
  let%bind response, body =
    Ohcamel.Server.dispatch server
      {
        Ohcamel.Server.Request.meth = `POST;
        path = "/api/desk/kill";
        headers =
          Cohttp.Header.of_list
            [ ("X-OhCamel-Desk", "1"); ("Sec-Fetch-Site", "same-origin") ];
        body = {|{"why":"testing the switch"}|};
      }
  in
  let text =
    match body with
    | `String s -> s
    | `Strings ss -> String.concat ss
    | `Empty -> ""
    | `Pipe _ -> Alcotest.fail "the kill answered with a pipe"
  in
  Alcotest.(check bool) "the nudge did raise" false !armed;
  Alcotest.(check int)
    "answered 200: the halt is set" 200
    (Cohttp.Code.code_of_status (Cohttp.Response.status response));
  let json = Yojson.Safe.from_string text in
  Alcotest.(check (option string))
    "the error, a fixed sentence" (Some D.Desk_routes.kill_error_sentence)
    Yojson.Safe.Util.(to_string_option (member "error" json));
  Alcotest.(check string)
    "beside the switch as it reads" "halted"
    Yojson.Safe.Util.(to_string (member "state" (member "switch" json)));
  Alcotest.(check bool)
    "never the exception's own words" false
    (String.is_substring text ~substring:"boom"
    || String.is_substring text ~substring:"/var/lib");
  Alcotest.(check bool)
    "which reach the log" true
    (Queue.exists f.events ~f:(fun line ->
         String.is_substring line ~substring:"raised"
         && String.is_substring line ~substring:"boom"));
  let%bind () = pump f in
  Alcotest.(check string)
    "and the open order is still cancelled" "cancelled" (state f resting);
  Graph.destroy f.graph;
  return ()

(* [Oms.kill] itself, as the scheduler cases call it (fix round 1, M2): a
   raise after its halt -- the page's nudge, as above -- still reaches the
   caller, but only once the cancels have started, so the resting order is
   cancelled all the same. *)
let test_a_raise_inside_the_kill_still_sends_the_cancels () =
  let f = fixture () in
  let armed = ref false in
  let on_change () =
    if !armed then (
      armed := false;
      failwith "boom")
  in
  let oms = manager ~on_change f in
  let%bind _, resting =
    D.Oms.propose oms (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 50)
  in
  let%bind () = pump f in
  Alcotest.(check string) "resting" "accepted" (state f resting);
  armed := true;
  let raised =
    match D.Oms.kill oms ~why:"testing the switch" with
    | (_ : unit Deferred.t) -> false
    | exception _ -> true
  in
  Alcotest.(check bool) "the raise reaches the caller" true raised;
  let%bind () = pump f in
  Alcotest.(check string)
    "halted" "halted"
    (D.Halt.State.name (D.Halt.state (D.Oms.halt oms)));
  Alcotest.(check string)
    "and the order cancelled all the same" "cancelled" (state f resting);
  Graph.destroy f.graph;
  return ()

(* The venue's own updates, forwarded to a pipe the case closes: closing it
   is the stream giving up for good, as the paper host's does when it refuses
   the key, and whatever the venue says after that reaches no one. *)
let forwarded (sim : _ D.Venue.Trade.t) =
  let stream, to_desk = Pipe.create () in
  don't_wait_for
    (Pipe.iter_without_pushback sim.D.Venue.Trade.updates ~f:(fun u ->
         Pipe.write_without_pushback_if_open to_desk u));
  (stream, to_desk)

let switch_of oms = D.Halt.State.name (D.Halt.state (D.Oms.halt oms))

let resting_at f o =
  (Option.value_exn
     (D.Sim_venue.find_now f.venue o.D.Order.request.D.Order.Request.client_order_id))
    .D.Venue.Venue_order.status

(* The engine's stop (fix round 1). A limit buy of 50 AAPL at 99 rests, as
   sim-1; then the stream ends. The engine stops the desk and asks the venue
   to cancel the order, which it does -- but its word that it did travels on
   the stream that ended, so the desk still reads pending_cancel, and no
   confirmation can move it until a restart reconciles. A reset changes
   nothing, and a new order is refused by the switch alone. *)
let test_the_engine's_stop_cancels_and_a_reset_leaves_it_stopped () =
  let f = fixture () in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let stream, to_desk = forwarded sim in
  let deletes = ref [] in
  let oms =
    manager
      ~trade:
        {
          sim with
          D.Venue.Trade.cancel =
            (fun id ->
              deletes := id :: !deletes;
              sim.D.Venue.Trade.cancel id);
          updates = stream;
        }
      f
  in
  let%bind _, resting =
    D.Oms.propose oms (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 50)
  in
  let%bind () = pump f in
  Alcotest.(check string) "resting" "accepted" (state f resting);
  Pipe.close to_desk;
  let%bind () = settle () in
  Alcotest.(check string) "the switch reads stopped" "stopped" (switch_of oms);
  Alcotest.(check (list string)) "one DELETE, for sim-1" [ "sim-1" ] !deletes;
  Alcotest.(check string) "the venue cancelled it" "canceled" (resting_at f resting);
  Alcotest.(check string)
    "but the desk cannot hear that: pending_cancel" "pending_cancel" (state f resting);
  let said words =
    Queue.exists f.events ~f:(fun line -> String.is_substring line ~substring:words)
  in
  Alcotest.(check (pair bool bool))
    "the stop and its cancels, said" (true, true)
    ( said "STOPPED by the engine",
      said "the engine's stop has asked the venue to cancel every open order" );
  let%bind () = pump f in
  Alcotest.(check string)
    "the venue's report goes nowhere" "pending_cancel" (state f resting);
  D.Halt.reset (D.Oms.halt oms);
  Alcotest.(check string) "a reset leaves it stopped" "stopped" (switch_of oms);
  let%bind p, refused = D.Oms.propose oms (ticket aapl D.Order.Side.Buy 1) in
  Alcotest.(check string) "a new order: refused" "rejected_pre_trade" (state f refused);
  Alcotest.(check (list string)) "by the switch alone" [ "kill_switch" ] (rules p);
  Graph.destroy f.graph;
  return ()

(* The engine's stop when the venue refuses the DELETE too, as it will when
   the stream ended because the paper host refused the key. The cancel is
   sent again under the stop on the lookups' schedule -- 2, 10 and 30 s --
   and after the last the order is left pending_cancel, said aloud. *)
let test_under_the_engine's_stop_a_refused_cancel_is_sent_again () =
  let f = fixture () in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let stream, to_desk = forwarded sim in
  let deletes = ref 0 in
  let oms =
    manager
      ~trade:
        {
          sim with
          D.Venue.Trade.cancel =
            (fun _ ->
              incr deletes;
              return (Or_error.error_string "the paper host refused the key"));
          updates = stream;
        }
      f
  in
  let%bind _, resting =
    D.Oms.propose oms (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 50)
  in
  let%bind () = pump f in
  Pipe.close to_desk;
  let%bind () = settle () in
  Alcotest.(check (pair string int))
    "stopped, one DELETE" ("stopped", 1)
    (switch_of oms, !deletes);
  let at s =
    let%bind () =
      Time_source.advance_by_alarms ~wait_for:settle f.clock
        ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec s))
    in
    settle ()
  in
  let%bind () = at 2.0 in
  Alcotest.(check int) "at 2 s, sent again" 2 !deletes;
  let%bind () = at 12.0 in
  Alcotest.(check int) "at 12 s" 3 !deletes;
  let%bind () = at 42.0 in
  Alcotest.(check int) "at 42 s, the last" 4 !deletes;
  Alcotest.(check bool)
    "and said so" true
    (Queue.exists f.events ~f:(fun line ->
         String.is_substring line ~substring:"no retry is left"));
  Alcotest.(check (pair string string))
    "still pending_cancel, still resting at the venue, still stopped"
    ("pending_cancel", "new")
    (state f resting, resting_at f resting);
  Alcotest.(check string) "stopped" "stopped" (switch_of oms);
  Graph.destroy f.graph;
  return ()

(* The restart after that stop (fix round 1). The journal says pending_cancel
   and the venue still has the order resting: no DELETE ever reached it. And
   while nobody listened, 20 of its 50 filled at 99: the venue's lookup says
   partially_filled, 20 at 99, still working the rest. The restart's
   reconciliation recovers the fill first, which moves the order to
   partially_filled, so the cancel is asked for again, journaled as one --
   pending_cancel, the 20 kept -- and the DELETE is sent again by the venue's
   id. The first answer is not a confirmation, so the next reconciliation
   that finds it resting sends it again; that one the venue takes, though its
   reports still say it rests until it acts, so a third reconciliation sends
   nothing -- one DELETE per venue id. When the venue acts, the restart's
   stream reports the order cancelled. *)
let test_a_restart_sends_again_a_cancel_that_never_arrived () =
  let f = fixture () in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let stream, to_desk = forwarded sim in
  let refused = Or_error.error_string "the paper host refused the key" in
  let stopped =
    manager
      ~trade:
        { sim with D.Venue.Trade.cancel = (fun _ -> return refused); updates = stream }
      f
  in
  let%bind _, resting =
    D.Oms.propose stopped (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 50)
  in
  let%bind () = pump f in
  Pipe.close to_desk;
  let%bind () = settle () in
  Alcotest.(check (pair string string))
    "stopped; pending_cancel on the desk, new at the venue" ("pending_cancel", "new")
    (state f resting, resting_at f resting);
  (* The restart, with the key fixed. The simulated venue fills whole orders
     only, so the partial fill is the lookup's answer, written by hand. *)
  let venue = D.Sim_venue.trade ~auto:false f.venue in
  let partly (v : D.Venue.Venue_order.t) =
    {
      v with
      D.Venue.Venue_order.status = "partially_filled";
      filled_qty = 20.0;
      filled_avg_price = Some 99.0;
    }
  in
  let deletes = ref [] and answers = ref [ refused; Ok () ] in
  let restarted =
    manager
      ~trade:
        {
          venue with
          D.Venue.Trade.cancel =
            (fun id ->
              deletes := id :: !deletes;
              match !answers with
              | answer :: rest ->
                  answers := rest;
                  return answer
              | [] -> return (Ok ()));
          find_order =
            (fun c ->
              let%map found = venue.D.Venue.Trade.find_order c in
              Or_error.map found ~f:(Option.map ~f:partly));
        }
      f
  in
  let%bind () = D.Oms.reconcile restarted in
  let%bind () = settle () in
  Alcotest.(check (list string)) "the restart sends it again" [ "sim-1" ] !deletes;
  Alcotest.(check (pair string (float 1e-9)))
    "the fill recovered, and the cancel asked for again: pending_cancel, 20 kept"
    ("pending_cancel", 20.0)
    (state f resting, (journaled f resting).D.Order.filled_qty);
  let%bind () = D.Oms.reconcile restarted in
  let%bind () = settle () in
  Alcotest.(check (list string))
    "not a confirmation, so the next finding sends it again" [ "sim-1"; "sim-1" ] !deletes;
  let%bind () = D.Oms.reconcile restarted in
  let%bind () = settle () in
  Alcotest.(check int) "confirmed once: nothing more" 2 (List.length !deletes);
  Alcotest.(check string)
    "still pending_cancel until the venue acts" "pending_cancel" (state f resting);
  let%bind (_ : unit Or_error.t) = venue.D.Venue.Trade.cancel "sim-1" in
  let%bind () = pump f in
  Alcotest.(check (pair string (float 1e-9)))
    "and then cancelled, the 20 kept" ("cancelled", 20.0)
    (state f resting, (journaled f resting).D.Order.filled_qty);
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
   restart's questions about it error, and it goes onto the same schedule,
   whose first lookup finds it. The restart reconciles twice, as a live start
   does -- once at start and once when the update stream connects -- and the
   second starts no second schedule for an order already on one. Nothing was
   sent twice. The three
   that never hear back get update pipes of their own that nothing writes to
   and nothing closes, so none can take the venue's updates from the restart
   -- and none stops its desk, as a stream that ended would (fix round 1). *)
let test_a_restart_reconciles () =
  let f = fixture () in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let answer_lost =
    {
      sim with
      D.Venue.Trade.submit =
        (fun permit r ->
          don't_wait_for (Deferred.ignore_m (sim.D.Venue.Trade.submit permit r));
          Deferred.never ());
    }
  in
  let never_sent =
    { answer_lost with D.Venue.Trade.submit = (fun _ _ -> Deferred.never ()) }
  in
  let quiet (trade : _ D.Venue.Trade.t) =
    { trade with D.Venue.Trade.updates = fst (Pipe.create ()) }
  in
  let first = manager ~trade:(quiet answer_lost) f
  and second = manager ~trade:(quiet never_sent) f
  and third = manager ~trade:(quiet answer_lost) f in
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
     symbol. XOM's first two lookups -- the two reconciliations' -- error, as a
     request that timed out would. *)
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
          if Symbol.equal symbol xom && lookups_of xom <= 2 then
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
  (* The second reconciliation, the stream's: AAPL is filled and out of the
     open set, so it asks about MSFT and XOM once each -- a miss and an error
     again -- and both are already on the schedule, so it starts no more. *)
  let%bind () = D.Oms.reconcile restarted in
  let%bind () = settle () in
  Alcotest.(check (list (pair string string)))
    "a second reconciliation changes no state"
    [ ("AAPL", "filled"); ("MSFT", "submit_unknown"); ("XOM", "submit_unknown") ]
    (states ());
  Alcotest.(check (triple int int int))
    "lookups after the second, at t0" (1, 2, 2) (looked_up ());
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
  (* AAPL 1: found at t0, never scheduled. MSFT 2 + 2: t0 twice, then one
     schedule's 2 s and 12 s -- a second schedule would have asked at each
     again. XOM 2 + 1: t0 twice (the errors), 2 s (found), and a found order
     leaves the schedule. *)
  Alcotest.(check (triple int int int)) "lookups at 41 s" (1, 4, 3) (looked_up ());
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 42.0))
  in
  let%bind () = settle () in
  Alcotest.(check (list (pair string string)))
    "at 42 s the last lookup misses: failed"
    [ ("AAPL", "filled"); ("MSFT", "failed"); ("XOM", "submitted") ]
    (states ());
  (* MSFT 2 + 3: t0 twice, 2 s, 12 s, 42 s; the others unchanged *)
  Alcotest.(check (triple int int int)) "lookups at 42 s" (1, 5, 3) (looked_up ());
  (* still AAPL and XOM alone *)
  Alcotest.(check int) "and nothing was sent again" 2 (D.Sim_venue.received f.venue);
  Graph.destroy f.graph;
  return ()

(* Invariant 12, after the arrival quote. [propose] runs the rules and the
   gate, then fetches the quote while it holds the sequencer, and a book that
   was the account's when the rules ran can age past the sync window before
   the quote answers. So the book is asked again once the quote is in. Here it
   is current when the proposal starts -- the rules pass, which is the only
   way the quote is asked for at all -- and stops being so while the quote is
   in flight. The order is refused by the trading rule, and nothing reaches
   the venue. *)
let test_a_book_that_goes_stale_while_the_quote_is_fetched_is_refused () =
  let f = fixture () in
  let current = ref true in
  let asked = Ivar.create () and answer = Ivar.create () in
  let venue_read = D.Sim_venue.read f.venue in
  let read =
    {
      venue_read with
      D.Venue.Read.latest_quote =
        (fun symbol ->
          Ivar.fill_if_empty asked ();
          let%bind () = Ivar.read answer in
          venue_read.D.Venue.Read.latest_quote symbol);
    }
  in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let submitted = ref 0 in
  let counted =
    {
      sim with
      D.Venue.Trade.submit =
        (fun permit r ->
          incr submitted;
          sim.D.Venue.Trade.submit permit r);
    }
  in
  let oms = manager ~trade:counted ~read ~book_is_current:(fun () -> !current) f in
  let proposal = D.Oms.propose oms (ticket aapl D.Order.Side.Buy 10) in
  let%bind () = Ivar.read asked in
  current := false;
  Ivar.fill_exn answer ();
  let%bind p, o = proposal in
  let%bind () = settle () in
  Alcotest.(check string) "refused" "rejected_pre_trade" (state f o);
  Alcotest.(check (list string)) "by the trading rule" [ "trading" ] (rules p);
  Alcotest.(check bool)
    "saying the book may not be the account's" true
    (List.exists (D.Oms.Preview.reasons p)
       ~f:(String.is_substring ~substring:"recent read of the account"));
  Alcotest.(check (pair int int))
    "nothing submitted, and the venue received nothing" (0, 0)
    (!submitted, D.Sim_venue.received f.venue);
  Graph.destroy f.graph;
  return ()

(* An order the desk declared failed, which the venue holds. The venue takes
   two limit buys that rest -- AAPL 10 at 99 (the ask is 100 x 1.0005 =
   100.05) and XOM 20 at 49 (the ask is 50 x 1.0005 = 50.025) -- but both
   answers are lost and every lookup misses, so at t0 + 2 + 10 + 30 = 42 s
   the desk declares both failed. Then the venue speaks: AAPL's order is
   reported resting on the stream with the switch clear, and XOM's is found
   resting by a reconciliation under a halt by hand. The desk has given up on
   both, so nothing else would ever manage them; each is cancelled at once by
   the venue's id, whatever the switch reads, and the log says so. A cancel
   is not a resend: the venue received two orders and still has two. A late
   copy of XOM's report on the stream, still saying new, and a second
   reconciliation send nothing more -- exactly one DELETE each. The venue
   numbers orders as it receives them, so AAPL's is sim-1 and XOM's sim-2.
   The desk hears only what the case writes on its stream: the venue's own
   updates go to a pipe nobody reads. *)
let test_a_failed_order_the_venue_reports_resting_is_cancelled () =
  let f = fixture () in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let stream, to_desk = Pipe.create () in
  let deletes = ref [] in
  let sent id = List.count !deletes ~f:(String.equal id) in
  let blind =
    {
      sim with
      D.Venue.Trade.submit =
        (fun permit r ->
          let%map (_ : D.Venue.Submission.t) = sim.D.Venue.Trade.submit permit r in
          D.Venue.Submission.Unknown "timed out after 10 s");
      find_order = (fun _ -> return (Ok None));
      cancel =
        (fun id ->
          deletes := id :: !deletes;
          sim.D.Venue.Trade.cancel id);
      updates = stream;
    }
  in
  let oms = manager ~trade:blind f in
  let%bind _, a = D.Oms.propose oms (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 10) in
  let%bind _, x =
    D.Oms.propose oms
      (ticket ~kind:(D.Order.Kind.Limit (Price.of_float 49.0)) xom D.Order.Side.Buy 20)
  in
  (* 14:00:00 + 42 s: the last of the 2, 10 and 30 s lookups misses *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 42.0))
  in
  let%bind () = settle () in
  Alcotest.(check (pair string string))
    "both declared failed" ("failed", "failed")
    (state f a, state f x);
  Alcotest.(check int) "though the venue received both" 2 (D.Sim_venue.received f.venue);
  let at_venue o =
    Option.value_exn
      (D.Sim_venue.find_now f.venue o.D.Order.request.D.Order.Request.client_order_id)
  in
  let report (v : D.Venue.Venue_order.t) =
    Pipe.write_without_pushback to_desk
      { D.Venue.Update.event = "new"; order = v; fill = None; at = Time_ns.now () }
  in
  let deletes_of_both () = (sent "sim-1", sent "sim-2") in
  (* AAPL's, on the stream, the switch clear *)
  report (at_venue a);
  let%bind () = settle () in
  Alcotest.(check (pair int int)) "one DELETE, for sim-1" (1, 0) (deletes_of_both ());
  Alcotest.(check (option string))
    "by the venue id the desk kept" (Some "sim-1") (journaled f a).D.Order.venue_order_id;
  Alcotest.(check string) "still failed on the desk" "failed" (state f a);
  Alcotest.(check string)
    "and cancelled at the venue" "canceled" (at_venue a).D.Venue.Venue_order.status;
  (* XOM's, by a reconciliation, under a halt by hand. Its report as the venue
     made it before the cancel is kept for the late copy below. The halt is
     set on the switch itself, not by [Oms.kill], whose own cancels look for
     failed orders too (the cases after this one): here the reconciliation
     is what finds it. *)
  let before_the_cancel = at_venue x in
  D.Halt.halt (D.Oms.halt oms) ~why:"testing the switch" ~at:t0;
  let%bind () = D.Oms.reconcile oms in
  let%bind () = settle () in
  Alcotest.(check (pair int int)) "one DELETE, for sim-2" (1, 1) (deletes_of_both ());
  Alcotest.(check (option string))
    "by the venue id the desk kept" (Some "sim-2") (journaled f x).D.Order.venue_order_id;
  Alcotest.(check string)
    "and cancelled at the venue" "canceled" (at_venue x).D.Venue.Venue_order.status;
  report before_the_cancel;
  let%bind () = settle () in
  let%bind () = D.Oms.reconcile oms in
  let%bind () = settle () in
  Alcotest.(check (pair int int))
    "a late report and a second reconciliation send nothing more" (1, 1)
    (deletes_of_both ());
  Alcotest.(check int) "and nothing was sent again" 2 (D.Sim_venue.received f.venue);
  Alcotest.(check int)
    "one line for each, saying the desk cancels what it declared failed" 2
    (Queue.count f.events ~f:(fun line ->
         String.is_substring line ~substring:"declared failed"
         && String.is_substring line ~substring:"cancelling it"));
  Graph.destroy f.graph;
  return ()

(* A failed order the venue still works, and nothing else: a limit buy of 10
   AAPL at 99 that rests at the venue as sim-1, whose answer was lost and
   whose every lookup missed, so at t0 + 42 s the desk declared it failed.
   The venue never reports it on the stream, and no reconciliation runs. The
   stream is [to_desk], which the case may close; the venue's own updates go
   to a pipe nobody reads. *)
let failed_but_resting () =
  let f = fixture () in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let stream, to_desk = Pipe.create () in
  let deletes = ref [] in
  let blind =
    {
      sim with
      D.Venue.Trade.submit =
        (fun permit r ->
          let%map (_ : D.Venue.Submission.t) = sim.D.Venue.Trade.submit permit r in
          D.Venue.Submission.Unknown "timed out after 10 s");
      find_order = (fun _ -> return (Ok None));
      cancel =
        (fun id ->
          deletes := id :: !deletes;
          sim.D.Venue.Trade.cancel id);
      updates = stream;
    }
  in
  let oms = manager ~trade:blind f in
  let%bind _, a = D.Oms.propose oms (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 10) in
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 42.0))
  in
  let%map () = settle () in
  Alcotest.(check (pair string string))
    "declared failed, and resting at the venue" ("failed", "new")
    (state f a, resting_at f a);
  (f, oms, a, deletes, to_desk)

(* A kill cancels it: the open set cannot name a failed order, so the kill's
   cancels ask the venue's own list of its open orders for any this desk
   declared failed (Oms.cancel_all), and DELETE it by the venue's id. A
   market-on-open order the venue holds would otherwise rest some fourteen
   hours, to the auction, with the switch set. *)
let test_a_kill_cancels_a_failed_order_the_venue_still_works () =
  let%bind f, oms, a, deletes, _ = failed_but_resting () in
  let%bind () = D.Oms.kill oms ~why:"testing the switch" in
  let%bind () = settle () in
  Alcotest.(check (list string)) "one DELETE, for sim-1" [ "sim-1" ] !deletes;
  Alcotest.(check (pair string string))
    "cancelled at the venue; failed on the desk" ("canceled", "failed")
    (resting_at f a, state f a);
  Alcotest.(check (option string))
    "by the venue id the desk now keeps" (Some "sim-1")
    (journaled f a).D.Order.venue_order_id;
  Graph.destroy f.graph;
  return ()

(* And the engine's stop, when the stream ends, cancels it the same way. *)
let test_the_engine's_stop_cancels_a_failed_order_the_venue_still_works () =
  let%bind f, oms, a, deletes, to_desk = failed_but_resting () in
  Pipe.close to_desk;
  let%bind () = settle () in
  Alcotest.(check string) "stopped" "stopped" (switch_of oms);
  Alcotest.(check (list string)) "one DELETE, for sim-1" [ "sim-1" ] !deletes;
  Alcotest.(check (pair string string))
    "cancelled at the venue; failed on the desk" ("canceled", "failed")
    (resting_at f a, state f a);
  Graph.destroy f.graph;
  return ()

(* The engine's stop when a cancel raises rather than answering. Two orders
   rest at the venue: AAPL 50 at 99, open on the desk as sim-1, and XOM 20
   at 49, sim-2, whose answer was lost and whose every lookup missed, so at
   t0 + 42 s the desk declared it failed. The stream ends; the DELETE of
   sim-1 raises. The stop's cancels run under a monitor of their own, so the
   raise reaches the log, naming what raised, and not the process; the
   switch reads stopped; the line that says the cancels were asked for is
   not written -- and the failed order is still looked for and cancelled,
   because a raise among the open orders' cancels does not end the sweep. *)
let test_a_stop_whose_cancel_raises_says_so_and_stays_stopped () =
  let f = fixture () in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  (* The venue's updates reach the desk, but none about XOM's order: its
     answer, and every word of it, is lost. *)
  let stream, to_desk = Pipe.create () in
  don't_wait_for
    (Pipe.iter_without_pushback sim.D.Venue.Trade.updates ~f:(fun u ->
         if not (Symbol.equal u.D.Venue.Update.order.D.Venue.Venue_order.symbol xom) then
           Pipe.write_without_pushback_if_open to_desk u));
  let deletes = ref [] in
  let oms =
    manager
      ~trade:
        {
          sim with
          D.Venue.Trade.submit =
            (fun permit r ->
              if Symbol.equal r.D.Order.Request.symbol xom then
                let%map (_ : D.Venue.Submission.t) = sim.D.Venue.Trade.submit permit r in
                D.Venue.Submission.Unknown "timed out after 10 s"
              else sim.D.Venue.Trade.submit permit r);
          find_order = (fun _ -> return (Ok None));
          cancel =
            (fun id ->
              if String.equal id "sim-1" then failwith "the DELETE raised"
              else (
                deletes := id :: !deletes;
                sim.D.Venue.Trade.cancel id));
          updates = stream;
        }
      f
  in
  let%bind _, resting =
    D.Oms.propose oms (ticket ~kind:limit_at_99 aapl D.Order.Side.Buy 50)
  in
  let%bind _, failed =
    D.Oms.propose oms
      (ticket ~kind:(D.Order.Kind.Limit (Price.of_float 49.0)) xom D.Order.Side.Buy 20)
  in
  let%bind () = pump f in
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 42.0))
  in
  let%bind () = settle () in
  Alcotest.(check (pair string string))
    "one resting, one failed" ("accepted", "failed")
    (state f resting, state f failed);
  Pipe.close to_desk;
  let%bind () = settle () in
  let said words =
    Queue.exists f.events ~f:(fun line -> String.is_substring line ~substring:words)
  in
  Alcotest.(check string) "the switch reads stopped" "stopped" (switch_of oms);
  Alcotest.(check (triple bool bool bool))
    "the raise, logged with its words; no line saying the cancels were asked for"
    (true, true, false)
    ( said "the engine's stop's cancels raised",
      said "the DELETE raised",
      said "the engine's stop has asked the venue to cancel" );
  Alcotest.(check (pair (list string) string))
    "and the failed order still cancelled" ([ "sim-2" ], "canceled")
    (!deletes, resting_at f failed);
  Graph.destroy f.graph;
  return ()

(* [Oms.run] stops the desk before it says the stream ended: a log line that
   raises must not keep the switch from being set. Here the line saying the
   stream has ended raises; [run] returns that raise, and the switch reads
   stopped all the same. *)
let test_the_stream's_end_stops_the_desk_before_the_line_that_says_so () =
  let f = fixture () in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let stream, to_desk = forwarded sim in
  let on_event line =
    if String.is_substring line ~substring:"stream of order updates has ended" then
      failwith "the log raised"
  in
  let oms =
    manager ~run:false ~on_event ~trade:{ sim with D.Venue.Trade.updates = stream } f
  in
  let ran = Monitor.try_with ~extract_exn:true (fun () -> D.Oms.run oms) in
  Pipe.close to_desk;
  let%bind () = settle () in
  let%bind ran = ran in
  Alcotest.(check bool) "the line raised, out of run" true (Result.is_error ran);
  Alcotest.(check string) "and the switch reads stopped" "stopped" (switch_of oms);
  Graph.destroy f.graph;
  return ()

(* A clock read that fails after one that answered: the session it last
   described is not assumed to go on, so the session reads closed
   ([Oms.refresh] sets no clock at all), and the log says why. The first read
   answers, and an order passes the session rule; the second fails, and the
   same order is refused by it. *)
let test_a_clock_read_that_fails_clears_the_session () =
  let f = fixture () in
  let answers = ref [ true; false ] in
  let venue_read = D.Sim_venue.read f.venue in
  let read =
    {
      venue_read with
      D.Venue.Read.clock =
        (fun () ->
          match !answers with
          | true :: rest ->
              answers := rest;
              venue_read.D.Venue.Read.clock ()
          | _ -> return (Or_error.error_string "HTTP 503 from the clock"));
    }
  in
  let oms = manager ~read f in
  let refused () = rules (D.Oms.preview oms (ticket aapl D.Order.Side.Buy 1)) in
  let%bind () = D.Oms.refresh oms in
  Alcotest.(check (list string)) "a read that answered: open" [] (refused ());
  let%bind () = D.Oms.refresh oms in
  Alcotest.(check (list string))
    "a read that failed: the session rule refuses" [ "session" ] (refused ());
  Alcotest.(check bool)
    "and the log says why" true
    (Queue.exists f.events ~f:(fun line ->
         String.is_substring line
           ~substring:"the venue's clock is unavailable, so the session reads closed"));
  Graph.destroy f.graph;
  return ()

(* The session rule reads the venue's clock at the order's own time (the main
   suite's cases), so a time past the close the clock named reads closed
   until the clock is read again: past it, the clock says nothing about the
   session after. So the manager reads the clock again as the session it
   described ends, not up to a minute later. The simulated venue's sessions
   follow one another with no gap -- without this the demo would refuse its
   own orders between each close and the next refresh. This venue opened its
   first five-minute session 270 s before t0, so that session closes at
   t0 + 30 s and the next at t0 + 330 s; the manager refreshes every minute,
   on the case's clock, and reads the time from it too. *)
let test_the_clock_is_read_again_when_its_session_ends () =
  let f = fixture () in
  let now () = Time_source.now f.clock in
  let venue =
    D.Sim_venue.create ~latency:Time_ns.Span.zero
      ~opened_at:(Time_ns.sub t0 (Time_ns.Span.of_sec 270.0))
      ~marks:(fun s -> Option.map (Hashtbl.find f.marks s) ~f:Price.of_float)
      ~now
      ~half_spread_bps:(fun _ -> 5.0)
      ~cash:(Notional.of_float 1_000_000.0)
      ~positions:[] ()
  in
  let reads = ref 0 in
  let venue_read = D.Sim_venue.read venue in
  let read =
    {
      venue_read with
      D.Venue.Read.clock =
        (fun () ->
          incr reads;
          venue_read.D.Venue.Read.clock ());
    }
  in
  let oms = manager ~read ~now f in
  let refused () = rules (D.Oms.preview oms (ticket aapl D.Order.Side.Buy 1)) in
  let at span =
    Time_source.advance_by_alarms ~wait_for:settle f.clock ~to_:(Time_ns.add t0 span)
  in
  don't_wait_for (D.Oms.refresh_forever oms ~every:(Time_ns.Span.of_min 1.0));
  let%bind () = settle () in
  Alcotest.(check (pair int (list string)))
    "t0: one read, open" (1, [])
    (!reads, refused ());
  (* 14:00:30 - 1 ns: the first session has not closed *)
  let%bind () = at Time_ns.Span.(of_sec 30.0 - nanosecond) in
  let%bind () = settle () in
  Alcotest.(check (pair int (list string)))
    "1 ns short of its close: still one read, open" (1, [])
    (!reads, refused ());
  (* 14:00:30: the close, and the clock read again at once *)
  let%bind () = at (Time_ns.Span.of_sec 30.0) in
  let%bind () = settle () in
  Alcotest.(check (pair int (list string)))
    "at the close: read again, and the next session open" (2, [])
    (!reads, refused ());
  (* 14:01:30: a minute after, as every minute -- not a read per cycle *)
  let%bind () = at (Time_ns.Span.of_sec 90.0) in
  let%bind () = settle () in
  Alcotest.(check (pair int (list string)))
    "a minute later: the third read" (3, [])
    (!reads, refused ());
  Graph.destroy f.graph;
  return ()

(* Preview and propose share one function for the gate's verdict --
   [propose] computes it by calling [preview], and refuses whenever
   [Preview.passed] does not hold -- so a resting order the gate must now
   count (Task 3) cannot make the two disagree: this checks that under a real
   order, actually submitted and resting at the venue, not merely one
   fixture-injected.

   tech-cap is lowered to 40,000 for this case: the fixture's own 100,000 is
   too wide for two orders to breach without either alone crossing the desk's
   25,000 per-order notional cap. AAPL 220 x $100 = $22,000 is proposed and
   rests (the venue is never pumped, so nothing here fills): alone, TECH
   would be 22,000, under both the order cap and tech-cap. MSFT 110 x $200 =
   $22,000 is proposed next: alone it would also be 22,000, under both caps,
   but with the resting AAPL order counted the gate sees 44,000, over the
   40,000 cap. A fresh [preview] of that same ticket refuses it first,
   naming tech-cap, and [propose] refuses it exactly the same way. *)
let test_preview_and_propose_agree_about_a_resting_order () =
  let tech_cap_40k =
    {
      Limit.name = "tech-cap";
      scope = Limit.Sector tech;
      kind = Limit.Gross_notional (Notional.of_float 40_000.0);
    }
  in
  let f = fixture ~limits:[ tech_cap_40k ] () in
  let oms = manager f in
  let limit_100 = D.Order.Kind.Limit (Price.of_float 100.0) in
  let limit_200 = D.Order.Kind.Limit (Price.of_float 200.0) in
  let%bind _, resting =
    D.Oms.propose oms (ticket ~kind:limit_100 aapl D.Order.Side.Buy 220)
  in
  Alcotest.(check bool)
    "the first rests" true
    (not (D.Order.State.is_terminal resting.D.Order.state));
  let ticket2 = ticket ~kind:limit_200 msft D.Order.Side.Buy 110 in
  let preview = D.Oms.preview oms ticket2 in
  let if_buys = D.Oms.Scenario.describe D.Oms.Scenario.Resting_buys in
  Alcotest.(check (list string))
    "preview: by tech-cap, if the resting buy fills" [ "tech-cap" ]
    (match preview.D.Oms.Preview.gate with
    | Some (Ok s) -> (
        match D.Oms.Scenarios.first_failure s with
        | Some (scenario, v)
          when D.Oms.Scenario.equal scenario D.Oms.Scenario.Resting_buys ->
            List.map v.Ohcamel.Gate.Verdict.created ~f:(fun m ->
                m.Ohcamel.Gate.Move.limit)
        | Some _ | None -> [])
    | Some (Error _) | None -> []);
  Alcotest.(check bool) "preview refuses it" false (D.Oms.Preview.passed preview);
  let%bind _, o = D.Oms.propose oms ticket2 in
  Alcotest.(check string) "propose refuses it too" "rejected_pre_trade" (state f o);
  (* The refusal journaled by [propose] says why, as the preview did: the
     limit, and the scenario it fails under. *)
  let reason = Option.value (journaled f o).D.Order.reason ~default:"" in
  Alcotest.(check bool)
    "and its journaled reason names tech-cap, if the resting buy fills" true
    (String.is_substring reason ~substring:"tech-cap would be breached"
    && String.is_substring reason ~substring:(sprintf "(%s)" if_buys));
  Graph.destroy f.graph;
  return ()

(* Task 4. [Desk.after_fill]'s read is [Desk.sync], and [sync_with] calls
   [on_first_sync] -- the restore, in production -- once, the first time a
   read answers. A restore that raises used to leave fill_read `Running` for
   the rest of the process: [upon (sync t) f] never runs [f] when [sync t]'s
   own deferred fails partway through its bind chain, so nothing ever set the
   flag back, and no fill after that one could ever start a read of its own.
   Monitor.protect's [finally] now runs on that path too.

   A bare desk, no order manager: [account] is gated on an Ivar so a second
   [after_fill] can land while the first read is still out, coalescing to
   `Owed` exactly as it would under an ordinary, non-raising read (Task 2's
   own promise, kept here under a raise as well). Releasing the gate lets
   the read finish, [on_first_sync] raise once, and Monitor.protect's
   [finally] see `Owed`: it idles fill_read and starts the coalesced read at
   once, which -- [synced] having already been set to true before the raise
   -- runs [on_first_sync] no second time and so does not raise again. A
   third, ordinary [after_fill] afterwards is the plainest form of "a read
   after a raising restore still runs". *)
let bare_account () =
  {
    D.Venue.Account.equity = Notional.of_float 1_000_000.0;
    cash = Notional.of_float 1_000_000.0;
    buying_power = Notional.of_float 1_000_000.0;
    last_equity = Some (Notional.of_float 1_000_000.0);
    status = "ACTIVE";
    trading_blocked = false;
    shorting_enabled = true;
  }

let test_a_raising_restore_idles_fill_read_and_a_later_read_still_runs () =
  let f = fixture () in
  let gate = Ivar.create () in
  let reads = ref 0 in
  let read : D.Venue.Read.t =
    {
      name = "test";
      account =
        (fun () ->
          incr reads;
          let%map () = Ivar.read gate in
          Ok (bare_account ()));
      positions = (fun () -> return (Ok []));
      clock = (fun () -> return (Or_error.error_string "unused in this test"));
      latest_quote = (fun _ -> return (Or_error.error_string "unused in this test"));
      daily_bars = (fun _ ~days:_ -> return (Or_error.error_string "unused in this test"));
    }
  in
  let armed = ref true in
  let desk =
    D.Desk.create ~graph:f.graph ~journal:f.journal ~venue:(D.Desk.Reads read)
      ~spec:Desk_spec.default ~on_change:ignore ~on_first_sync:(fun () ->
        if !armed then (
          armed := false;
          failwith "boom: the restore raised"))
  in
  let owed_while_out = ref false in
  let%bind raised =
    Monitor.try_with ~extract_exn:true (fun () ->
        D.Desk.after_fill desk;
        (* the fill this read was for; landing again while it is still out *)
        D.Desk.after_fill desk;
        owed_while_out := Poly.equal desk.D.Desk.fill_read `Owed;
        Ivar.fill_exn gate ();
        Scheduler.yield_until_no_jobs_remain ())
  in
  Alcotest.(check bool)
    "a fill during the raising read is coalesced to Owed, same as any other read" true
    !owed_while_out;
  (match raised with
  | Error exn ->
      Alcotest.(check bool)
        "the restore's own exception reached here, not swallowed" true
        (String.is_substring (Exn.to_string exn) ~substring:"boom: the restore raised")
  | Ok () -> Alcotest.fail "the raising restore did not raise");
  Alcotest.(check bool)
    "fill_read is idle, not stuck Running for the life of the process" true
    (Poly.equal desk.D.Desk.fill_read `Idle);
  Alcotest.(check int)
    "the read that raised, and the one the coalesced fill was owed" 2 !reads;
  D.Desk.after_fill desk;
  let%bind () = Scheduler.yield_until_no_jobs_remain () in
  Alcotest.(check int) "an ordinary read afterwards still runs" 3 !reads;
  Alcotest.(check bool) "and idles again" true (Poly.equal desk.D.Desk.fill_read `Idle);
  Graph.destroy f.graph;
  return ()

(* ---- Task 13a, fix round: [Desk.refresh_clock] (ruling 20, B2) ---------- *)

(* [Desk.refresh_clock] is the Deferred half of the market-clock block: the
   only caller is bin/main.ml's staleness timer, which starts it and does not
   wait for it. The main suite hands readings over with [Desk.set_clock], a
   plain setter, so until these three cases the whole of [refresh_clock] could
   be replaced with [Deferred.unit] and every test stayed green -- including
   the three behaviours the code's own comments call load-bearing. They need a
   scheduler, so they live here.

   [Venue.Read.clock] is a plain closure, so each case substitutes one: a read
   that fails on its second call, a read that does not answer until the case
   releases an Ivar, and no read at all (a disabled venue). No network, no
   latency, no wall-clock wait. *)

(* A Monday's session on the New York venue, in UTC. 2026-09-14 is a Monday --
   2026-01-01 is a Thursday (2024-01-01 was a Monday, 2024 is a leap year so
   2025-01-01 is a Wednesday, and 2025 has 365 days so 2026-01-01 is a
   Thursday), and 2026-09-14 is 256 days later (31 + 28 + 31 + 30 + 31 + 30 +
   31 + 31 = 243 days to 2026-09-01, plus 13), with 256 = 36 x 7 + 4, so
   Thursday + 4 = Monday. The regular session runs 09:30-16:00 ET =
   13:30-20:00 UTC in daylight time, so at t0 (14:00 UTC) the market is open,
   this session closes at 20:00 UTC and the next opens Tuesday at 13:30. The
   same reading as test/test_desk.ml's [monday_session]. *)
let monday_session =
  {
    D.Venue.Session_clock.now = t0;
    is_open = true;
    next_open = Time_ns.of_string_with_utc_offset "2026-09-15T13:30:00Z";
    next_close = Time_ns.of_string_with_utc_offset "2026-09-14T20:00:00Z";
    next_close_date = Date.of_string "2026-09-14";
  }

(* A read half whose clock is the case's and whose other four answers are
   errors: [refresh_clock] reads the clock and nothing else, and a case that
   accidentally drove [sync] would say so here instead of passing quietly. *)
let clock_only_read clock : D.Venue.Read.t =
  {
    name = "test";
    account = (fun () -> return (Or_error.error_string "unused in this test"));
    positions = (fun () -> return (Or_error.error_string "unused in this test"));
    clock;
    latest_quote = (fun _ -> return (Or_error.error_string "unused in this test"));
    daily_bars = (fun _ ~days:_ -> return (Or_error.error_string "unused in this test"));
  }

let clock_desk f venue =
  D.Desk.create ~graph:f.graph ~journal:f.journal ~venue ~spec:Desk_spec.default
    ~on_change:ignore ~on_first_sync:ignore

let clock_block json = Yojson.Safe.Util.member "clock" json

let clock_field json key =
  match Yojson.Safe.Util.member key (clock_block json) with
  | `String s -> s
  (* Never raises, so a block that has gone null under a mutation fails with a
     readable message instead of a Yojson type error. *)
  | other -> Yojson.Safe.to_string other

(* "A FAILED READ LEAVES THIS ALONE" (desk/desk.ml's comment on the field) --
   the headline contract of the whole block. A reading does not become wrong
   when the next request fails, it becomes old, and the block says so in a
   word while keeping every value the venue gave. Turning [| Error _ -> ()]
   into [| Error _ -> t.clock <- None] would make one venue hiccup publish
   "unknown", on which Task 18's watch.sh skips both of its market-state
   checks -- the exact false alarm this field exists to avoid.

   The venue answers once and fails afterwards. The stamp is the host's wall
   clock at the moment the answer landed, so the ages this case means are
   stated relative to that stamp and not to a fixed time: at the stamp itself
   the age is zero, and at the stamp + 901 s it is past the fifteen-minute
   freshness window (900 s) and reads "stale". *)
let test_a_failed_clock_read_keeps_the_reading_it_had_which_ages_to_stale () =
  let f = fixture () in
  let reads = ref 0 in
  let read =
    clock_only_read (fun () ->
        incr reads;
        return
          (if !reads = 1 then Ok monday_session
           else Or_error.error_string "simulated: the venue's clock did not answer"))
  in
  let desk = clock_desk f (D.Desk.Reads read) in
  let%bind () = D.Desk.refresh_clock desk in
  Alcotest.(check int) "the first refresh took a read" 1 !reads;
  let read_at = snd (Option.value_exn desk.D.Desk.clock) in
  let fresh = D.Desk.summary_json ~now:read_at desk in
  Alcotest.(check string) "which answered" "venue-clock" (clock_field fresh "source");
  Alcotest.(check string)
    "the venue's own next close" "2026-09-14T20:00:00.000000000Z"
    (clock_field fresh "next_close");
  (* The failing read. *)
  let%bind () = D.Desk.refresh_clock desk in
  Alcotest.(check int) "the second refresh took a read too, and it failed" 2 !reads;
  Alcotest.(check string)
    "a failed read leaves the whole published block alone"
    (Yojson.Safe.to_string (clock_block fresh))
    (Yojson.Safe.to_string (clock_block (D.Desk.summary_json ~now:read_at desk)));
  Alcotest.(check bool)
    "including its stamp, which is still the successful read's" true
    (Option.value_map desk.D.Desk.clock ~default:false ~f:(fun (_, at) ->
         Time_ns.equal at read_at));
  (* And it ages, rather than disappearing: 901 s past the stamp is past the
     fifteen-minute window, so the word changes and the values do not. *)
  let old =
    D.Desk.summary_json ~now:(Time_ns.add read_at (Time_ns.Span.of_sec 901.0)) desk
  in
  Alcotest.(check string)
    "at 901 s the reading is stale" "stale" (clock_field old "source");
  Alcotest.(check string)
    "and is_open is still the venue's" "true" (clock_field old "is_open");
  Alcotest.(check string)
    "and so is the session's date" "2026-09-14"
    (clock_field old "next_close_date");
  Graph.destroy f.graph;
  return ()

(* The in-flight guard. The timer that calls [refresh_clock] every
   Config.Runtime.clock_interval (5 s) also writes the graph's [now] cell, so
   it starts the read and does not wait for it; without the guard a venue
   slower than one interval would pile a request per interval for as long as
   it stayed slow.

   The venue does not answer until this case fills the gate. The second
   refresh must start no read AND must not wait on the first -- it is called
   from a timer callback that has work of its own to finish.

   It also pins why a slow read cannot move [read_at] backwards: the stamp is
   taken where the answer lands, not where the request was sent, and the guard
   means at most one read is ever out, so two readings can only be stamped in
   the order they land. (Two reads out at once is not constructible against
   this code, which is the reviewer's own finding; what is asserted here is
   the pair of properties that makes it so.) *)
let test_a_second_clock_refresh_while_one_is_out_starts_no_second_read () =
  let f = fixture () in
  let gate = Ivar.create () in
  let reads = ref 0 in
  let read =
    clock_only_read (fun () ->
        incr reads;
        let%map () = Ivar.read gate in
        Ok monday_session)
  in
  let desk = clock_desk f (D.Desk.Reads read) in
  let first = D.Desk.refresh_clock desk in
  let%bind () = Scheduler.yield_until_no_jobs_remain () in
  Alcotest.(check int) "one read is out" 1 !reads;
  Alcotest.(check bool)
    "and the guard says so" true
    (Poly.equal desk.D.Desk.clock_read `Running);
  let second = D.Desk.refresh_clock desk in
  let%bind () = Scheduler.yield_until_no_jobs_remain () in
  Alcotest.(check int) "the second refresh started no second read" 1 !reads;
  Alcotest.(check bool)
    "and returned at once rather than waiting on the first" true
    (Deferred.is_determined second);
  Alcotest.(check bool) "the first read is still out" false (Deferred.is_determined first);
  Alcotest.(check bool)
    "nothing is cached while it is out" true
    (Option.is_none desk.D.Desk.clock);
  let before_it_landed = Time_ns.now () in
  Ivar.fill_exn gate ();
  let%bind () = first in
  let%bind () = Scheduler.yield_until_no_jobs_remain () in
  Alcotest.(check int) "still one read, and its answer landed" 1 !reads;
  Alcotest.(check bool)
    "the guard idles again, so the next interval starts a read" true
    (Poly.equal desk.D.Desk.clock_read `Idle);
  Alcotest.(check bool)
    "the stamp is where the answer landed, not where the request was sent" true
    (Option.value_map desk.D.Desk.clock ~default:false ~f:(fun (_, at) ->
         Time_ns.( >= ) at before_it_landed));
  Graph.destroy f.graph;
  return ()

(* A disabled venue -- no credentials, or a refused trading key -- has no read
   half at all, so there is nothing to ask. The refresh is a no-op: it raises
   nothing, it leaves the cache empty, and the block goes on saying "unknown"
   with five nulls. Unknown is not zero and it is not false: is_open false
   would say the market is shut, which is a claim this desk has none to make,
   and watch.sh would act on it. *)
let test_a_disabled_venue_takes_no_clock_read_and_stays_unknown () =
  let f = fixture () in
  let desk =
    clock_desk f
      (D.Desk.Unavailable
         { name = "alpaca-paper"; reason = "no credentials in this test" })
  in
  let%bind () = D.Desk.refresh_clock desk in
  let%bind () = Scheduler.yield_until_no_jobs_remain () in
  Alcotest.(check bool) "nothing was cached" true (Option.is_none desk.D.Desk.clock);
  Alcotest.(check bool)
    "and no read is out" true
    (Poly.equal desk.D.Desk.clock_read `Idle);
  let s = D.Desk.summary_json desk in
  Alcotest.(check string) "the block says unknown" "unknown" (clock_field s "source");
  Alcotest.(check (list string))
    "and carries five nulls, not zeros"
    [ "null"; "null"; "null"; "null"; "null" ]
    (List.map
       [ "is_open"; "next_open"; "next_close"; "next_close_date"; "read_at" ]
       ~f:(clock_field s));
  Graph.destroy f.graph;
  return ()

(* The intake's minute loop: a pass that raises is logged, and the next one
   still runs. The signals directory is removed before the first pass, so
   listing it raises -- the real failure of a volume that goes away -- and
   restored, with a signal in it, before the second. The loop waits on the
   case's clock: at t0 the first pass, at t0 + 60 s the second. *)
let test_a_raising_pass_is_logged_and_the_next_still_runs () =
  let clock = Time_source.create ~now:t0 () in
  let journal = Or_error.ok_exn (D.Journal.open_ ~path:":memory:") in
  D.Journal.record_session journal
    {
      D.Journal.Session.date = Date.of_string "2026-09-11";
      equity_close = 100_000.0;
      cash_close = 100_000.0;
      gross_close = 0.0;
      net_close = 0.0;
      recorded_at = t0;
    };
  let dir = Filename_unix.temp_dir "ohcamel-signals-" "" in
  let events = Queue.create () in
  let intake =
    D.Intake.create ~journal ~dir
      ~strategies:
        [
          {
            Ohcamel.Config.Book.Signals_spec.Strategy.name = "exp_a01_spy";
            symbols = [ "SPY" ];
            max_age = 3;
            sizing = Ohcamel.Config.Book.Signals_spec.Advisory;
            capital_fraction = 0.5;
            cadence = Ohcamel.Config.Book.Signals_spec.Cadence.Daily;
            adjust = Ohcamel.Config.Book.Signals_spec.Adjust.Intraday;
            max_age_minutes = 20;
            horizon = Ohcamel.Config.Book.Signals_spec.Horizon.Carry;
            stop = None;
            reinstated = None;
          };
        ]
      ~universe:[ Symbol.of_string "SPY" ]
      ~on_event:(Queue.enqueue events)
      ~now:(fun () -> Time_source.now clock)
      ()
  in
  Core_unix.rmdir dir;
  don't_wait_for (D.Intake.run ~time_source:(Time_source.read_only clock) intake);
  let%bind () = settle () in
  Alcotest.(check int)
    "the first pass raised, and said so" 1
    (Queue.count events ~f:(String.is_substring ~substring:"a pass raised"));
  Alcotest.(check bool)
    "and completed nothing" true
    (Option.is_none (D.Intake.last_pass intake));
  Core_unix.mkdir dir;
  let zeros = "sha256:" ^ String.make 64 '0' in
  Out_channel.write_all
    (Filename.concat dir "exp_a01_spy-2026-09-11.json")
    ~data:
      (sprintf
         {|{"schema_version":1,"strategy":"exp_a01_spy","params_hash":"%s","as_of":"2026-09-11","computed_at":"2026-09-11T23:15:00Z","data_hash":"%s","sequence":20260911,"validation":{"status":"pass","gates_version":"2026-09-02"},"targets":[{"symbol":"SPY","weight":1.0}]}|}
         zeros zeros);
  (* 14:00:00 + 60 s - 1 ns: the second pass has not run *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle clock
      ~to_:(Time_ns.add t0 Time_ns.Span.(of_sec 60.0 - nanosecond))
  in
  let%bind () = settle () in
  Alcotest.(check bool)
    "1 ns short of a minute: not yet judged" true
    (Option.is_none (D.Journal.signal journal ~strategy:"exp_a01_spy" ~sequence:20260911));
  (* 14:01:00 *)
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 60.0))
  in
  let%bind () = settle () in
  Alcotest.(check (option string))
    "a minute on, the loop ran again and judged the file" (Some "advisory")
    (Option.map (D.Journal.signal journal ~strategy:"exp_a01_spy" ~sequence:20260911)
       ~f:(fun s -> D.Journal.Signal.Verdict.to_string s.D.Journal.Signal.verdict));
  Alcotest.(check (option string))
    "and its pass completed at 14:01"
    (Some (D.Desk_time.rfc3339 (Time_ns.add t0 (Time_ns.Span.of_sec 60.0))))
    (Option.map (D.Intake.last_pass intake) ~f:D.Desk_time.rfc3339);
  Array.iter (Sys_unix.readdir dir) ~f:(fun name ->
      Core_unix.unlink (Filename.concat dir name));
  Core_unix.rmdir dir;
  D.Journal.close journal;
  return ()

(* ------------------------------------------------------------------------ *)
(* Task 15: the rebalance, market-on-open, and the re-check after the quote  *)
(* ------------------------------------------------------------------------ *)

(* Monday 14 September 2026's close, recorded at the fixture's prints --
   AAPL 100, MSFT 200, XOM 50 -- and the evening after it at 19:15 EDT
   (23:15Z), when the opening auction's window is open until Tuesday 09:28
   EDT (13:28Z). *)
let evening = Time_ns.of_string_with_utc_offset "2026-09-14T23:15:00Z"
let tuesday_open = Time_ns.of_string_with_utc_offset "2026-09-15T13:30:00Z"
let tuesday_close = Time_ns.of_string_with_utc_offset "2026-09-15T20:00:00Z"

let record_close ?(sessions = [ "2026-09-14" ]) f =
  List.iter sessions ~f:(fun d ->
      D.Journal.record_marks f.journal
        (List.map
           [ (aapl, 100.0); (msft, 200.0); (xom, 50.0) ]
           ~f:(fun (symbol, close) ->
             { D.Journal.Mark.date = Date.of_string d; symbol; close; qty = 0.0 }));
      D.Journal.record_session f.journal
        {
          D.Journal.Session.date = Date.of_string d;
          equity_close = 1_000_000.0;
          cash_close = 1_000_000.0;
          gross_close = 0.0;
          net_close = 0.0;
          recorded_at = Time_ns.add evening (Time_ns.Span.of_hr (-3.0));
        })

(* The venue after Monday's close, and the manager reading its clock. *)
let after_the_close f oms =
  D.Sim_venue.set_session f.venue
    (D.Sim_venue.Session.Closed { next_open = tuesday_open; next_close = tuesday_close });
  D.Oms.set_clock oms (Some (D.Sim_venue.clock f.venue))

let rebalance_leg symbol side qty =
  {
    D.Rebalance.Leg.symbol;
    weight = 0.0;
    price = 0.0;
    target = 0;
    current = 0;
    side;
    qty;
  }

let aapl_source =
  {
    D.Rebalance.Source.strategy = "exp_a01_aapl";
    sequence = 1;
    as_of = Date.of_string "2026-09-14";
  }

(* A rebalance of one market-on-open order, AAPL +50 at Monday's close of
   100, proposed at 19:15 EDT. The venue's submit asks the journal, as it is
   called, what it holds for the order: pending_submit, with its source and
   its tif -- the journal before the wire. The venue takes it and holds it
   while the session is closed: an hour's pumping fills nothing. Then the
   session opens and one pump fills it as a market order, half a spread from
   the mark: 100 x 1.0005 = 100.05. The strategy's own fills then read AAPL
   50, which is what the next rebalance will count as its position. *)
let test_a_rebalance_journals_before_the_wire_and_fills_when_the_session_opens () =
  let f = fixture () in
  record_close f;
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let at_the_wire = ref [] in
  let watched =
    {
      sim with
      D.Venue.Trade.submit =
        (fun permit r ->
          at_the_wire :=
            Option.map (D.Journal.load_order f.journal r.D.Order.Request.client_order_id)
              ~f:(fun row ->
                ( D.Order.State.to_string row.D.Journal.Order_row.order.D.Order.state,
                  row.D.Journal.Order_row.source ))
            :: !at_the_wire;
          sim.D.Venue.Trade.submit permit r);
    }
  in
  let oms = manager ~trade:watched ~now:(fun () -> evening) f in
  after_the_close f oms;
  let%bind outcome =
    D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Open ~source:aapl_source
      ~legs:[ rebalance_leg aapl D.Order.Side.Buy 50 ]
  in
  let%bind () = settle () in
  let o =
    match outcome with
    | D.Oms.Rebalance_outcome.Sent [ o ] -> o
    | other ->
        Alcotest.failf "not one order sent: %s" (D.Oms.Rebalance_outcome.to_string other)
  in
  Alcotest.(check (list (option (pair string string))))
    "at the wire, the journal already held it pending_submit, from its signal"
    [ Some ("pending_submit", "signal:exp_a01_aapl:1") ]
    !at_the_wire;
  Alcotest.(check string)
    "journaled as market-on-open" "opg"
    (D.Order.Tif.to_string (journaled f o).D.Order.request.D.Order.Request.tif);
  Alcotest.(check string) "the venue took it" "accepted" (state f o);
  D.Sim_venue.pump f.venue;
  let%bind () = settle () in
  Alcotest.(check string) "held while the session is closed" "accepted" (state f o);
  D.Sim_venue.set_session f.venue
    (D.Sim_venue.Session.Open
       {
         next_close = tuesday_close;
         next_open = Time_ns.add tuesday_open (Time_ns.Span.of_day 1.0);
       });
  let%bind () = pump f in
  Alcotest.(check string) "filled once the session opens" "filled" (state f o);
  Alcotest.(check (option (float 1e-9)))
    "at 100 x 1.0005" (Some 100.05)
    (D.Order.avg_fill_price (journaled f o));
  Alcotest.(check (list (pair string (float 0.0))))
    "the strategy's own position: AAPL 50"
    [ ("AAPL", 50.0) ]
    (List.map (D.Journal.source_fills f.journal ~prefix:"signal:exp_a01_aapl:")
       ~f:(fun (s, q) -> (Symbol.to_string s, q)));
  Alcotest.(check int) "sent once" 1 (D.Sim_venue.received f.venue);
  (* Its costs say their arrival quote was an after-hours one. *)
  let fill_json = D.Oms.fills_json oms (D.Journal.recent_fills f.journal ~limit:1) in
  Alcotest.(check string)
    "the fill is marked arrival_after_hours" "true"
    (Yojson.Safe.to_string
       (Yojson.Safe.Util.member "arrival_after_hours"
          (List.hd_exn (Yojson.Safe.Util.to_list fill_json))));
  Alcotest.(check bool)
    "and the costs' note says why" true
    (String.is_substring
       (Yojson.Safe.Util.to_string (Yojson.Safe.Util.member "note" (D.Oms.tca_json oms)))
       ~substring:"arrival quote was read after hours");
  Graph.destroy f.graph;
  return ()

(* A kill sets the switch outside the sequencer, so it can land between two
   submits. Here it lands inside the first: AAPL +10 goes, and the switch is
   set while the venue answers it. Before MSFT +10 is sent the switch is
   asked again, and MSFT and XOM +10 after it, journaled pending_submit with
   AAPL, both move to rejected_pre_trade naming the switch, and neither is
   sent: the venue received one order. AAPL, given its venue id under the
   switch, is cancelled by the switch's own machinery. *)
let test_a_halt_between_two_submits_leaves_the_second_unsent () =
  let f = fixture () in
  record_close f;
  let halt = D.Halt.create D.Halt.Source.none in
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let submits = ref 0 in
  let halting =
    {
      sim with
      D.Venue.Trade.submit =
        (fun permit r ->
          incr submits;
          let answer = sim.D.Venue.Trade.submit permit r in
          D.Halt.halt halt ~why:"halted by hand, between two submits" ~at:evening;
          answer);
    }
  in
  let oms = manager ~trade:halting ~halt ~now:(fun () -> evening) f in
  after_the_close f oms;
  let%bind outcome =
    D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Open ~source:aapl_source
      ~legs:
        [
          rebalance_leg aapl D.Order.Side.Buy 10;
          rebalance_leg msft D.Order.Side.Buy 10;
          rebalance_leg xom D.Order.Side.Buy 10;
        ]
  in
  let%bind () = settle () in
  match outcome with
  | D.Oms.Rebalance_outcome.Stopped { sent = [ first ]; unsent = [ second; third ]; why }
    ->
      Alcotest.(check string)
        "the first was sent" "AAPL"
        (Symbol.to_string first.D.Order.request.D.Order.Request.symbol);
      Alcotest.(check (pair string string))
        "the second: MSFT, rejected before the wire"
        ("MSFT", "rejected_pre_trade")
        (Symbol.to_string second.D.Order.request.D.Order.Request.symbol, state f second);
      let reason =
        "kill_switch: the desk was halted by hand at 2026-09-14T23:15:00.000000000Z: \
         halted by hand, between two submits"
      in
      Alcotest.(check (option string))
        "with the switch's reason" (Some reason) (journaled f second).D.Order.reason;
      Alcotest.(check string) "and the outcome says the same" reason why;
      Alcotest.(check (triple string string (option string)))
        "the third: XOM, rejected before the wire, for the same reason"
        ("XOM", "rejected_pre_trade", Some reason)
        ( Symbol.to_string third.D.Order.request.D.Order.Request.symbol,
          state f third,
          (journaled f third).D.Order.reason );
      Alcotest.(check (pair int int))
        "one submit, and the venue received one" (1, 1)
        (!submits, D.Sim_venue.received f.venue);
      Alcotest.(check string)
        "and the first, named under the switch, was cancelled" "cancelled" (state f first);
      Graph.destroy f.graph;
      return ()
  | other ->
      Alcotest.failf "not stopped after one: %s" (D.Oms.Rebalance_outcome.to_string other)

(* The session rule, asked again once the arrival quote is in (Task 1's
   carried item). The clock names this session's close one second after the
   proposal starts; the rules pass, the quote is asked for, and while it is
   in flight the close passes. The order is refused by the session rule,
   journaled rejected_pre_trade, and nothing reaches the venue. *)
let test_a_session_that_closes_while_the_quote_is_fetched_is_refused () =
  let f = fixture () in
  let now = ref t0 in
  let asked = Ivar.create () and answer = Ivar.create () in
  let venue_read = D.Sim_venue.read f.venue in
  let read =
    {
      venue_read with
      D.Venue.Read.latest_quote =
        (fun symbol ->
          Ivar.fill_if_empty asked ();
          let%bind () = Ivar.read answer in
          venue_read.D.Venue.Read.latest_quote symbol);
    }
  in
  let oms = manager ~read ~now:(fun () -> !now) f in
  D.Oms.set_clock oms
    (Some
       {
         D.Venue.Session_clock.now = t0;
         is_open = true;
         next_open = Time_ns.add t0 (Time_ns.Span.of_day 1.0);
         next_close = Time_ns.add t0 (Time_ns.Span.of_sec 1.0);
         next_close_date = Date.of_string "2026-09-14";
       });
  let proposal = D.Oms.propose oms (ticket aapl D.Order.Side.Buy 10) in
  let%bind () = Ivar.read asked in
  now := Time_ns.add t0 (Time_ns.Span.of_sec 2.0);
  Ivar.fill_exn answer ();
  let%bind p, o = proposal in
  let%bind () = settle () in
  Alcotest.(check string) "refused" "rejected_pre_trade" (state f o);
  Alcotest.(check (list string)) "by the session rule" [ "session" ] (rules p);
  Alcotest.(check int) "and the venue received nothing" 0 (D.Sim_venue.received f.venue);
  Graph.destroy f.graph;
  return ()

(* The same for a rebalance, and for the opening auction's own window. At
   09:27:59 EDT on Tuesday the rules pass -- the window closes at 09:28 --
   and the arrival quote is asked for; while it is in flight 09:28 comes. The
   rebalance is refused -- for timing alone, Not_now, the session rule's
   refusal (the stage review's A-I1) -- naming the boundary, before anything
   is written: the journal holds no order, and the venue received none. *)
let test_a_rebalance_whose_window_closes_while_its_quotes_are_fetched_is_refused () =
  let f = fixture () in
  record_close f;
  let now = ref (Time_ns.of_string_with_utc_offset "2026-09-15T13:27:59Z") in
  let asked = Ivar.create () and answer = Ivar.create () in
  let venue_read = D.Sim_venue.read f.venue in
  let read =
    {
      venue_read with
      D.Venue.Read.latest_quote =
        (fun symbol ->
          Ivar.fill_if_empty asked ();
          let%bind () = Ivar.read answer in
          venue_read.D.Venue.Read.latest_quote symbol);
    }
  in
  let oms = manager ~read ~now:(fun () -> !now) f in
  after_the_close f oms;
  let proposal =
    D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Open ~source:aapl_source
      ~legs:[ rebalance_leg aapl D.Order.Side.Buy 10 ]
  in
  let%bind () = Ivar.read asked in
  now := Time_ns.of_string_with_utc_offset "2026-09-15T13:28:00Z";
  Ivar.fill_exn answer ();
  let%bind outcome = proposal in
  let%bind () = settle () in
  (match outcome with
  | D.Oms.Rebalance_outcome.Not_now why ->
      Alcotest.(check bool)
        (sprintf "naming the order and the boundary: %s" why)
        true
        (String.is_substring why
           ~substring:
             "after the arrival quotes, order 1 of 1 (AAPL buy 10 market-on-open): \
              session: it is 2026-09-15 09:28 ET, not before 2026-09-15 09:28 ET")
  | other -> Alcotest.failf "not refused: %s" (D.Oms.Rebalance_outcome.to_string other));
  Alcotest.(check (pair int int))
    "nothing journaled, nothing sent" (0, 0)
    ( List.length (D.Journal.recent_orders f.journal ~limit:10),
      D.Sim_venue.received f.venue );
  Graph.destroy f.graph;
  return ()

(* The whole path, from files to the venue. A LIVE strategy, exp_a01_aapl,
   on AAPL at a capital fraction of 0.01; the book holds nothing and a
   million in cash, so E is 1,000,000 and the target is 1.0 x 0.01 x
   1,000,000 / 100 = 100 shares (10,000, under the order cap and tech-cap).
   Two of its signals are accepted in one pass, sequence 1 and sequence 2:
   only 2 is sized -- 1's orders would rest beside it until the open, and the
   position counts fills -- and 1's judgement says so. 2 becomes one
   rebalance: one market-on-open order, AAPL +100, journaled with source
   signal:exp_a01_aapl:2 and sent, and the outcome is written over 2's
   pending sentence. *)
let test_an_accepted_signal_on_a_live_strategy_becomes_one_rebalance () =
  let f = fixture () in
  record_close ~sessions:[ "2026-09-11"; "2026-09-14" ] f;
  let oms = manager ~now:(fun () -> evening) f in
  after_the_close f oms;
  let dir = Filename_unix.temp_dir "ohcamel-signals-" "" in
  let intake =
    D.Intake.create ~journal:f.journal ~dir
      ~strategies:
        [
          {
            Ohcamel.Config.Book.Signals_spec.Strategy.name = "exp_a01_aapl";
            symbols = [ "AAPL" ];
            max_age = 3;
            sizing = Ohcamel.Config.Book.Signals_spec.Live;
            capital_fraction = 0.01;
            cadence = Ohcamel.Config.Book.Signals_spec.Cadence.Daily;
            adjust = Ohcamel.Config.Book.Signals_spec.Adjust.Intraday;
            max_age_minutes = 20;
            horizon = Ohcamel.Config.Book.Signals_spec.Horizon.Carry;
            stop = None;
            reinstated = None;
          };
        ]
      ~universe:[ aapl; msft; xom ] ~on_event:ignore
      ~now:(fun () -> evening)
      ()
  in
  let zeros = "sha256:" ^ String.make 64 '0' in
  List.iter
    [ (1, "2026-09-11"); (2, "2026-09-14") ]
    ~f:(fun (sequence, as_of) ->
      Out_channel.write_all
        (Filename.concat dir (sprintf "exp_a01_aapl-%s.json" as_of))
        ~data:
          (sprintf
             {|{"schema_version":1,"strategy":"exp_a01_aapl","params_hash":"%s","as_of":"%s","computed_at":"%sT23:15:00Z","data_hash":"%s","sequence":%d,"validation":{"status":"pass","gates_version":"2026-09-02"},"targets":[{"symbol":"AAPL","weight":1.0}]}|}
             zeros as_of as_of zeros sequence));
  let accepted = D.Intake.judge_pass intake in
  Alcotest.(check (list int))
    "both accepted in one pass" [ 1; 2 ]
    (List.map accepted ~f:(fun a -> a.D.Intake.Accepted.doc.sequence));
  let%bind () = D.Intake.size intake ~oms:(Some oms) ~scale:(fun () -> 1.0) accepted in
  let%bind () = settle () in
  let rebalance sequence =
    Option.value_exn
      (Option.bind (D.Journal.signal f.journal ~strategy:"exp_a01_aapl" ~sequence)
         ~f:(fun s -> s.D.Journal.Signal.rebalance))
  in
  let says what ~substring s =
    Alcotest.(check bool)
      (sprintf "%s: %S" what s) true
      (String.is_substring s ~substring)
  in
  says "1 is not sized" ~substring:"not sized: sequence 2 of the same strategy"
    (rebalance 1);
  says "2 was sent" ~substring:"1 market-on-open order journaled, then sent" (rebalance 2);
  says "as AAPL +100"
    ~substring:"AAPL buy 100 (target 100 at 100.00, weight 1; the strategy holds 0)"
    (rebalance 2);
  Alcotest.(check (list (triple string string int)))
    "one order in the journal, from sequence 2"
    [ ("signal:exp_a01_aapl:2", "AAPL", 100) ]
    (List.map (D.Journal.recent_orders f.journal ~limit:10) ~f:(fun r ->
         let q = r.D.Journal.Order_row.order.D.Order.request in
         ( r.D.Journal.Order_row.source,
           Symbol.to_string q.D.Order.Request.symbol,
           q.D.Order.Request.qty )));
  Alcotest.(check int) "sent once" 1 (D.Sim_venue.received f.venue);
  Array.iter (Sys_unix.readdir dir) ~f:(fun name ->
      Core_unix.unlink (Filename.concat dir name));
  Core_unix.rmdir dir;
  Graph.destroy f.graph;
  return ()

(* The incubation tier (Task 9), from files to the venue. inc_aapl on AAPL
   is (sizing incubation) at a capital fraction of 0.01, under
   (incubation_cap 0.005): it is the roster's only incubation strategy, so
   the cap's ratio is min(1, 0.005 / 0.01) = 0.5, folded into the scale:
   1 x 0.5 = 0.5. With E 1,000,000 (nothing held, a million in cash), w 1.0
   and AAPL's close of 100 the target is 1.0 x 0.01 x 0.5 x 1,000,000 / 100
   = 50 shares.
   Its one document FAILED its battery, and is sized all the same: one
   market-on-open order, AAPL +50, journaled under signal:inc_aapl:1 -- the
   strategy's one range, the tier being on the judgement and the sizing row,
   not the source -- and sent. The sizing row says what it was sized to:
   sequence 1, scale 0.5, allocated 0.01 x 0.5 x 1,000,000 = 5,000, tier
   incubation. [venue_name]
   renames the venue's read half; on a venue that is neither the paper
   account nor the simulator, nothing is journaled or sent, and the
   refusal is the rebalance's sentence. *)
let incubation_signal ?venue_name () =
  let f = fixture () in
  record_close f;
  let read =
    Option.map venue_name ~f:(fun name ->
        { (D.Sim_venue.read f.venue) with D.Venue.Read.name })
  in
  let oms = manager ?read ~incubation_cap:0.005 ~now:(fun () -> evening) f in
  after_the_close f oms;
  let dir = Filename_unix.temp_dir "ohcamel-signals-" "" in
  let intake =
    D.Intake.create ~journal:f.journal ~dir
      ~strategies:
        [
          {
            Ohcamel.Config.Book.Signals_spec.Strategy.name = "inc_aapl";
            symbols = [ "AAPL" ];
            max_age = 3;
            sizing = Ohcamel.Config.Book.Signals_spec.Incubation;
            capital_fraction = 0.01;
            cadence = Ohcamel.Config.Book.Signals_spec.Cadence.Daily;
            adjust = Ohcamel.Config.Book.Signals_spec.Adjust.Intraday;
            max_age_minutes = 20;
            horizon = Ohcamel.Config.Book.Signals_spec.Horizon.Carry;
            stop = None;
            reinstated = None;
          };
        ]
      ~universe:[ aapl; msft; xom ] ~on_event:ignore
      ~now:(fun () -> evening)
      ()
  in
  let zeros = "sha256:" ^ String.make 64 '0' in
  Out_channel.write_all
    (Filename.concat dir "inc_aapl-1.json")
    ~data:
      (sprintf
         {|{"schema_version":1,"strategy":"inc_aapl","params_hash":"%s","as_of":"2026-09-14","computed_at":"2026-09-14T23:15:00Z","data_hash":"%s","sequence":1,"validation":{"status":"fail","gates_version":"2026-09-02"},"targets":[{"symbol":"AAPL","weight":1.0}]}|}
         zeros zeros);
  let accepted = D.Intake.judge_pass intake in
  let%bind () = D.Intake.size intake ~oms:(Some oms) ~scale:(fun () -> 1.0) accepted in
  let%map () = settle () in
  let signal =
    Option.value_exn (D.Journal.signal f.journal ~strategy:"inc_aapl" ~sequence:1)
  in
  let orders =
    List.map (D.Journal.recent_orders f.journal ~limit:10) ~f:(fun r ->
        let q = r.D.Journal.Order_row.order.D.Order.request in
        ( r.D.Journal.Order_row.source,
          Symbol.to_string q.D.Order.Request.symbol,
          q.D.Order.Request.qty ))
  in
  let result =
    ( signal,
      orders,
      D.Sim_venue.received f.venue,
      D.Journal.latest_sizing f.journal ~strategy:"inc_aapl" )
  in
  Array.iter (Sys_unix.readdir dir) ~f:(fun name ->
      Core_unix.unlink (Filename.concat dir name));
  Core_unix.rmdir dir;
  Graph.destroy f.graph;
  result

let test_an_incubation_strategy's_failing_signal_is_sized_on_the_paper_venue () =
  let%map signal, orders, received, sizing = incubation_signal () in
  let rebalance = Option.value_exn signal.D.Journal.Signal.rebalance in
  Alcotest.(check (pair string (option string)))
    "accepted_incubation at R6"
    ("accepted_incubation", Some "R6")
    ( D.Journal.Signal.Verdict.to_string signal.D.Journal.Signal.verdict,
      signal.D.Journal.Signal.rule );
  Alcotest.(check bool)
    (sprintf "sized, not 'not sized': %S" rebalance)
    true
    (String.is_prefix rebalance ~prefix:"1 market-on-open order journaled, then sent");
  Alcotest.(check bool)
    (sprintf "as AAPL +50: %S" rebalance)
    true
    (String.is_substring rebalance
       ~substring:"AAPL buy 50 (target 50 at 100.00, weight 1; the strategy holds 0)");
  Alcotest.(check (list (triple string string int)))
    "one order, in the strategy's one range"
    [ ("signal:inc_aapl:1", "AAPL", 50) ]
    orders;
  Alcotest.(check int) "sent once" 1 received;
  match sizing with
  | None -> Alcotest.fail "no sizing row"
  | Some (sequence, scale, allocated, _, tier) ->
      Alcotest.(check (pair (triple int (float 1e-9) (float 1e-6)) string))
        "the sizing row: sequence 1, scale 0.5, 5,000 allocated, incubation"
        ((1, 0.5, 5_000.0), "incubation")
        ((sequence, scale, allocated), tier)

(* A cap of nothing flattens like a scale of nothing (Task 9's fix round).
   inc_aapl at 0.02 under (incubation_cap 0): the cap's ratio is 0, so the
   scale is 1 x 0 = 0 and every target is 1.0 x 0.02 x 0 x E / 100 = 0. The
   strategy already holds AAPL 200 of its own -- bought under
   signal:inc_aapl:1 and filled at the open, and held by the account -- so
   its next document, sequence 2, sells exactly those 200: a sale, not a
   refusal. (200 rather than 300: 300 x 100 = 30,000 is over this fixture's
   25,000 order cap, and the case is about the cap, not the order cap.) The
   sizing row: sequence 2, scale 0, allocated 0.02 x 0 x E = 0, incubation. *)
let test_a_cap_of_nothing_flattens_like_a_scale_of_nothing () =
  let f = fixture () in
  record_close f;
  let oms = manager ~incubation_cap:0.0 ~now:(fun () -> evening) f in
  after_the_close f oms;
  let%bind bought =
    D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Open
      ~source:
        {
          D.Rebalance.Source.strategy = "inc_aapl";
          sequence = 1;
          as_of = Date.of_string "2026-09-14";
        }
      ~legs:[ rebalance_leg aapl D.Order.Side.Buy 200 ]
  in
  (match bought with
  | D.Oms.Rebalance_outcome.Sent [ _ ] -> ()
  | other -> Alcotest.failf "not bought: %s" (D.Oms.Rebalance_outcome.to_string other));
  D.Sim_venue.set_session f.venue
    (D.Sim_venue.Session.Open
       {
         next_close = tuesday_close;
         next_open = Time_ns.add tuesday_open (Time_ns.Span.of_day 1.0);
       });
  let%bind () = pump f in
  Alcotest.(check (list (pair string (float 0.0))))
    "the strategy's own position: AAPL 200"
    [ ("AAPL", 200.0) ]
    (List.map (D.Journal.source_fills f.journal ~prefix:"signal:inc_aapl:")
       ~f:(fun (s, q) -> (Symbol.to_string s, q)));
  Graph.set_qty f.graph aapl (Qty.of_float 200.0);
  Graph.stabilize f.graph;
  after_the_close f oms;
  let dir = Filename_unix.temp_dir "ohcamel-signals-" "" in
  let intake =
    D.Intake.create ~journal:f.journal ~dir
      ~strategies:
        [
          {
            Ohcamel.Config.Book.Signals_spec.Strategy.name = "inc_aapl";
            symbols = [ "AAPL" ];
            max_age = 3;
            sizing = Ohcamel.Config.Book.Signals_spec.Incubation;
            capital_fraction = 0.02;
            cadence = Ohcamel.Config.Book.Signals_spec.Cadence.Daily;
            adjust = Ohcamel.Config.Book.Signals_spec.Adjust.Intraday;
            max_age_minutes = 20;
            horizon = Ohcamel.Config.Book.Signals_spec.Horizon.Carry;
            stop = None;
            reinstated = None;
          };
        ]
      ~universe:[ aapl; msft; xom ] ~on_event:ignore
      ~now:(fun () -> evening)
      ()
  in
  let zeros = "sha256:" ^ String.make 64 '0' in
  Out_channel.write_all
    (Filename.concat dir "inc_aapl-2.json")
    ~data:
      (sprintf
         {|{"schema_version":1,"strategy":"inc_aapl","params_hash":"%s","as_of":"2026-09-14","computed_at":"2026-09-14T23:15:00Z","data_hash":"%s","sequence":2,"validation":{"status":"fail","gates_version":"2026-09-02"},"targets":[{"symbol":"AAPL","weight":1.0}]}|}
         zeros zeros);
  let accepted = D.Intake.judge_pass intake in
  let%bind () = D.Intake.size intake ~oms:(Some oms) ~scale:(fun () -> 1.0) accepted in
  let%bind () = settle () in
  let rebalance =
    Option.value_exn
      (Option.bind (D.Journal.signal f.journal ~strategy:"inc_aapl" ~sequence:2)
         ~f:(fun s -> s.D.Journal.Signal.rebalance))
  in
  Alcotest.(check bool)
    (sprintf "a sale, not a refusal: %S" rebalance)
    true
    (String.is_prefix rebalance ~prefix:"1 market-on-open order journaled, then sent");
  Alcotest.(check bool)
    (sprintf "AAPL -200 to a target of 0: %S" rebalance)
    true
    (String.is_substring rebalance
       ~substring:"AAPL sell 200 (target 0 at 100.00, weight 1; the strategy holds 200)");
  (match D.Journal.latest_sizing f.journal ~strategy:"inc_aapl" with
  | None -> Alcotest.fail "no sizing row"
  | Some (sequence, scale, allocated, _, tier) ->
      Alcotest.(check (pair (triple int (float 0.0) (float 0.0)) string))
        "the sizing row: sequence 2, scale 0, nothing allocated, incubation"
        ((2, 0.0, 0.0), "incubation")
        ((sequence, scale, allocated), tier));
  Array.iter (Sys_unix.readdir dir) ~f:(fun name ->
      Core_unix.unlink (Filename.concat dir name));
  Core_unix.rmdir dir;
  Graph.destroy f.graph;
  return ()

let test_incubation_refused_on_a_foreign_venue () =
  let%map signal, orders, received, sizing = incubation_signal ~venue_name:"other" () in
  Alcotest.(check (option string))
    "the rebalance's sentence"
    (Some
       "refused, and nothing journaled or sent: incubation sizing is refused on venue \
        other")
    signal.D.Journal.Signal.rebalance;
  Alcotest.(check int) "nothing journaled" 0 (List.length orders);
  Alcotest.(check int) "nothing sent" 0 received;
  Alcotest.(check bool) "and no sizing row" true (Option.is_none sizing)

(* The review's case (C1). AAPL -200 and MSFT +120, sells first, pass the
   gate on every subset on this empty book (TECH is a net sum: whole
   |-20,000 + 24,000| = 4,000; the sell alone 20,000; the buy alone 24,000).
   The venue refuses the sell. The buy, gated beside it,
   is a different trade without it, so it is moved to rejected_pre_trade
   naming the sell, and never sent: the venue received nothing. Then the same
   with a sell whose answer is lost: it reached the venue and its outcome is
   unknown, and the buy is again not sent -- the venue received one order.
   And when the venue takes the sell and refuses the buy, the LAST order:
   both went, the sell acknowledged and the buy rejected_by_venue, and the
   outcome says the last was not acknowledged -- never that the orders were
   sent and acknowledged, and never that an order after it went unsent. *)
let test_a_leg_the_venue_does_not_acknowledge_stops_the_legs_after_it () =
  let run ~answer =
    let f = fixture () in
    record_close f;
    let sim = D.Sim_venue.trade ~auto:false f.venue in
    let submits = ref [] in
    let refusing =
      {
        sim with
        D.Venue.Trade.submit =
          (fun permit r ->
            submits := Symbol.to_string r.D.Order.Request.symbol :: !submits;
            match answer with
            | `Refused ->
                return (D.Venue.Submission.Rejected "insufficient qty available")
            | `Refused_last when List.length !submits = 2 ->
                return (D.Venue.Submission.Rejected "insufficient buying power")
            | `Refused_last -> sim.D.Venue.Trade.submit permit r
            | `Lost ->
                let%map (_ : D.Venue.Submission.t) = sim.D.Venue.Trade.submit permit r in
                D.Venue.Submission.Unknown "timed out after 10 s");
      }
    in
    let oms = manager ~trade:refusing ~now:(fun () -> evening) f in
    after_the_close f oms;
    let%bind outcome =
      D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Open ~source:aapl_source
        ~legs:
          [
            rebalance_leg aapl D.Order.Side.Sell 200;
            rebalance_leg msft D.Order.Side.Buy 120;
          ]
    in
    let%map () = settle () in
    (f, outcome, List.rev !submits)
  in
  let check what (f, outcome, submits) ~sell_state ~received =
    (match outcome with
    | D.Oms.Rebalance_outcome.Stopped { sent = [ sell ]; unsent = [ buy ]; why } ->
        Alcotest.(check (pair string string))
          (what ^ ": the sell went, and stands as the venue left it")
          ("AAPL", sell_state)
          (Symbol.to_string sell.D.Order.request.D.Order.Request.symbol, state f sell);
        Alcotest.(check (pair string string))
          (what ^ ": the buy was never sent")
          ("MSFT", "rejected_pre_trade")
          (Symbol.to_string buy.D.Order.request.D.Order.Request.symbol, state f buy);
        Alcotest.(check bool)
          (sprintf "%s: its reason names the sell: %s" what why)
          true
          (String.is_substring why
             ~substring:"order 1 of 2 (AAPL sell 200 market-on-open) was not acknowledged"
          && Option.equal String.equal (journaled f buy).D.Order.reason (Some why))
    | other -> Alcotest.failf "%s: %s" what (D.Oms.Rebalance_outcome.to_string other));
    Alcotest.(check (pair (list string) int))
      (what ^ ": one submit, the sell's")
      ([ "AAPL" ], received)
      (submits, D.Sim_venue.received f.venue);
    Graph.destroy f.graph
  in
  let%bind refused = run ~answer:`Refused in
  check "refused" refused ~sell_state:"rejected_by_venue" ~received:0;
  let%bind lost = run ~answer:`Lost in
  check "unknown" lost ~sell_state:"accepted" ~received:1;
  let%map f, outcome, submits = run ~answer:`Refused_last in
  (match outcome with
  | D.Oms.Rebalance_outcome.Stopped { sent = [ sell; buy ]; unsent = []; why } ->
      Alcotest.(check (list (pair string string)))
        "the last refused: both went, as the venue left them"
        [ ("AAPL", "accepted"); ("MSFT", "rejected_by_venue") ]
        (List.map [ sell; buy ] ~f:(fun o ->
             (Symbol.to_string o.D.Order.request.D.Order.Request.symbol, state f o)));
      Alcotest.(check string)
        "the reason names the last order, and nothing after it" why
        "rebalance: order 2 of 2 (MSFT buy 120 market-on-open) was not acknowledged (the \
         venue refused it: insufficient buying power)";
      let said = D.Oms.Rebalance_outcome.to_string outcome in
      Alcotest.(check (pair bool bool))
        (sprintf "the sentence says the last was not acknowledged: %s" said)
        (true, false)
        ( String.is_substring said
            ~substring:"every order journaled, then sent, and the last not acknowledged",
          String.is_substring said ~substring:"and acknowledged:" )
  | other ->
      Alcotest.failf "the last refused: %s" (D.Oms.Rebalance_outcome.to_string other));
  Alcotest.(check (pair (list string) int))
    "the last refused: two submits, one received"
    ([ "AAPL"; "MSFT" ], 1)
    (submits, D.Sim_venue.received f.venue);
  Graph.destroy f.graph

(* ------------------------------------------------------------------------ *)
(* Task 10: the intraday mode and its waves                                  *)
(* ------------------------------------------------------------------------ *)

(* Tuesday 15 September at 11:00 EDT (15:00Z), inside the session the
   manager's market reads open: Monday's close, recorded, is still the newest
   whose 16:00 has come, so the calendar passes. *)
let tuesday_mid = Time_ns.of_string_with_utc_offset "2026-09-15T15:00:00Z"

(* An intraday rebalance of three, AAPL, MSFT and XOM +10 at marks of 100,
   200 and 50: three day limit orders at each mark x 1.001 -- 100.10, 200.20,
   50.05 -- sent at once, one wave, then waited on. Each fills on the next
   pump, the ask (mark x 1.0005) being inside its limit: AAPL at 100.05. The
   wait's next poll, 2 s on, finds nothing open, and the outcome is the
   wave's own: Sent, as a rebalance of one wave settled inside its wait. *)
let three_buys =
  [
    rebalance_leg aapl D.Order.Side.Buy 10;
    rebalance_leg msft D.Order.Side.Buy 10;
    rebalance_leg xom D.Order.Side.Buy 10;
  ]

let advance_to f seconds =
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec seconds))
  in
  settle ()

let test_intraday_single_wave_sends_day_limits () =
  let f = fixture () in
  record_close f;
  let oms = manager ~now:(fun () -> tuesday_mid) f in
  let waiting =
    D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Intraday ~source:aapl_source
      ~legs:three_buys
  in
  let%bind () = settle () in
  Alcotest.(check (pair int bool))
    "three sent, and the wave waited on" (3, false)
    (D.Sim_venue.received f.venue, Deferred.is_determined waiting);
  let%bind () = pump f in
  let%bind () = advance_to f 2.0 in
  let outcome =
    match Deferred.peek waiting with
    | Some o -> o
    | None -> Alcotest.fail "still waiting once every order filled"
  in
  let sent =
    match outcome with
    | D.Oms.Rebalance_outcome.Sent os -> os
    | other -> Alcotest.failf "not sent: %s" (D.Oms.Rebalance_outcome.to_string other)
  in
  Alcotest.(check (list (triple string string (float 1e-9))))
    "three day limits at mark x 1.001"
    [
      ("AAPL", "limit day", 100.10);
      ("MSFT", "limit day", 200.20);
      ("XOM", "limit day", 50.05);
    ]
    (List.map sent ~f:(fun o ->
         let r = (journaled f o).D.Order.request in
         ( Symbol.to_string r.D.Order.Request.symbol,
           sprintf "%s %s"
             (D.Order.Kind.to_string r.D.Order.Request.kind)
             (D.Order.Tif.to_string r.D.Order.Request.tif),
           Price.to_float
             (Option.value_exn (D.Order.Kind.limit_price r.D.Order.Request.kind)) )));
  Alcotest.(check bool)
    "the sentence names day limits" true
    (String.is_prefix
       (D.Oms.Rebalance_outcome.to_string outcome)
       ~prefix:"3 day limit orders journaled, then sent, and acknowledged");
  Alcotest.(check (list string))
    "each filled on the next pump"
    [ "filled"; "filled"; "filled" ]
    (List.map sent ~f:(state f));
  Alcotest.(check (option (float 1e-9)))
    "AAPL at 100 x 1.0005" (Some 100.05)
    (D.Order.avg_fill_price (journaled f (List.hd_exn sent)));
  Graph.destroy f.graph;
  return ()

(* Open mode is what it was: two legs after Monday's close are two
   market-on-open orders, market and opg, and the sentence is the one the
   intake has always recorded. *)
let test_open_mode_unchanged () =
  let f = fixture () in
  record_close f;
  let oms = manager ~now:(fun () -> evening) f in
  after_the_close f oms;
  let%bind outcome =
    D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Open ~source:aapl_source
      ~legs:
        [ rebalance_leg aapl D.Order.Side.Buy 10; rebalance_leg xom D.Order.Side.Buy 10 ]
  in
  let%map () = settle () in
  (match outcome with
  | D.Oms.Rebalance_outcome.Sent os ->
      Alcotest.(check (list string))
        "market, opg"
        [ "market opg"; "market opg" ]
        (List.map os ~f:(fun o ->
             let r = (journaled f o).D.Order.request in
             sprintf "%s %s"
               (D.Order.Kind.to_string r.D.Order.Request.kind)
               (D.Order.Tif.to_string r.D.Order.Request.tif)))
  | other -> Alcotest.failf "not sent: %s" (D.Oms.Rebalance_outcome.to_string other));
  Alcotest.(check bool)
    "the sentence it always was" true
    (String.is_prefix
       (D.Oms.Rebalance_outcome.to_string outcome)
       ~prefix:"2 market-on-open orders journaled, then sent, and acknowledged: ");
  Graph.destroy f.graph

(* Seven names for two waves: the fixture's three and IWM 200, QQQ 300, SPY
   400, TLT 90. Nothing is held, so every buy grows a position and there is
   no shrinking wave: +10 of each, by notional 10 x price -- SPY 4,000, QQQ
   3,000, IWM 2,000, MSFT 2,000, AAPL 1,000, TLT 900, XOM 500 -- is wave 1
   the six largest and wave 2 XOM alone. *)
let iwm = Symbol.of_string "IWM"
let qqq = Symbol.of_string "QQQ"
let spy = Symbol.of_string "SPY"
let tlt = Symbol.of_string "TLT"

let wide () =
  fixture
    ~extra:
      [
        (iwm, "FUNDS", 200.0);
        (qqq, "FUNDS", 300.0);
        (spy, "FUNDS", 400.0);
        (tlt, "FUNDS", 90.0);
      ]
    ()

let seven =
  List.map
    [
      (aapl, 100.0);
      (msft, 200.0);
      (xom, 50.0);
      (iwm, 200.0);
      (qqq, 300.0);
      (spy, 400.0);
      (tlt, 90.0);
    ]
    ~f:(fun (s, price) ->
      { (rebalance_leg s D.Order.Side.Buy 10) with D.Rebalance.Leg.price })

let wide_source =
  {
    D.Rebalance.Source.strategy = "exp_i_wide";
    sequence = 1;
    as_of = Date.of_string "2026-09-14";
  }

let symbols_of orders =
  List.map orders ~f:(fun (r : D.Journal.Order_row.t) ->
      Symbol.to_string r.D.Journal.Order_row.order.D.Order.request.D.Order.Request.symbol)
  |> List.sort ~compare:String.compare

(* The wait runs out. Wave 1's six day limits go and the venue takes them;
   every mark then moves up 1%, so each ask (mark x 1.01 x 1.0005) is above
   its limit (mark x 1.001) and the pumps fill nothing. At 179 s the
   rebalance is still waiting; at 180 s, the book's wave_timeout_s, it stops:
   every order of the rebalance is cancelled -- journaled pending_cancel when
   its DELETE is sent, cancelled when the venue confirms -- the outcome is
   Waved with timed_out, and XOM's wave 2 is never sent: the venue received
   six orders. *)
let test_wave_timeout_cancels () =
  let f = wide () in
  record_close f;
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let at_the_delete = ref [] in
  let watched =
    {
      sim with
      D.Venue.Trade.cancel =
        (fun id ->
          at_the_delete :=
            List.filter_map (D.Journal.open_orders f.journal) ~f:(fun r ->
                let o = r.D.Journal.Order_row.order in
                Option.some_if
                  (Option.equal String.equal o.D.Order.venue_order_id (Some id))
                  (D.Order.State.to_string o.D.Order.state))
            @ !at_the_delete;
          sim.D.Venue.Trade.cancel id);
    }
  in
  let oms = manager ~trade:watched ~now:(fun () -> tuesday_mid) f in
  let outcome =
    D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Intraday ~source:wide_source
      ~legs:seven
  in
  let%bind () = settle () in
  let orders () = D.Journal.recent_orders f.journal ~limit:20 in
  Alcotest.(check (list string))
    "wave 1: the six largest, sent and taken"
    [ "AAPL"; "IWM"; "MSFT"; "QQQ"; "SPY"; "TLT" ]
    (symbols_of (orders ()));
  List.iter
    [
      (aapl, 100.0); (msft, 200.0); (iwm, 200.0); (qqq, 300.0); (spy, 400.0); (tlt, 90.0);
    ]
    ~f:(fun (s, p) -> mark f s (p *. 1.01));
  let%bind () = pump f in
  Alcotest.(check (list string))
    "outside their limits, nothing fills"
    (List.init 6 ~f:(fun _ -> "accepted"))
    (List.map (orders ()) ~f:(fun r ->
         D.Order.State.to_string r.D.Journal.Order_row.order.D.Order.state));
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 179.0))
  in
  let%bind () = settle () in
  Alcotest.(check bool) "at 179 s, still waiting" false (Deferred.is_determined outcome);
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle f.clock
      ~to_:(Time_ns.add t0 (Time_ns.Span.of_sec 180.0))
  in
  let%bind () = settle () in
  (match Deferred.peek outcome with
  | Some
      (D.Oms.Rebalance_outcome.Waved
         { waves = [ D.Oms.Rebalance_outcome.Sent os ]; timed_out = true } as o) ->
      Alcotest.(check int) "one wave of six" 6 (List.length os);
      Alcotest.(check bool)
        (sprintf "the sentence says it timed out: %s"
           (D.Oms.Rebalance_outcome.to_string o))
        true
        (String.is_prefix
           (D.Oms.Rebalance_outcome.to_string o)
           ~prefix:
             "in 1 wave, and wave 1 was not filled or cancelled within its wait, so \
              every open order of the rebalance was cancelled and no wave after it was \
              sent: wave 1: 6 day limit orders")
  | Some other ->
      Alcotest.failf "not timed out: %s" (D.Oms.Rebalance_outcome.to_string other)
  | None -> Alcotest.fail "still waiting at 180 s");
  Alcotest.(check (list string))
    "each DELETE sent with its order journaled pending_cancel"
    (List.init 6 ~f:(fun _ -> "pending_cancel"))
    !at_the_delete;
  Alcotest.(check (list string))
    "then cancelled, every one"
    (List.init 6 ~f:(fun _ -> "cancelled"))
    (List.map (orders ()) ~f:(fun r ->
         D.Order.State.to_string r.D.Journal.Order_row.order.D.Order.state));
  Alcotest.(check int) "no second wave: six received" 6 (D.Sim_venue.received f.venue);
  Graph.destroy f.graph;
  return ()

(* A restart between two waves (the plan's Review Focus 4). Wave 1's six
   day limits are sent and rest; the manager is dropped while it waits, and a
   new one is built on the same journal and venue and reconciles. The next
   intake pass brings sequence 2 for the same strategy: its targets are not
   sized, because six of its orders may still fill -- the existing refusal,
   word for word, as the rebalance's sentence -- and no new order exists: the
   journal holds the six, the venue received six. *)
let test_restart_mid_wave_does_not_double_send () =
  let f = wide () in
  record_close f;
  let oms = manager ~now:(fun () -> tuesday_mid) f in
  let first =
    D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Intraday ~source:wide_source
      ~legs:seven
  in
  let%bind () = settle () in
  Alcotest.(check (pair int bool))
    "wave 1 of 2 sent, and its wait not over" (6, false)
    (D.Sim_venue.received f.venue, Deferred.is_determined first);
  let restarted = manager ~now:(fun () -> tuesday_mid) f in
  let%bind () = D.Oms.reconcile restarted in
  let%bind () = settle () in
  let dir = Filename_unix.temp_dir "ohcamel-signals-" "" in
  let intake =
    D.Intake.create ~journal:f.journal ~dir
      ~strategies:
        [
          {
            Ohcamel.Config.Book.Signals_spec.Strategy.name = "exp_i_wide";
            symbols = [ "AAPL"; "MSFT"; "XOM"; "IWM"; "QQQ"; "SPY"; "TLT" ];
            max_age = 3;
            sizing = Ohcamel.Config.Book.Signals_spec.Live;
            capital_fraction = 0.01;
            cadence = Ohcamel.Config.Book.Signals_spec.Cadence.Daily;
            adjust = Ohcamel.Config.Book.Signals_spec.Adjust.Intraday;
            max_age_minutes = 20;
            horizon = Ohcamel.Config.Book.Signals_spec.Horizon.Carry;
            stop = None;
            reinstated = None;
          };
        ]
      ~universe:[ aapl; msft; xom; iwm; qqq; spy; tlt ]
      ~on_event:ignore
      ~now:(fun () -> tuesday_mid)
      ()
  in
  let zeros = "sha256:" ^ String.make 64 '0' in
  Out_channel.write_all
    (Filename.concat dir "exp_i_wide-2.json")
    ~data:
      (sprintf
         {|{"schema_version":1,"strategy":"exp_i_wide","params_hash":"%s","as_of":"2026-09-14","computed_at":"2026-09-14T23:15:00Z","data_hash":"%s","sequence":2,"validation":{"status":"pass","gates_version":"2026-09-02"},"targets":[{"symbol":"SPY","weight":1.0}]}|}
         zeros zeros);
  let accepted = D.Intake.judge_pass intake in
  Alcotest.(check (list int))
    "sequence 2 accepted" [ 2 ]
    (List.map accepted ~f:(fun a -> a.D.Intake.Accepted.doc.sequence));
  let%bind () =
    D.Intake.size intake ~oms:(Some restarted) ~scale:(fun () -> 1.0) accepted
  in
  let%map () = settle () in
  let resting =
    D.Journal.source_orders_unsettled f.journal ~prefix:"signal:exp_i_wide:"
      ~since:(Some (Date.of_string "2026-09-14"))
  in
  Alcotest.(check string)
    "the existing refusal, word for word"
    (sprintf
       "no targets, and no order: the strategy's fill history is unknown: 6 of its \
        orders may still fill (%s), so its position is not yet known, and nothing is \
        sized on top of them"
       (String.concat ~sep:"; "
          (List.map resting ~f:(fun r ->
               D.Oms.describe_resting r.D.Journal.Order_row.order))))
    (Option.value_exn
       (Option.bind (D.Journal.signal f.journal ~strategy:"exp_i_wide" ~sequence:2)
          ~f:(fun s -> s.D.Journal.Signal.rebalance)));
  Alcotest.(check (pair (list string) int))
    "no new order: the journal's six, the venue's six"
    ([ "AAPL"; "IWM"; "MSFT"; "QQQ"; "SPY"; "TLT" ], 6)
    ( symbols_of (D.Journal.recent_orders f.journal ~limit:20),
      D.Sim_venue.received f.venue );
  Array.iter (Sys_unix.readdir dir) ~f:(fun name ->
      Core_unix.unlink (Filename.concat dir name));
  Core_unix.rmdir dir;
  Graph.destroy f.graph

(* A single wave withheld: the three day limits go, every mark moves up 1%
   so no ask reaches its limit, and at 180 s the wave's wait runs out --
   each order cancelled, journaled pending_cancel at its DELETE, and the
   outcome Waved [Sent 3] with timed_out, where a settled single wave would
   have been Sent. *)
let test_a_single_intraday_wave_withheld_is_cancelled_at_its_timeout () =
  let f = fixture () in
  record_close f;
  let oms = manager ~now:(fun () -> tuesday_mid) f in
  let waiting =
    D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Intraday ~source:aapl_source
      ~legs:three_buys
  in
  let%bind () = settle () in
  List.iter
    [ (aapl, 100.0); (msft, 200.0); (xom, 50.0) ]
    ~f:(fun (s, p) -> mark f s (p *. 1.01));
  let%bind () = pump f in
  let%bind () = advance_to f 179.0 in
  Alcotest.(check bool) "at 179 s, still waiting" false (Deferred.is_determined waiting);
  let%bind () = advance_to f 180.0 in
  (match Deferred.peek waiting with
  | Some
      (D.Oms.Rebalance_outcome.Waved
         { waves = [ D.Oms.Rebalance_outcome.Sent os ]; timed_out = true }) ->
      Alcotest.(check (list string))
        "all three cancelled"
        [ "cancelled"; "cancelled"; "cancelled" ]
        (List.map os ~f:(state f))
  | Some other ->
      Alcotest.failf "not timed out: %s" (D.Oms.Rebalance_outcome.to_string other)
  | None -> Alcotest.fail "still waiting at 180 s");
  Alcotest.(check int) "three received, nothing more" 3 (D.Sim_venue.received f.venue);
  Graph.destroy f.graph;
  return ()

(* A wave the venue stops partway is waited on and cancelled like any other.
   The venue acknowledges AAPL and MSFT and leaves XOM, the wave's last,
   unacknowledged -- its submit answers unknown and never reached the venue
   -- so the wave is Stopped, every order having gone. Nothing fills. XOM's
   lookups find nothing at 2, 12 and 42 s, and it fails; the two the venue
   holds rest until 180 s, when each is cancelled, journaled pending_cancel
   at its DELETE. Outcome: Waved [Stopped] with timed_out. *)
let test_a_stopped_wave's_orders_are_cancelled_at_the_timeout () =
  let f = fixture () in
  record_close f;
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let submits = ref 0 in
  let at_the_delete = ref [] in
  let scripted =
    {
      sim with
      D.Venue.Trade.submit =
        (fun permit r ->
          incr submits;
          if !submits = 3 then return (D.Venue.Submission.Unknown "timed out after 10 s")
          else sim.D.Venue.Trade.submit permit r);
      cancel =
        (fun id ->
          at_the_delete :=
            List.filter_map (D.Journal.open_orders f.journal) ~f:(fun r ->
                let o = r.D.Journal.Order_row.order in
                Option.some_if
                  (Option.equal String.equal o.D.Order.venue_order_id (Some id))
                  (D.Order.State.to_string o.D.Order.state))
            @ !at_the_delete;
          sim.D.Venue.Trade.cancel id);
    }
  in
  let oms = manager ~trade:scripted ~now:(fun () -> tuesday_mid) f in
  let waiting =
    D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Intraday ~source:aapl_source
      ~legs:three_buys
  in
  let%bind () = settle () in
  let%bind () = advance_to f 179.0 in
  Alcotest.(check bool) "at 179 s, still waiting" false (Deferred.is_determined waiting);
  let%bind () = advance_to f 180.0 in
  (match Deferred.peek waiting with
  | Some
      (D.Oms.Rebalance_outcome.Waved
         {
           waves = [ D.Oms.Rebalance_outcome.Stopped { sent; unsent = []; _ } ];
           timed_out = true;
         }) ->
      Alcotest.(check (list (pair string string)))
        "the two acknowledged cancelled; the unknown one failed when nothing found it"
        [ ("AAPL", "cancelled"); ("MSFT", "cancelled"); ("XOM", "failed") ]
        (List.map sent ~f:(fun o ->
             (Symbol.to_string o.D.Order.request.D.Order.Request.symbol, state f o)))
  | Some other ->
      Alcotest.failf "not timed out: %s" (D.Oms.Rebalance_outcome.to_string other)
  | None -> Alcotest.fail "still waiting at 180 s");
  Alcotest.(check (list string))
    "each DELETE sent with its order journaled pending_cancel"
    [ "pending_cancel"; "pending_cancel" ]
    !at_the_delete;
  Alcotest.(check int) "two reached the venue" 2 (D.Sim_venue.received f.venue);
  Graph.destroy f.graph;
  return ()

(* Two waves that settle. Wave 1's six fill on a pump; between the waves the
   strategy's own position is the six at 10 each. The wait's poll at 2 s
   finds nothing open and wave 2, XOM +10, is checked and sent -- and at its
   submit the book the gate read already holds wave 1's fills: 10 of each of
   the six. XOM fills on the next pump, the poll at 4 s finds nothing open,
   and the outcome is Waved [Sent 6; Sent 1], not timed out: seven received. *)
let test_two_waves_settle_and_the_second_is_gated_after_the_first_fills () =
  let f = wide () in
  record_close f;
  let sim = D.Sim_venue.trade ~auto:false f.venue in
  let book_at_wave_2 = ref None in
  let six = [ aapl; iwm; msft; qqq; spy; tlt ] in
  let watched =
    {
      sim with
      D.Venue.Trade.submit =
        (fun permit r ->
          if Symbol.equal r.D.Order.Request.symbol xom then
            book_at_wave_2 :=
              Some (List.map six ~f:(fun s -> Qty.to_float (Graph.qty f.graph s)));
          sim.D.Venue.Trade.submit permit r);
    }
  in
  let oms = manager ~trade:watched ~now:(fun () -> tuesday_mid) f in
  let waiting =
    D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Intraday ~source:wide_source
      ~legs:seven
  in
  let%bind () = settle () in
  Alcotest.(check int) "wave 1: six" 6 (D.Sim_venue.received f.venue);
  let%bind () = pump f in
  (match D.Oms.strategy_position oms ~strategy:"exp_i_wide" with
  | Ok own ->
      Alcotest.(check (list (pair string (float 0.0))))
        "between the waves, the strategy's own: the six at 10"
        (List.map [ "AAPL"; "IWM"; "MSFT"; "QQQ"; "SPY"; "TLT" ] ~f:(fun s -> (s, 10.0)))
        (List.map (Map.to_alist own) ~f:(fun (s, q) -> (Symbol.to_string s, q)))
  | Error why -> Alcotest.failf "unknown between the waves: %s" why);
  let%bind () = advance_to f 2.0 in
  Alcotest.(check int) "wave 2 sent at the poll" 7 (D.Sim_venue.received f.venue);
  Alcotest.(check (option (list (float 0.0))))
    "wave 2 gated on the book with wave 1's fills"
    (Some (List.init 6 ~f:(fun _ -> 10.0)))
    !book_at_wave_2;
  let%bind () = pump f in
  let%bind () = advance_to f 4.0 in
  (match Deferred.peek waiting with
  | Some
      (D.Oms.Rebalance_outcome.Waved
         {
           waves =
             [ D.Oms.Rebalance_outcome.Sent first; D.Oms.Rebalance_outcome.Sent second ];
           timed_out = false;
         }) ->
      Alcotest.(check (pair int int))
        "six, then one" (6, 1)
        (List.length first, List.length second);
      Alcotest.(check (list string))
        "every order filled"
        (List.init 7 ~f:(fun _ -> "filled"))
        (List.map (first @ second) ~f:(state f))
  | Some other ->
      Alcotest.failf "not two waves: %s" (D.Oms.Rebalance_outcome.to_string other)
  | None -> Alcotest.fail "still waiting");
  Graph.destroy f.graph;
  return ()

(* The floor in the Open mode (the stage review's A-I6, withdrawing Task 10's
   "both modes"): nothing is dropped. exp_floor holds AAPL and XOM at 0.01
   of E 1,000,000. A weight of 0.01 on XOM is 100 / 50 = 2 shares, 100,
   under the $200 floor -- and a market-on-open rebalance sends it all the
   same, beside AAPL at 0.99 (99 shares) or alone, and its sentence names no
   floor. The intraday floor is test_rebalance's. *)
let floor_signal targets =
  let f = fixture () in
  record_close f;
  let oms = manager ~now:(fun () -> evening) f in
  after_the_close f oms;
  let dir = Filename_unix.temp_dir "ohcamel-signals-" "" in
  let intake =
    D.Intake.create ~journal:f.journal ~dir
      ~strategies:
        [
          {
            Ohcamel.Config.Book.Signals_spec.Strategy.name = "exp_floor";
            symbols = [ "AAPL"; "XOM" ];
            max_age = 3;
            sizing = Ohcamel.Config.Book.Signals_spec.Live;
            capital_fraction = 0.01;
            cadence = Ohcamel.Config.Book.Signals_spec.Cadence.Daily;
            adjust = Ohcamel.Config.Book.Signals_spec.Adjust.Intraday;
            max_age_minutes = 20;
            horizon = Ohcamel.Config.Book.Signals_spec.Horizon.Carry;
            stop = None;
            reinstated = None;
          };
        ]
      ~universe:[ aapl; msft; xom ] ~on_event:ignore
      ~now:(fun () -> evening)
      ()
  in
  let zeros = "sha256:" ^ String.make 64 '0' in
  Out_channel.write_all
    (Filename.concat dir "exp_floor-1.json")
    ~data:
      (sprintf
         {|{"schema_version":1,"strategy":"exp_floor","params_hash":"%s","as_of":"2026-09-14","computed_at":"2026-09-14T23:15:00Z","data_hash":"%s","sequence":1,"validation":{"status":"pass","gates_version":"2026-09-02"},"targets":[%s]}|}
         zeros zeros targets);
  let accepted = D.Intake.judge_pass intake in
  let%bind () = D.Intake.size intake ~oms:(Some oms) ~scale:(fun () -> 1.0) accepted in
  let%map () = settle () in
  let sentence =
    Option.value_exn
      (Option.bind (D.Journal.signal f.journal ~strategy:"exp_floor" ~sequence:1)
         ~f:(fun s -> s.D.Journal.Signal.rebalance))
  in
  let result =
    ( sentence,
      Option.is_some (D.Journal.latest_sizing f.journal ~strategy:"exp_floor"),
      D.Sim_venue.received f.venue )
  in
  Array.iter (Sys_unix.readdir dir) ~f:(fun name ->
      Core_unix.unlink (Filename.concat dir name));
  Core_unix.rmdir dir;
  Graph.destroy f.graph;
  result

let test_an_open_rebalance_sends_a_leg_under_the_floor () =
  let%bind one, sized_one, received_one =
    floor_signal {|{"symbol":"AAPL","weight":0.99},{"symbol":"XOM","weight":0.01}|}
  in
  Alcotest.(check bool)
    (sprintf "both sent, no floor named: %S" one)
    true
    (String.is_prefix one ~prefix:"2 market-on-open orders journaled, then sent"
    && not (String.is_substring one ~substring:"floor"));
  Alcotest.(check (pair bool int)) "sized, both sent" (true, 2) (sized_one, received_one);
  let%map alone, sized_alone, received_alone =
    floor_signal {|{"symbol":"XOM","weight":0.01}|}
  in
  Alcotest.(check bool)
    (sprintf "XOM alone, 2 x 50 = 100, sent: %S" alone)
    true
    (String.is_prefix alone ~prefix:"1 market-on-open order journaled, then sent"
    && String.is_substring alone ~substring:"XOM buy 2");
  Alcotest.(check (pair bool int))
    "sized, and sent" (true, 1) (sized_alone, received_alone)

(* ------------------------------------------------------------------------ *)
(* Task 11: the overlay and the adjust pass                                  *)
(* ------------------------------------------------------------------------ *)

type adjust_fixture = {
  a_f : fixture;
  a_name : string;
  a_oms : D.Oms.t;
  a_intake : D.Intake.t;
  a_dir : string;
  pass_and_adjust : at:float -> unit Deferred.t;
  cleanup : unit -> unit;
}

(* A strategy at half the capital, adjusting intraday, on AAPL, holding 350
   of its own on a book of exactly 100,000, last sized at 0.70; then
   overlay-7.json arrives at [overlay], and the adjust pass runs.

   The 350 are bought as the strategy's own -- an intraday rebalance under
   signal:<name>:1, filled at 100.05 on Tuesday at 11:00 EDT, the session
   open -- and the cash is then set to 65,000, so E = 65,000 + 350 x 100 =
   100,000 at the mark of 100. (The order cap is raised to 50,000: the buy is
   35,035 at its limit.) Its accepted document (w 1.0 on AAPL, as_of Monday)
   and its sizing row (sequence 1, scale 0.70, "open") are journaled as the
   intake would have. The overlay is dated five minutes before the venue
   clock's 11:00 and judged by the pass at once: fresh. With no drawdown the
   governor is 1, and an incubation cap of 0.5 over this one strategy's 0.5
   is a ratio of 1, so the effective scale is the overlay's, for either
   tier. The pass's waves are settled and waited on before it returns. *)
let adjust_case ?(last_scale = 0.70) ?last_ratio ?last_factor ?last_governor
    ?(sizing_row = true) ?(stale = false) ?(incubation_cap = 0.5)
    ?(drawdown = fun () -> None) ?dd_cap ?(adjust_now = true) ?(bought = true) ?unfilled
    ~(sizing : Ohcamel.Config.Book.Signals_spec.sizing) ~overlay () =
  let name = "exp_adjust" in
  let f = fixture () in
  record_close f;
  (* 11:00 EDT on Tuesday when the case starts, and then the case's clock:
     the duplicate rule reads the manager's now, and a case that proposes
     the same order again hundreds of seconds later must not be refused as
     a double-click because the clock never moved. *)
  let now () = Time_ns.add tuesday_mid (Time_ns.diff (Time_source.now f.clock) t0) in
  let oms = manager ~incubation_cap ~max_order_notional:50_000.0 ~now f in
  let source =
    {
      D.Rebalance.Source.strategy = name;
      sequence = 1;
      as_of = Date.of_string "2026-09-14";
    }
  in
  let%bind () =
    if not bought then (
      (* Nothing of its own: E = 100,000 is all cash. *)
      Graph.set_cash f.graph (Notional.of_float 100_000.0);
      return ())
    else
      let bought =
        D.Oms.propose_rebalance oms ~mode:D.Rebalance.Mode.Intraday ~source
          ~legs:[ rebalance_leg aapl D.Order.Side.Buy 350 ]
      in
      let%bind () = settle () in
      let%bind () = pump f in
      let%map () = advance_to f 2.0 in
      (match Deferred.peek bought with
      | Some (D.Oms.Rebalance_outcome.Sent [ _ ]) -> ()
      | Some other ->
          Alcotest.failf "not bought: %s" (D.Oms.Rebalance_outcome.to_string other)
      | None -> Alcotest.fail "the buy is still waiting");
      Graph.set_cash f.graph (Notional.of_float 65_000.0)
  in
  (* [unfilled]: a market-on-open order of the strategy's, journaled under
     its source as the evening's rebalance would have -- sent and
     acknowledged at [evening] -- that the venue then expired at the open,
     09:31 on Tuesday, with nothing filled: Alpaca's paper venue does this
     to most of them. Each as (side, qty, how it ended). *)
  List.iter (Option.value unfilled ~default:[]) ~f:(fun (side, qty, ended) ->
      let request =
        {
          D.Order.Request.client_order_id =
            D.Ids.Client_order_id.generate ~now:evening
              ~rng:(Random.State.make [| 11; qty |]);
          symbol = aapl;
          side;
          qty;
          kind = D.Order.Kind.Market;
          tif = D.Order.Tif.Opg;
        }
      in
      let o = D.Order.create request in
      D.Journal.insert_orders f.journal
        [
          {
            D.Journal.Insert.order = o;
            source = D.Rebalance.Source.to_string source;
            decision_price = Price.of_float 100.0;
            arrival = None;
            verdict = `Assoc [];
          };
        ]
        ~at:evening;
      let step o event ~at =
        let o, _ = D.Order.apply o event in
        D.Journal.update_order f.journal o ~event ~anomaly:None ~at;
        o
      in
      let opened = Time_ns.of_string_with_utc_offset "2026-09-15T13:31:00Z" in
      match ended with
      | `Expired ->
          let o = step o (D.Order.Event.Acknowledged "v-1") ~at:evening in
          ignore (step o D.Order.Event.Venue_expired ~at:opened : D.Order.t)
      | `Rejected ->
          ignore
            (step o
               (D.Order.Event.Venue_rejected_submission
                  "403: potential wash trade detected. use complex orders")
               ~at:evening
              : D.Order.t));
  mark f aapl 100.0;
  let zeros = "sha256:" ^ String.make 64 '0' in
  D.Journal.record_signal f.journal
    {
      D.Journal.Signal.strategy = name;
      sequence = 1;
      as_of = Date.of_string "2026-09-14";
      received_at = evening;
      verdict =
        (match sizing with
        | Ohcamel.Config.Book.Signals_spec.Incubation ->
            D.Journal.Signal.Verdict.Accepted_incubation
        | _ -> D.Journal.Signal.Verdict.Accepted);
      rule = None;
      detail = "passed";
      document =
        sprintf
          {|{"schema_version":1,"strategy":"%s","params_hash":"%s","as_of":"2026-09-14","computed_at":"2026-09-14T23:15:00Z","data_hash":"%s","sequence":1,"validation":{"status":"pass","gates_version":"2026-09-02"},"targets":[{"symbol":"AAPL","weight":1.0}]}|}
          name zeros zeros;
      rebalance = Some "sent";
    };
  if sizing_row then
    D.Journal.record_sizing f.journal ~strategy:name ~sequence:1 ~at:evening
      ~tier:(Ohcamel.Config.Book.Signals_spec.sizing_to_string sizing)
      ~scale:last_scale ~allocated:35_000.0 ~mode:"open" ?ratio:last_ratio
      ?factor:last_factor ?governor:last_governor;
  let dir = Filename_unix.temp_dir "ohcamel-signals-" "" in
  (* [stale]: the overlay was accepted fourteen hours before the venue's
     11:00 -- last evening's -- and is recorded as the intake would have,
     with no file left to judge. *)
  if stale then
    D.Journal.record_overlay f.journal ~sequence:7 ~as_of:"2026-09-14T22:50:00Z"
      ~received_at:(Time_ns.sub tuesday_mid (Time_ns.Span.of_hr 14.0))
      ~exposure_scale:(Float.of_string overlay) ~verdict:`Accepted ~rule:None
      ~detail:"passed O1-O4"
      ~document:
        (sprintf
           {|{"schema_version":1,"kind":"overlay","as_of":"2026-09-14T22:50:00Z","computed_at":"2026-09-14T22:50:05Z","sequence":7,"exposure_scale":%s,"inputs":{},"reported":{}}|}
           overlay)
  else
    Out_channel.write_all
      (Filename.concat dir "overlay-7.json")
      ~data:
        (sprintf
           {|{"schema_version":1,"kind":"overlay","as_of":"2026-09-15T14:55:00Z","computed_at":"2026-09-15T14:55:05Z","sequence":7,"exposure_scale":%s,"inputs":{},"reported":{}}|}
           overlay);
  let intake =
    D.Intake.create
      ~gauge:
        (D.Intake.Gauge.create ~spec:(D.Oms.spec oms)
           ~venue_now:(fun () -> Some (now ()))
           ~drawdown ~dd_cap)
      ~journal:f.journal ~dir
      ~strategies:
        [
          {
            Ohcamel.Config.Book.Signals_spec.Strategy.name;
            symbols = [ "AAPL" ];
            max_age = 3;
            sizing;
            capital_fraction = 0.5;
            cadence = Ohcamel.Config.Book.Signals_spec.Cadence.Daily;
            adjust = Ohcamel.Config.Book.Signals_spec.Adjust.Intraday;
            max_age_minutes = 20;
            horizon = Ohcamel.Config.Book.Signals_spec.Horizon.Carry;
            stop = None;
            reinstated = None;
          };
        ]
      ~universe:[ aapl; msft; xom ] ~on_event:(Queue.enqueue f.events) ~now ()
  in
  let pass_and_adjust ~at =
    ignore (D.Intake.judge_pass intake : D.Intake.Accepted.t list);
    let adjusted = D.Intake.adjust intake ~oms:(Some oms) in
    let%bind () = settle () in
    let%bind () = pump f in
    let%bind () = advance_to f at in
    if not (Deferred.is_determined adjusted) then
      Alcotest.fail "the adjust pass is still waiting";
    return ()
  in
  let%map () = if adjust_now then pass_and_adjust ~at:4.0 else return () in
  let cleanup () =
    Array.iter (Sys_unix.readdir dir) ~f:(fun n ->
        Core_unix.unlink (Filename.concat dir n));
    Core_unix.rmdir dir;
    Graph.destroy f.graph
  in
  {
    a_f = f;
    a_name = name;
    a_oms = oms;
    a_intake = intake;
    a_dir = dir;
    pass_and_adjust;
    cleanup;
  }

(* Every order of the strategy as (side, qty, kind tif), sorted: two orders
   journaled inside one millisecond have no order of their own. *)
let strategy_orders f name =
  List.sort ~compare:[%compare: string * int * string]
  @@ List.rev_filter_map (D.Journal.recent_orders f.journal ~limit:20) ~f:(fun r ->
      if String.equal r.D.Journal.Order_row.source (sprintf "signal:%s:1" name) then
        let q = r.D.Journal.Order_row.order.D.Order.request in
        Some
          ( D.Order.Side.to_string q.D.Order.Request.side,
            q.D.Order.Request.qty,
            sprintf "%s %s"
              (D.Order.Kind.to_string q.D.Order.Request.kind)
              (D.Order.Tif.to_string q.D.Order.Request.tif) )
      else None)

let latest_sizing_mode f name =
  let mode = ref "" in
  ignore
    (Sqlite3.exec_not_null_no_headers
       (D.Journal.For_testing.db f.journal)
       ~cb:(fun row -> mode := row.(0))
       (sprintf "SELECT mode FROM sizings WHERE strategy = '%s' ORDER BY at DESC LIMIT 1"
          name)
      : Sqlite3.Rc.t);
  !mode

(* 0.70 to 0.65 is 0.05, inside the band of 0.10: nothing is proposed, and
   the sizing row is the open one. *)
let test_adjust_within_band_does_nothing () =
  let%map { a_f = f; a_name = name; cleanup; _ } =
    adjust_case ~sizing:Ohcamel.Config.Book.Signals_spec.Live ~overlay:"0.65" ()
  in
  Alcotest.(check (list (triple string int string)))
    "the buy alone"
    [ ("buy", 350, "limit day") ]
    (strategy_orders f name);
  Alcotest.(check (pair (option (float 1e-12)) string))
    "still sized at 0.70, open" (Some 0.70, "open")
    ( Option.map (D.Journal.latest_sizing f.journal ~strategy:name)
        ~f:(fun (_, s, _, _, _) -> s),
      latest_sizing_mode f name );
  cleanup ()

(* 0.70 to 0.55 is 0.15, beyond the band: re-sized from its document at
   0.55. Live, w 1.0, f 0.5, E 100,000, p 100: the target is
   trunc(1.0 x 0.5 x 0.55 x 100,000 / 100) = 275, from the strategy's own
   350: Sell 75, a day limit (the intraday mode), filled. The sizing row:
   scale 0.55, allocated 0.5 x 0.55 x 100,000 = 27,500, mode adjust. *)
let test_adjust_beyond_band_re_sizes () =
  let%map { a_f = f; a_name = name; cleanup; _ } =
    adjust_case ~sizing:Ohcamel.Config.Book.Signals_spec.Live ~overlay:"0.55" ()
  in
  Alcotest.(check (list (triple string int string)))
    "the buy, then Sell 75 as a day limit"
    [ ("buy", 350, "limit day"); ("sell", 75, "limit day") ]
    (strategy_orders f name);
  Alcotest.(check (list (pair string (float 0.0))))
    "the strategy now holds 275"
    [ ("AAPL", 275.0) ]
    (List.map
       (D.Journal.source_fills f.journal ~prefix:(sprintf "signal:%s:" name))
       ~f:(fun (s, q) -> (Symbol.to_string s, q)));
  (match D.Journal.latest_sizing f.journal ~strategy:name with
  | None -> Alcotest.fail "no sizing row"
  | Some (sequence, scale, allocated, _, tier) ->
      Alcotest.(check (pair (triple int (float 1e-12) (float 1e-6)) (pair string string)))
        "sequence 1, scale 0.55, 27,500, live, adjust"
        ((1, 0.55, 27_500.0), ("live", "adjust"))
        ((sequence, scale, allocated), (tier, latest_sizing_mode f name)));
  cleanup ()

(* An accepted overlay of zero (review focus 1): the incubation strategy's
   whole position, 350, is sold in mode adjust, its sizing row at scale 0,
   and one overlay_zero alert says so. A second pass and adjust write no
   second alert and send nothing more: the strategy is sized at 0 now. *)
let test_overlay_zero_flattens_and_alerts () =
  let%bind { a_f = f; a_name = name; pass_and_adjust; cleanup; _ } =
    adjust_case ~sizing:Ohcamel.Config.Book.Signals_spec.Incubation ~overlay:"0.0" ()
  in
  let alerts () =
    List.filter_map (D.Journal.recent_alerts f.journal ~limit:20) ~f:(fun a ->
        if String.equal a.D.Journal.Alert.kind "overlay_zero" then
          Some (a.D.Journal.Alert.limit_name, a.D.Journal.Alert.line)
        else None)
  in
  let expected_alert = [ ("overlay", D.Intake.overlay_zero_line ~sequence:7) ] in
  Alcotest.(check (list (pair string string))) "one alert" expected_alert (alerts ());
  Alcotest.(check string)
    "its line"
    "overlay 7 says zero exposure: every strategy that adjusts intraday is flattened"
    (D.Intake.overlay_zero_line ~sequence:7);
  Alcotest.(check (list (triple string int string)))
    "the buy, then the whole position sold"
    [ ("buy", 350, "limit day"); ("sell", 350, "limit day") ]
    (strategy_orders f name);
  (match D.Journal.latest_sizing f.journal ~strategy:name with
  | None -> Alcotest.fail "no sizing row"
  | Some (_, scale, allocated, _, tier) ->
      Alcotest.(check (pair (pair (float 0.0) (float 0.0)) (pair string string)))
        "scale 0, nothing allocated, incubation, adjust"
        ((0.0, 0.0), ("incubation", "adjust"))
        ((scale, allocated), (tier, latest_sizing_mode f name)));
  let received = D.Sim_venue.received f.venue in
  let%map () = pass_and_adjust ~at:8.0 in
  Alcotest.(check (pair (list (pair string string)) int))
    "still one alert, and nothing more sent" (expected_alert, received)
    (alerts (), D.Sim_venue.received f.venue);
  cleanup ()

(* An unknown drawdown holds the governor (the fix round's ruling B). The
   graph's drawdown reads 0 against a 2 % cap, a governor of 1, and the
   overlay of 0.70 equals the last sizing: nothing moves. Then the drawdown
   raises: the governor holds at 1 -- never NaN, never 0 -- so two more
   passes sell nothing, and the hold is said once. *)
let test_an_unknown_drawdown_holds_the_governor_and_sells_nothing () =
  let read = ref (fun () -> Some 0.0) in
  let%bind { a_f = f; a_name = name; a_intake = intake; pass_and_adjust; cleanup; _ } =
    adjust_case
      ~drawdown:(fun () -> !read ())
      ~dd_cap:0.02 ~sizing:Ohcamel.Config.Book.Signals_spec.Live ~overlay:"0.70" ()
  in
  Alcotest.(check (float 0.0))
    "governor 1 from a drawdown of 0" 1.0
    (D.Intake.current_reading intake).D.Intake.Reading.governor;
  (read := fun () -> failwith "the graph is not stabilized");
  let%bind () = pass_and_adjust ~at:6.0 in
  let%map () = pass_and_adjust ~at:8.0 in
  let r = D.Intake.current_reading intake in
  Alcotest.(check (pair (float 0.0) bool))
    "held at 1" (1.0, true)
    (r.D.Intake.Reading.governor, r.D.Intake.Reading.governor_held);
  Alcotest.(check int)
    "said once" 1
    (List.count (Queue.to_list f.events)
       ~f:(String.is_substring ~substring:"drawdown cannot be read"));
  Alcotest.(check (list (triple string int string)))
    "nothing sold"
    [ ("buy", 350, "limit day") ]
    (strategy_orders f name);
  cleanup ()

(* The band is bypassed only by an overlay of exactly 0 (ruling C). A
   drawdown at its 2 % cap is a governor of 0, so with the overlay at 0.7 the
   product is 0. Last sized at 0.05, that is a move of 0.05, inside the band:
   nothing. Last sized at 0.70 it is a move of 0.70, beyond it: re-sized by
   the band rule to a target of 0, Sell 350, scale 0, mode adjust. *)
let test_a_product_of_zero_moves_by_the_band_alone () =
  let%bind { a_f = f; a_name = name; cleanup; _ } =
    adjust_case ~last_scale:0.05
      ~drawdown:(fun () -> Some 0.02)
      ~dd_cap:0.02 ~sizing:Ohcamel.Config.Book.Signals_spec.Live ~overlay:"0.7" ()
  in
  Alcotest.(check (pair (list (triple string int string)) string))
    "inside the band: nothing"
    ([ ("buy", 350, "limit day") ], "open")
    (strategy_orders f name, latest_sizing_mode f name);
  cleanup ();
  let%map { a_f = f; a_name = name; cleanup; _ } =
    adjust_case
      ~drawdown:(fun () -> Some 0.02)
      ~dd_cap:0.02 ~sizing:Ohcamel.Config.Book.Signals_spec.Live ~overlay:"0.7" ()
  in
  Alcotest.(
    check (pair (list (triple string int string)) (pair (option (float 0.0)) string)))
    "beyond it: sold to 0, by the band"
    ([ ("buy", 350, "limit day"); ("sell", 350, "limit day") ], (Some 0.0, "adjust"))
    ( strategy_orders f name,
      ( Option.map (D.Journal.latest_sizing f.journal ~strategy:name)
          ~f:(fun (_, s, _, _, _) -> s),
        latest_sizing_mode f name ) );
  cleanup ()

(* The minute loop over the adjust case at 0.55, its Sell 75 never filled
   (the venue is not pumped), so the adjust pass waits on its wave. [run]'s
   first pass starts it at t0 + 2 s; the next pass, at t0 + 62 s, runs all
   the same and judges overlay-8.json, written meanwhile, while the sell is
   still open. *)
let run_over_a_held_adjust () =
  let%bind a =
    adjust_case ~adjust_now:false ~sizing:Ohcamel.Config.Book.Signals_spec.Live
      ~overlay:"0.55" ()
  in
  don't_wait_for
    (D.Intake.run
       ~time_source:(Time_source.read_only a.a_f.clock)
       ~oms:a.a_oms a.a_intake);
  let%map () = settle () in
  Out_channel.write_all
    (Filename.concat a.a_dir "overlay-8.json")
    ~data:
      {|{"schema_version":1,"kind":"overlay","as_of":"2026-09-15T14:56:00Z","computed_at":"2026-09-15T14:56:05Z","sequence":8,"exposure_scale":0.56,"inputs":{},"reported":{}}|};
  a

let open_of f name =
  List.count (D.Journal.open_orders f.journal) ~f:(fun r ->
      String.equal r.D.Journal.Order_row.source (sprintf "signal:%s:1" name))

let test_run_does_not_wait_for_the_adjust_pass () =
  let%bind a = run_over_a_held_adjust () in
  Alcotest.(check int) "the sell rests" 1 (open_of a.a_f a.a_name);
  let%map () = advance_to a.a_f 62.0 in
  Alcotest.(check (pair bool int))
    "the next pass judged overlay 8, the sell still open" (true, 1)
    (D.Journal.overlay_recorded a.a_f.journal ~sequence:8, open_of a.a_f a.a_name);
  a.cleanup ()

let test_a_busy_adjust_is_skipped_and_said_once () =
  let%bind a = run_over_a_held_adjust () in
  let sent = D.Sim_venue.received a.a_f.venue in
  let%bind () = advance_to a.a_f 62.0 in
  let%map () = advance_to a.a_f 122.0 in
  Alcotest.(check (pair int int))
    "two passes skipped: said once, nothing more sent" (1, sent)
    ( List.count (Queue.to_list a.a_f.events)
        ~f:(String.is_substring ~substring:"the last adjust pass is still running"),
      D.Sim_venue.received a.a_f.venue );
  Alcotest.(check (list (triple string int string)))
    "the buy and the one sell"
    [ ("buy", 350, "limit day"); ("sell", 75, "limit day") ]
    (strategy_orders a.a_f a.a_name);
  a.cleanup ()

(* ------------------------------------------------------------------------ *)
(* The stage review's fix wave: the retry set, the stale hold, the ratio,   *)
(* and the sizing row only for a whole rebalance                             *)
(* ------------------------------------------------------------------------ *)

let live_strategy ?(name = "exp_a01_aapl") ?(symbols = [ "AAPL" ])
    ?(capital_fraction = 0.01) ?(adjust = Ohcamel.Config.Book.Signals_spec.Adjust.Open) ()
    =
  {
    Ohcamel.Config.Book.Signals_spec.Strategy.name;
    symbols;
    max_age = 3;
    sizing = Ohcamel.Config.Book.Signals_spec.Live;
    capital_fraction;
    cadence = Ohcamel.Config.Book.Signals_spec.Cadence.Daily;
    adjust;
    max_age_minutes = 20;
    horizon = Ohcamel.Config.Book.Signals_spec.Horizon.Carry;
    stop = None;
    reinstated = None;
  }

let write_signal dir ~strategy ~sequence ?(as_of = "2026-09-14")
    ?(targets = {|[{"symbol":"AAPL","weight":1.0}]|}) () =
  let zeros = "sha256:" ^ String.make 64 '0' in
  Out_channel.write_all
    (Filename.concat dir (sprintf "%s-%d.json" strategy sequence))
    ~data:
      (sprintf
         {|{"schema_version":1,"strategy":"%s","params_hash":"%s","as_of":"%s","computed_at":"%sT22:45:00Z","data_hash":"%s","sequence":%d,"validation":{"status":"pass","gates_version":"2026-09-02"},"targets":%s}|}
         strategy zeros as_of as_of zeros sequence targets)

type evening_fixture = {
  e_f : fixture;
  e_oms : D.Oms.t;
  e_dir : string;
  e_clock : Time_source.Read_write.t;
  e_venue : bool ref;
  e_intake : D.Intake.t;
  e_cleanup : unit -> unit;
}

(* An evening on its own clock, from [start]: Monday's close recorded,
   the venue's session closed until Tuesday's open, and an intake whose
   venue clock is the case's clock while [e_venue] holds -- unset, no venue
   reading has arrived. The order manager's clock is the case's too, so the
   opening auction's 19:00 ET is crossed by advancing it. *)
let evening_case ?(venue = true) ?(max_order_notional = 25_000.0) ~start ~strategies () =
  let f = fixture () in
  record_close f;
  let clock = Time_source.create ~now:(Time_ns.of_string_with_utc_offset start) () in
  let now () = Time_source.now clock in
  let oms = manager ~max_order_notional ~now f in
  after_the_close f oms;
  let dir = Filename_unix.temp_dir "ohcamel-signals-" "" in
  let venue = ref venue in
  let intake =
    D.Intake.create
      ~gauge:
        (D.Intake.Gauge.create ~spec:(D.Oms.spec oms)
           ~venue_now:(fun () -> if !venue then Some (now ()) else None)
           ~drawdown:(fun () -> None)
           ~dd_cap:None)
      ~journal:f.journal ~dir ~strategies ~universe:[ aapl; msft; xom ]
      ~on_event:(Queue.enqueue f.events) ~now ()
  in
  let cleanup () =
    Array.iter (Sys_unix.readdir dir) ~f:(fun n ->
        Core_unix.unlink (Filename.concat dir n));
    Core_unix.rmdir dir;
    Graph.destroy f.graph
  in
  {
    e_f = f;
    e_oms = oms;
    e_dir = dir;
    e_clock = clock;
    e_venue = venue;
    e_intake = intake;
    e_cleanup = cleanup;
  }

let start_run e =
  don't_wait_for
    (D.Intake.run ~time_source:(Time_source.read_only e.e_clock) ~oms:e.e_oms e.e_intake);
  settle ()

let advance_clock e at =
  let%bind () =
    Time_source.advance_by_alarms ~wait_for:settle e.e_clock
      ~to_:(Time_ns.of_string_with_utc_offset at)
  in
  settle ()

let rebalance_of f ~strategy ~sequence =
  Option.bind (D.Journal.signal f.journal ~strategy ~sequence) ~f:(fun s ->
      s.D.Journal.Signal.rebalance)

let orders_of f =
  List.map (D.Journal.recent_orders f.journal ~limit:20) ~f:(fun r ->
      let q = r.D.Journal.Order_row.order.D.Order.request in
      ( r.D.Journal.Order_row.source,
        Symbol.to_string q.D.Order.Request.symbol,
        q.D.Order.Request.qty ))

(* A-I1. A document judged at 18:46 EDT (22:46Z) is refused by the opening
   auction's window -- before 19:00 ET -- and that refusal is one of timing:
   the judgement stays pending, nothing is journaled, and it is tried again
   every pass. Overlay 1, the 18:50 run's, judged at the 18:51 pass, says
   0.8. At 18:59 it still waits; the pass at 19:00 sizes it at the overlay's
   0.8 (held, nine minutes old: not stale; governor 1): trunc(1.0 x 0.01 x 0.8 x
   1,000,000 / 100) = 80 shares of AAPL, one market-on-open order under
   signal:exp_a01_aapl:1, its sizing row at 23:00:00Z with scale 0.8, ratio
   1, factor 0.8 and governor 1. *)
let test_a_document_judged_before_19_is_sized_at_the_first_pass_after () =
  let e =
    evening_case ~start:"2026-09-14T22:46:00Z" ~strategies:[ live_strategy () ] ()
  in
  write_signal e.e_dir ~strategy:"exp_a01_aapl" ~sequence:1 ();
  let%bind () = start_run e in
  let f = e.e_f in
  Alcotest.(check (pair (option string) int))
    "18:46: still pending, nothing journaled"
    (Some (D.Intake.pending ~strategy:"exp_a01_aapl" ~sequence:1), 0)
    (rebalance_of f ~strategy:"exp_a01_aapl" ~sequence:1, List.length (orders_of f));
  let%bind () = advance_clock e "2026-09-14T22:50:00Z" in
  Out_channel.write_all
    (Filename.concat e.e_dir "overlay-1.json")
    ~data:
      {|{"schema_version":1,"kind":"overlay","as_of":"2026-09-14T22:50:00Z","computed_at":"2026-09-14T22:50:05Z","sequence":1,"exposure_scale":0.8,"inputs":{},"reported":{}}|};
  let%bind () = advance_clock e "2026-09-14T22:59:00Z" in
  Alcotest.(check (triple bool (option string) int))
    "18:59: the overlay judged, the document still pending"
    (true, Some (D.Intake.pending ~strategy:"exp_a01_aapl" ~sequence:1), 0)
    ( D.Journal.overlay_recorded f.journal ~sequence:1,
      rebalance_of f ~strategy:"exp_a01_aapl" ~sequence:1,
      List.length (orders_of f) );
  Alcotest.(check int)
    "the wait said once, not once a minute" 1
    (List.count (Queue.to_list f.events)
       ~f:(String.is_substring ~substring:"waits, still pending"));
  let%map () = advance_clock e "2026-09-14T23:00:00Z" in
  Alcotest.(check (list (triple string string int)))
    "19:00: one order, AAPL 80, from sequence 1"
    [ ("signal:exp_a01_aapl:1", "AAPL", 80) ]
    (orders_of f);
  Alcotest.(check bool)
    "its sentence: sent" true
    (String.is_prefix
       (Option.value ~default:"" (rebalance_of f ~strategy:"exp_a01_aapl" ~sequence:1))
       ~prefix:"1 market-on-open order journaled, then sent");
  (match D.Journal.latest_sizing_row f.journal ~strategy:"exp_a01_aapl" with
  | None -> Alcotest.fail "no sizing row"
  | Some z ->
      Alcotest.(check (pair string (list (option (float 1e-12)))))
        "sized at 23:00, scale 0.8: ratio 1, factor 0.8, governor 1"
        ("2026-09-14T23:00:00.000000000Z", [ Some 0.8; Some 1.0; Some 0.8; Some 1.0 ])
        (z.D.Journal.Sizing.at, [ Some z.scale; z.ratio; z.factor; z.governor ]));
  e.e_cleanup ()

(* A-I1 and A-I2. A restart at 19:15 EDT before any venue reading: the pass
   judges the document accepted, and nothing is sized -- the overlay cannot
   be aged and the session cannot be timed, and sizing now would be at the
   fallback, 0.5. A minute later, still nothing, and the wait is said once.
   The reading arrives; the next pass sizes it at the overlay's value, which
   with no overlay ever accepted is absent: 1.0 x governor 1, so
   trunc(1.0 x 0.01 x 1.0 x 1,000,000 / 100) = 100 shares, never the
   fallback's 50. *)
let test_a_restart_sizes_nothing_until_the_venue_clock_answers () =
  let e =
    evening_case ~venue:false ~start:"2026-09-14T23:15:00Z"
      ~strategies:[ live_strategy () ]
      ()
  in
  write_signal e.e_dir ~strategy:"exp_a01_aapl" ~sequence:1 ();
  let%bind () = start_run e in
  let f = e.e_f in
  let%bind () = advance_clock e "2026-09-14T23:16:00Z" in
  Alcotest.(check (pair (option string) int))
    "no reading: pending, nothing journaled"
    (Some (D.Intake.pending ~strategy:"exp_a01_aapl" ~sequence:1), 0)
    (rebalance_of f ~strategy:"exp_a01_aapl" ~sequence:1, List.length (orders_of f));
  Alcotest.(check int)
    "said once" 1
    (List.count (Queue.to_list f.events)
       ~f:
         (String.is_substring
            ~substring:"no venue clock has answered, so nothing is sized"));
  e.e_venue := true;
  let%map () = advance_clock e "2026-09-14T23:17:00Z" in
  Alcotest.(check (list (triple string string int)))
    "the reading arrives: AAPL 100, at the absent overlay's 1.0, never the fallback's 50"
    [ ("signal:exp_a01_aapl:1", "AAPL", 100) ]
    (orders_of f);
  Alcotest.(check (option (float 1e-12)))
    "sized at 1.0" (Some 1.0)
    (Option.map (D.Journal.latest_sizing_row f.journal ~strategy:"exp_a01_aapl")
       ~f:(fun z -> z.D.Journal.Sizing.scale));
  e.e_cleanup ()

(* A-I1 and R5. Sequence 1 is judged at 18:46 and waits for 19:00; at 18:55
   sequence 2 of the same strategy arrives and is accepted. Sequence 1's
   retry is closed -- "not sized: sequence 2 of the same strategy was
   accepted after it" -- and at 19:00 sequence 2 alone is sized: one order,
   from 2. *)
let test_a_superseding_document_cancels_the_older_retry () =
  let e =
    evening_case ~start:"2026-09-14T22:46:00Z" ~strategies:[ live_strategy () ] ()
  in
  write_signal e.e_dir ~strategy:"exp_a01_aapl" ~sequence:1 ();
  let%bind () = start_run e in
  let f = e.e_f in
  write_signal e.e_dir ~strategy:"exp_a01_aapl" ~sequence:2 ();
  let%bind () = advance_clock e "2026-09-14T22:55:00Z" in
  Alcotest.(check (pair (option string) (option string)))
    "1 closed as superseded, 2 pending"
    ( Some
        "not sized: sequence 2 of the same strategy was accepted after it, and only the \
         latest is sized",
      Some (D.Intake.pending ~strategy:"exp_a01_aapl" ~sequence:2) )
    ( rebalance_of f ~strategy:"exp_a01_aapl" ~sequence:1,
      rebalance_of f ~strategy:"exp_a01_aapl" ~sequence:2 );
  let%map () = advance_clock e "2026-09-14T23:00:00Z" in
  Alcotest.(check (list (triple string string int)))
    "19:00: one order, from sequence 2"
    [ ("signal:exp_a01_aapl:2", "AAPL", 100) ]
    (orders_of f);
  e.e_cleanup ()

(* A-I1: a FINAL refusal still ends the judgement. At 19:15 EDT, a fraction
   of 0.15 is 1,500 AAPL at 100 = 150,000 of TECH, over tech-cap's 100,000
   (the order cap raised to 500,000 so the gate is what refuses): the gate's
   refusal is written over the pending sentence, and a minute later nothing
   is tried again. *)
let test_a_final_refusal_still_ends_the_judgement () =
  let e =
    evening_case ~max_order_notional:500_000.0 ~start:"2026-09-14T23:15:00Z"
      ~strategies:[ live_strategy ~capital_fraction:0.15 () ]
      ()
  in
  write_signal e.e_dir ~strategy:"exp_a01_aapl" ~sequence:1 ();
  let%bind () = start_run e in
  let f = e.e_f in
  let%map () = advance_clock e "2026-09-14T23:16:00Z" in
  let sentence =
    Option.value ~default:"" (rebalance_of f ~strategy:"exp_a01_aapl" ~sequence:1)
  in
  Alcotest.(check bool)
    (sprintf "the gate's refusal is the outcome: %s" sentence)
    true
    (String.is_prefix sentence
       ~prefix:"refused, and nothing journaled or sent: the gate refuses");
  Alcotest.(check (pair int int))
    "nothing journaled, no sizing row" (0, 0)
    ( List.length (orders_of f),
      Option.value_map (D.Journal.latest_sizing_row f.journal ~strategy:"exp_a01_aapl")
        ~default:0 ~f:(fun _ -> 1) );
  e.e_cleanup ()

(* A-I3. The morning's first adjust, with last evening's overlay 14 hours
   old: stale. The strategy was last sized at 1.0 (factor 1, governor 1,
   ratio 1); the stale reading's effective scale is the fallback, 0.5, which
   before the fix sold it to 0.5 every morning. Now the pass holds: nothing
   is proposed, the sizing is untouched, and the hold is said once over two
   passes. Then the governor falls to 0.5 -- a drawdown of 1.5 % against a
   2 % cap -- while the overlay is still stale: a move of 0.5, beyond the
   band, and the strategy is re-sized at the factor it was last sized with
   times the governor now, 1.0 x 0.5 = 0.5 (not the fallback's 0.5 x 0.5):
   trunc(1.0 x 0.5 x 0.5 x 100,000 / 100) = 250 from its own 350, Sell 100
   as a day limit; the row at scale 0.5, mode adjust, note governor, factor
   1, governor 0.5. *)
let test_a_stale_overlay_holds_and_a_governor_move_re_sizes () =
  let drawdown = ref 0.0 in
  let%bind { a_f = f; a_name = name; pass_and_adjust; cleanup; _ } =
    adjust_case ~stale:true ~last_scale:1.0 ~last_ratio:1.0 ~last_factor:1.0
      ~last_governor:1.0
      ~drawdown:(fun () -> Some !drawdown)
      ~dd_cap:0.02 ~sizing:Ohcamel.Config.Book.Signals_spec.Live ~overlay:"0.9" ()
  in
  let%bind () = pass_and_adjust ~at:6.0 in
  Alcotest.(check (pair (list (triple string int string)) string))
    "stale: nothing proposed, still sized at the open's row"
    ([ ("buy", 350, "limit day") ], "open")
    (strategy_orders f name, latest_sizing_mode f name);
  Alcotest.(check int)
    "the hold said once over two passes" 1
    (List.count (Queue.to_list f.events)
       ~f:(String.is_substring ~substring:"holds every strategy at its last sizing"));
  drawdown := 0.015;
  let%map () = pass_and_adjust ~at:10.0 in
  Alcotest.(check (list (triple string int string)))
    "the governor's move: Sell 100"
    [ ("buy", 350, "limit day"); ("sell", 100, "limit day") ]
    (strategy_orders f name);
  (match D.Journal.latest_sizing_row f.journal ~strategy:name with
  | None -> Alcotest.fail "no sizing row"
  | Some z ->
      Alcotest.(
        check
          (pair
             (pair (float 1e-12) (pair string (option string)))
             (list (option (float 1e-12)))))
        "scale 0.5, adjust, governor; factor 1, governor 0.5"
        ((0.5, ("adjust", Some "governor")), [ Some 1.0; Some 0.5 ])
        ((z.D.Journal.Sizing.scale, (z.mode, z.note)), [ z.factor; z.governor ]));
  cleanup ()

(* A-I4. An incubation strategy at the canary's ratio: a cap of 0.087 over
   its fraction of 0.5 is 0.174. It was last sized at overlay 1.0, the
   product 0.174, with that ratio recorded. The overlay falls to 0.85: the
   PRODUCT moves only 1.0 x 0.174 - 0.85 x 0.174 = 0.0261, inside the band,
   which before the fix held it; the EFFECTIVE scale moves 0.15, beyond it,
   and it is re-sized: trunc(1.0 x 0.5 x 0.85 x 0.174 x 100,000 / 100) =
   trunc(73.95) = 73 from 350, Sell 277; the row at 0.85 x 0.174 with the
   ratio beside it. Then a ratio that changed alone (a cap edited across a
   restart): last sized at 0.5 with a ratio of 1, the effective 0.5 unmoved,
   the ratio now 0.174 -- re-sized to 0.5 x 0.174 = 0.087. *)
let test_the_band_compares_effective_scales_at_the_canary_ratio () =
  let%bind { a_f = f; a_name = name; cleanup; _ } =
    adjust_case ~incubation_cap:0.087 ~last_scale:0.174 ~last_ratio:0.174 ~last_factor:1.0
      ~last_governor:1.0 ~sizing:Ohcamel.Config.Book.Signals_spec.Incubation
      ~overlay:"0.85" ()
  in
  Alcotest.(check (list (triple string int string)))
    "re-sized: Sell 277"
    [ ("buy", 350, "limit day"); ("sell", 277, "limit day") ]
    (strategy_orders f name);
  Alcotest.(check (option (pair (float 1e-12) (option (float 1e-12)))))
    "scale 0.85 x 0.174, ratio 0.174"
    (Some (0.85 *. 0.174, Some 0.174))
    (Option.map (D.Journal.latest_sizing_row f.journal ~strategy:name) ~f:(fun z ->
         (z.D.Journal.Sizing.scale, z.ratio)));
  cleanup ();
  let%map { a_f = f; a_name = name; cleanup; _ } =
    adjust_case ~incubation_cap:0.087 ~last_scale:0.5 ~last_ratio:1.0 ~last_factor:0.5
      ~last_governor:1.0 ~sizing:Ohcamel.Config.Book.Signals_spec.Incubation
      ~overlay:"0.5" ()
  in
  Alcotest.(check (option (pair (float 1e-12) string)))
    "the ratio's change alone re-sizes: 0.087, adjust"
    (Some (0.5 *. 0.174, "adjust"))
    (Option.map (D.Journal.latest_sizing_row f.journal ~strategy:name) ~f:(fun z ->
         (z.D.Journal.Sizing.scale, z.mode)));
  cleanup ()

(* A-I5's addition. A strategy that holds 350 of its own and has an accepted
   document but no sizing for it -- a rebalance that held part of its size
   records none -- is re-sized on the next pass whatever the band: at 0.70
   its target is trunc(0.5 x 0.70 x 100,000 / 100) = 350, which it holds,
   so no order goes and the sizing row is written, mode adjust. *)
let test_an_unsized_strategy_holding_a_position_is_re_sized () =
  let%map { a_f = f; a_name = name; cleanup; _ } =
    adjust_case ~sizing_row:false ~sizing:Ohcamel.Config.Book.Signals_spec.Live
      ~overlay:"0.70" ()
  in
  Alcotest.(
    check (pair (list (triple string int string)) (option (pair (float 1e-12) string))))
    "no order, and the sizing row written at 0.70, adjust"
    ([ ("buy", 350, "limit day") ], Some (0.70, "adjust"))
    ( strategy_orders f name,
      Option.map (D.Journal.latest_sizing_row f.journal ~strategy:name) ~f:(fun z ->
          (z.D.Journal.Sizing.scale, z.mode)) );
  cleanup ()

(* The residuals: the unsized branch honours the stale hold. The same
   unsized strategy, holding 350 of its own, on a morning whose overlay is
   14 hours old: stale, so the pass holds it -- before the fix it was re-sized
   to the fallback's 0.5, trunc(0.5 x 0.5 x 100,000 / 100) = 250, Sell 100,
   and bought back at the first fresh overlay. No order, no sizing row. Then
   the first fresh overlay, 0.6: re-sized whatever the band, trunc(0.5 x 0.6
   x 100,000 / 100) = 300, Sell 50, the row at 0.6, adjust. *)
let test_an_unsized_strategy_holds_while_the_overlay_is_stale () =
  let%bind { a_f = f; a_name = name; a_dir = dir; pass_and_adjust; cleanup; _ } =
    adjust_case ~sizing_row:false ~stale:true
      ~sizing:Ohcamel.Config.Book.Signals_spec.Live ~overlay:"0.9" ()
  in
  Alcotest.(
    check (pair (list (triple string int string)) (option (pair (float 1e-12) string))))
    "stale: no order, no sizing row"
    ([ ("buy", 350, "limit day") ], None)
    ( strategy_orders f name,
      Option.map (D.Journal.latest_sizing_row f.journal ~strategy:name) ~f:(fun z ->
          (z.D.Journal.Sizing.scale, z.mode)) );
  Out_channel.write_all
    (Filename.concat dir "overlay-8.json")
    ~data:
      {|{"schema_version":1,"kind":"overlay","as_of":"2026-09-15T14:55:00Z","computed_at":"2026-09-15T14:55:05Z","sequence":8,"exposure_scale":0.6,"inputs":{},"reported":{}}|};
  let%map () = pass_and_adjust ~at:6.0 in
  Alcotest.(
    check (pair (list (triple string int string)) (option (pair (float 1e-12) string))))
    "the first fresh overlay: Sell 50, the row at 0.6, adjust"
    ([ ("buy", 350, "limit day"); ("sell", 50, "limit day") ], Some (0.6, "adjust"))
    ( strategy_orders f name,
      Option.map (D.Journal.latest_sizing_row f.journal ~strategy:name) ~f:(fun z ->
          (z.D.Journal.Sizing.scale, z.mode)) );
  cleanup ()

(* THE COMPLETION (the paper venue's finding of 2026-10-21: 55 of the desk's
   59 market-on-open orders expired at Alpaca's opening auction unfilled, and
   nothing sized them again). The strategy was sized at the open -- its row
   0.70, "open" -- and its one order, Buy 350 at the auction, expired with
   nothing filled; it holds nothing. The overlay reads 0.70, inside the band,
   so the band alone would hold it at nothing all month. The adjust pass
   finds the order ended unfilled after the sizing it belongs to, and sizes
   the strategy again from its document in the intraday mode: Buy 350 as a
   day limit, filled, the row 0.70, "complete". The next pass sends nothing
   more: the expired order is older than the completion's row. *)
let test_an_order_expired_at_the_auction_is_completed_intraday () =
  let%bind { a_f = f; a_name = name; pass_and_adjust; cleanup; _ } =
    adjust_case ~bought:false
      ~unfilled:[ (D.Order.Side.Buy, 350, `Expired) ]
      ~last_factor:0.70 ~last_governor:1.0 ~last_ratio:1.0
      ~sizing:Ohcamel.Config.Book.Signals_spec.Live ~overlay:"0.70" ()
  in
  let sizing () =
    Option.map (D.Journal.latest_sizing_row f.journal ~strategy:name) ~f:(fun z ->
        (z.D.Journal.Sizing.scale, z.mode, z.note))
  in
  Alcotest.(
    check
      (pair
         (list (triple string int string))
         (option (triple (float 1e-12) string (option string)))))
    "the expired auction order, then Buy 350 as a day limit; the row 0.70, complete"
    ( [ ("buy", 350, "limit day"); ("buy", 350, "market opg") ],
      Some (0.70, "complete", Some "completion") )
    (strategy_orders f name, sizing ());
  Alcotest.(check (list (pair string (float 0.0))))
    "the strategy now holds 350"
    [ ("AAPL", 350.0) ]
    (List.map
       (D.Journal.source_fills f.journal ~prefix:(sprintf "signal:%s:" name))
       ~f:(fun (s, q) -> (Symbol.to_string s, q)));
  let received = D.Sim_venue.received f.venue in
  let%map () = pass_and_adjust ~at:8.0 in
  Alcotest.(check (pair int (option (triple (float 1e-12) string (option string)))))
    "the next pass: nothing more sent, the row still the completion's"
    (received, Some (0.70, "complete", Some "completion"))
    (D.Sim_venue.received f.venue, sizing ());
  cleanup ()

(* The same, unsized: the evening's rebalance was stopped at its first order
   -- the venue refused it as a wash trade, so nothing after it was sent and
   no sizing row was written -- and the strategy holds nothing. Before, an
   unsized strategy holding nothing waited for its next document; now its
   refused order is the record that a sizing was attempted and did not
   hold, and it is completed: Buy 350, the row 0.70, "complete". *)
let test_a_rebalance_the_venue_refused_is_completed_intraday () =
  let%map { a_f = f; a_name = name; cleanup; _ } =
    adjust_case ~bought:false ~sizing_row:false
      ~unfilled:[ (D.Order.Side.Buy, 350, `Rejected) ]
      ~sizing:Ohcamel.Config.Book.Signals_spec.Live ~overlay:"0.70" ()
  in
  Alcotest.(
    check (pair (list (triple string int string)) (option (pair (float 1e-12) string))))
    "the refused order, then Buy 350 as a day limit; the row 0.70, complete"
    ([ ("buy", 350, "limit day"); ("buy", 350, "market opg") ], Some (0.70, "complete"))
    ( strategy_orders f name,
      Option.map (D.Journal.latest_sizing_row f.journal ~strategy:name) ~f:(fun z ->
          (z.D.Journal.Sizing.scale, z.mode)) );
  cleanup ()

(* A completion whose day limit never fills -- the venue's mark moves away
   before the wave is pumped, so the limit at 100.10 rests under an ask of
   101.05 -- is cancelled at the wave's timeout, and the cancelled order is
   itself unfilled: the next pass tries again. At most three times a
   session, so a name whose mark is wrong does not churn the venue all day:
   the fourth pass sends nothing, and says why once. *)
let test_a_completion_is_tried_at_most_three_times_a_session () =
  let%bind { a_f = f; a_name = name; a_intake = intake; a_oms = oms; cleanup; _ } =
    adjust_case ~bought:false ~adjust_now:false
      ~unfilled:[ (D.Order.Side.Buy, 350, `Expired) ]
      ~sizing:Ohcamel.Config.Book.Signals_spec.Live ~overlay:"0.70" ()
  in
  let attempt ~at =
    ignore (D.Intake.judge_pass intake : D.Intake.Accepted.t list);
    let adjusted = D.Intake.adjust intake ~oms:(Some oms) in
    let%bind () = settle () in
    (* The venue's mark alone moves: the graph still reads 100, so the next
       limit is priced where this one was. *)
    Hashtbl.set f.marks ~key:aapl ~data:101.0;
    let%bind () = pump f in
    let%bind () = advance_to f at in
    if not (Deferred.is_determined adjusted) then
      Alcotest.fail "the adjust pass is still waiting";
    return ()
  in
  let%bind () = attempt ~at:200.0 in
  let%bind () = attempt ~at:400.0 in
  let%bind () = attempt ~at:600.0 in
  let%map () = attempt ~at:800.0 in
  Alcotest.(check (list (triple string int string)))
    "the auction's order, then three day limits, each cancelled at its timeout"
    [
      ("buy", 350, "limit day");
      ("buy", 350, "limit day");
      ("buy", 350, "limit day");
      ("buy", 350, "market opg");
    ]
    (strategy_orders f name);
  Alcotest.(check (option (pair (float 1e-12) string)))
    "no completion held: the row is still the open one"
    (Some (0.70, "open"))
    (Option.map (D.Journal.latest_sizing_row f.journal ~strategy:name) ~f:(fun z ->
         (z.D.Journal.Sizing.scale, z.mode)));
  Alcotest.(check int)
    "the fourth pass said so, once" 1
    (List.count (Queue.to_list f.events)
       ~f:(String.is_substring ~substring:"tried 3 times this session"));
  cleanup ()

(* A-I5. A two-wave intraday re-size through the adjust pass. exp_i_wide
   holds nothing and was last sized at 0.3 (ratio 1); overlay 7 says 0.9,
   so with E 1,000,000 and a fraction of 0.01 the targets are, for the six
   weights of 0.16, 0.16 x 0.01 x 0.9 x 1,000,000 = 1,440 -- AAPL 14, MSFT 7,
   IWM 7, QQQ 4, SPY 3, TLT 16 -- and for XOM's 0.04, 360 / 50 = 7: seven
   orders, so two waves, XOM's 350 the smallest and the second. Wave 1 is
   sent and fills. Then wave 2 is refused -- by the gate, ENERGY's cap of
   300 under XOM's 7 x 50.05, or by the switch, set by hand between the
   waves -- and the rebalance ends Waved [Sent 6; Refused]: no sizing row is
   written, the strategy still reads 0.3, and the next pass tries again (the
   band still beyond: 0.9 against 0.3) with XOM alone. Under the gate that
   is refused again; with the switch reset, XOM goes, fills, and the sizing
   row is written at last, at 0.9. *)
let wide_adjust ~refuse_with () =
  let energy_cap =
    {
      Limit.name = "energy-cap";
      scope = Limit.Sector (Sector.of_string "ENERGY");
      kind = Limit.Gross_notional (Notional.of_float 300.0);
    }
  in
  let f =
    fixture
      ~limits:
        (match refuse_with with
        | `Gate -> [ tech_cap; energy_cap ]
        | `Halt -> [ tech_cap ])
      ~extra:
        [
          (iwm, "FUNDS", 200.0);
          (qqq, "FUNDS", 300.0);
          (spy, "FUNDS", 400.0);
          (tlt, "FUNDS", 90.0);
        ]
      ()
  in
  record_close f;
  D.Journal.record_marks f.journal
    (List.map
       [ (iwm, 200.0); (qqq, 300.0); (spy, 400.0); (tlt, 90.0) ]
       ~f:(fun (symbol, close) ->
         { D.Journal.Mark.date = Date.of_string "2026-09-14"; symbol; close; qty = 0.0 }));
  let halt = D.Halt.create D.Halt.Source.none in
  let oms = manager ~halt ~now:(fun () -> tuesday_mid) f in
  let name = "exp_i_wide" in
  let zeros = "sha256:" ^ String.make 64 '0' in
  D.Journal.record_signal f.journal
    {
      D.Journal.Signal.strategy = name;
      sequence = 1;
      as_of = Date.of_string "2026-09-14";
      received_at = evening;
      verdict = D.Journal.Signal.Verdict.Accepted;
      rule = None;
      detail = "passed";
      document =
        sprintf
          {|{"schema_version":1,"strategy":"%s","params_hash":"%s","as_of":"2026-09-14","computed_at":"2026-09-14T23:15:00Z","data_hash":"%s","sequence":1,"validation":{"status":"pass","gates_version":"2026-09-02"},"targets":[{"symbol":"AAPL","weight":0.16},{"symbol":"MSFT","weight":0.16},{"symbol":"IWM","weight":0.16},{"symbol":"QQQ","weight":0.16},{"symbol":"SPY","weight":0.16},{"symbol":"TLT","weight":0.16},{"symbol":"XOM","weight":0.04}]}|}
          name zeros zeros;
      rebalance = Some "sent";
    };
  D.Journal.record_sizing f.journal ~strategy:name ~sequence:1 ~at:evening ~tier:"live"
    ~scale:0.3 ~allocated:3_000.0 ~mode:"open" ~ratio:1.0 ~factor:0.3 ~governor:1.0;
  let dir = Filename_unix.temp_dir "ohcamel-signals-" "" in
  Out_channel.write_all
    (Filename.concat dir "overlay-7.json")
    ~data:
      {|{"schema_version":1,"kind":"overlay","as_of":"2026-09-15T14:55:00Z","computed_at":"2026-09-15T14:55:05Z","sequence":7,"exposure_scale":0.9,"inputs":{},"reported":{}}|};
  let intake =
    D.Intake.create
      ~gauge:
        (D.Intake.Gauge.create ~spec:(D.Oms.spec oms)
           ~venue_now:(fun () -> Some tuesday_mid)
           ~drawdown:(fun () -> None)
           ~dd_cap:None)
      ~journal:f.journal ~dir
      ~strategies:
        [
          live_strategy ~name
            ~symbols:[ "AAPL"; "MSFT"; "XOM"; "IWM"; "QQQ"; "SPY"; "TLT" ]
            ~adjust:Ohcamel.Config.Book.Signals_spec.Adjust.Intraday ();
        ]
      ~universe:[ aapl; msft; xom; iwm; qqq; spy; tlt ]
      ~on_event:(Queue.enqueue f.events)
      ~now:(fun () -> tuesday_mid)
      ()
  in
  let cleanup () =
    Array.iter (Sys_unix.readdir dir) ~f:(fun n ->
        Core_unix.unlink (Filename.concat dir n));
    Core_unix.rmdir dir;
    Graph.destroy f.graph
  in
  (f, oms, halt, intake, name, cleanup)

let test_a_rebalance_held_in_part_records_no_sizing_and_is_retried () =
  let check_case refuse_with =
    let f, oms, halt, intake, name, cleanup = wide_adjust ~refuse_with () in
    ignore (D.Intake.judge_pass intake : D.Intake.Accepted.t list);
    let adjusted = D.Intake.adjust intake ~oms:(Some oms) in
    let%bind () = settle () in
    Alcotest.(check int) "wave 1: six sent" 6 (D.Sim_venue.received f.venue);
    let%bind () = pump f in
    (match refuse_with with
    | `Halt -> D.Halt.halt halt ~why:"halted by hand, between two waves" ~at:tuesday_mid
    | `Gate -> ());
    let%bind () = advance_to f 2.0 in
    if not (Deferred.is_determined adjusted) then
      Alcotest.fail "the adjust is still waiting";
    let sizing () =
      Option.map (D.Journal.latest_sizing_row f.journal ~strategy:name) ~f:(fun z ->
          (z.D.Journal.Sizing.scale, z.mode))
    in
    Alcotest.(check (pair (option (pair (float 1e-12) string)) int))
      "wave 2 refused: no sizing row, still 0.3 open; six received"
      (Some (0.3, "open"), 6)
      (sizing (), D.Sim_venue.received f.venue);
    Alcotest.(check bool)
      "the outcome said: two waves" true
      (List.exists (Queue.to_list f.events)
         ~f:(String.is_substring ~substring:"in 2 waves"));
    (match refuse_with with `Halt -> D.Halt.reset halt | `Gate -> ());
    let again = D.Intake.adjust intake ~oms:(Some oms) in
    let%bind () = settle () in
    let%bind () = pump f in
    let%bind () = advance_to f 4.0 in
    if not (Deferred.is_determined again) then Alcotest.fail "the retry is still waiting";
    (match refuse_with with
    | `Gate ->
        Alcotest.(check (pair bool (option (pair (float 1e-12) string))))
          "retried next pass: XOM alone, refused by the gate again, still unsized"
          (true, Some (0.3, "open"))
          ( List.exists (Queue.to_list f.events)
              ~f:
                (String.is_substring
                   ~substring:"refused, and nothing journaled or sent: the gate refuses"),
            sizing () )
    | `Halt ->
        Alcotest.(check (pair int (option (pair (float 1e-12) string))))
          "retried next pass: XOM sent and filled, sized at last at 0.9"
          (7, Some (0.9, "adjust"))
          (D.Sim_venue.received f.venue, sizing ()));
    cleanup ();
    return ()
  in
  let%bind () = check_case `Gate in
  check_case `Halt

(* I4, as the retry set has it now. Two live strategies, each of whose
   judgements a crash left pending. exp_a01_aapl's crash came before anything
   was journaled: the restarted loop's first pass, the venue clock answering,
   tries it again and sizes it -- AAPL 100. exp_a01_msft's came after its
   order was journaled and sent (MSFT +10 under signal:exp_a01_msft:1): its
   judgement is closed, "not sized again", and nothing more is sent. *)
let test_a_crash_left_pending_is_retried_unless_its_orders_were_journaled () =
  let e =
    evening_case ~start:"2026-09-14T23:15:00Z"
      ~strategies:
        [ live_strategy (); live_strategy ~name:"exp_a01_msft" ~symbols:[ "MSFT" ] () ]
      ()
  in
  let f = e.e_f in
  write_signal e.e_dir ~strategy:"exp_a01_aapl" ~sequence:1 ();
  write_signal e.e_dir ~strategy:"exp_a01_msft" ~sequence:1
    ~targets:{|[{"symbol":"MSFT","weight":1.0}]|} ();
  Alcotest.(check int)
    "the crash: both accepted, neither sized" 2
    (List.length (D.Intake.judge_pass e.e_intake));
  let%bind (_ : D.Oms.Rebalance_outcome.t) =
    D.Oms.propose_rebalance e.e_oms ~mode:D.Rebalance.Mode.Open
      ~source:
        {
          D.Rebalance.Source.strategy = "exp_a01_msft";
          sequence = 1;
          as_of = Date.of_string "2026-09-14";
        }
      ~legs:[ rebalance_leg msft D.Order.Side.Buy 10 ]
  in
  let%map () = start_run e in
  Alcotest.(check (list (triple string string int)))
    "the restart: AAPL sized; MSFT's one order, from before the crash"
    [ ("signal:exp_a01_aapl:1", "AAPL", 100); ("signal:exp_a01_msft:1", "MSFT", 10) ]
    (List.sort ~compare:[%compare: string * string * int] (orders_of f));
  Alcotest.(check (option string))
    "MSFT's judgement closed"
    (Some
       "orders were journaled under this source (signal:exp_a01_msft:1); not sized again")
    (rebalance_of f ~strategy:"exp_a01_msft" ~sequence:1);
  Alcotest.(check int) "two sent in all" 2 (D.Sim_venue.received f.venue);
  e.e_cleanup ()

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
        case "a raise after the halt is answered, and the cancels still go"
          test_a_raise_after_the_halt_is_answered_and_the_cancels_still_go;
        case "a raise inside the kill still sends the cancels"
          test_a_raise_inside_the_kill_still_sends_the_cancels;
        case "the engine's stop cancels, and a reset leaves it stopped"
          test_the_engine's_stop_cancels_and_a_reset_leaves_it_stopped;
        case "under the engine's stop, a refused cancel is sent again"
          test_under_the_engine's_stop_a_refused_cancel_is_sent_again;
        case "a restart sends again a cancel that never arrived"
          test_a_restart_sends_again_a_cancel_that_never_arrived;
        case "a limit's trip cancels the open orders"
          test_a_limit's_trip_cancels_the_open_orders;
        case "a restart reconciles against the venue" test_a_restart_reconciles;
        case "a book that goes stale while the quote is fetched is refused"
          test_a_book_that_goes_stale_while_the_quote_is_fetched_is_refused;
        case "a failed order the venue reports resting is cancelled, once"
          test_a_failed_order_the_venue_reports_resting_is_cancelled;
        case "a kill cancels a failed order the venue still works"
          test_a_kill_cancels_a_failed_order_the_venue_still_works;
        case "the engine's stop cancels a failed order the venue still works"
          test_the_engine's_stop_cancels_a_failed_order_the_venue_still_works;
        case "a stop whose cancel raises says so, and stays stopped"
          test_a_stop_whose_cancel_raises_says_so_and_stays_stopped;
        case "the stream's end stops the desk before the line that says so"
          test_the_stream's_end_stops_the_desk_before_the_line_that_says_so;
        case "a clock read that fails clears the session"
          test_a_clock_read_that_fails_clears_the_session;
        case "the clock is read again when its session ends"
          test_the_clock_is_read_again_when_its_session_ends;
        case "preview and propose agree about a resting order"
          test_preview_and_propose_agree_about_a_resting_order;
        case "a session that closes while the quote is fetched is refused"
          test_a_session_that_closes_while_the_quote_is_fetched_is_refused;
      ] );
    ( "rebalance",
      [
        case "a rebalance journals before the wire, and fills when the session opens"
          test_a_rebalance_journals_before_the_wire_and_fills_when_the_session_opens;
        case "a halt between two submits leaves the second rejected_pre_trade and unsent"
          test_a_halt_between_two_submits_leaves_the_second_unsent;
        case "a rebalance whose window closes while its quotes are fetched is refused"
          test_a_rebalance_whose_window_closes_while_its_quotes_are_fetched_is_refused;
        case "an accepted signal on a live strategy becomes one rebalance"
          test_an_accepted_signal_on_a_live_strategy_becomes_one_rebalance;
        case "a leg the venue does not acknowledge stops the legs after it"
          test_a_leg_the_venue_does_not_acknowledge_stops_the_legs_after_it;
        case "an incubation strategy's failing signal is sized on the paper venue"
          test_an_incubation_strategy's_failing_signal_is_sized_on_the_paper_venue;
        case "incubation refused on a foreign venue"
          test_incubation_refused_on_a_foreign_venue;
        case "a cap of nothing flattens like a scale of nothing"
          test_a_cap_of_nothing_flattens_like_a_scale_of_nothing;
        case "intraday single wave sends Day limits"
          test_intraday_single_wave_sends_day_limits;
        case "open mode unchanged" test_open_mode_unchanged;
        case "wave timeout cancels" test_wave_timeout_cancels;
        case "restart mid-wave does not double-send"
          test_restart_mid_wave_does_not_double_send;
        case "a single intraday wave withheld is cancelled at its timeout"
          test_a_single_intraday_wave_withheld_is_cancelled_at_its_timeout;
        case "a stopped wave's orders are cancelled at the timeout"
          test_a_stopped_wave's_orders_are_cancelled_at_the_timeout;
        case "two waves settle, the second gated after the first fills"
          test_two_waves_settle_and_the_second_is_gated_after_the_first_fills;
        case "an open rebalance sends a leg under the floor"
          test_an_open_rebalance_sends_a_leg_under_the_floor;
        case "overlay zero flattens and alerts" test_overlay_zero_flattens_and_alerts;
        case "adjust within band does nothing" test_adjust_within_band_does_nothing;
        case "adjust beyond band re-sizes" test_adjust_beyond_band_re_sizes;
        case "an unknown drawdown holds the governor and sells nothing"
          test_an_unknown_drawdown_holds_the_governor_and_sells_nothing;
        case "a product of zero moves by the band alone"
          test_a_product_of_zero_moves_by_the_band_alone;
        case "run does not wait for the adjust pass"
          test_run_does_not_wait_for_the_adjust_pass;
        case "a busy adjust is skipped, and said once"
          test_a_busy_adjust_is_skipped_and_said_once;
        case "a document judged before 19:00 is sized at the first pass after"
          test_a_document_judged_before_19_is_sized_at_the_first_pass_after;
        case "a restart sizes nothing until the venue clock answers"
          test_a_restart_sizes_nothing_until_the_venue_clock_answers;
        case "a superseding document cancels the older retry"
          test_a_superseding_document_cancels_the_older_retry;
        case "a final refusal still ends the judgement"
          test_a_final_refusal_still_ends_the_judgement;
        case "a stale overlay holds, and a governor move re-sizes"
          test_a_stale_overlay_holds_and_a_governor_move_re_sizes;
        case "the band compares effective scales at the canary ratio"
          test_the_band_compares_effective_scales_at_the_canary_ratio;
        case "an unsized strategy holding a position is re-sized"
          test_an_unsized_strategy_holding_a_position_is_re_sized;
        case "an unsized strategy holds while the overlay is stale"
          test_an_unsized_strategy_holds_while_the_overlay_is_stale;
        case "an order expired at the auction is completed intraday"
          test_an_order_expired_at_the_auction_is_completed_intraday;
        case "a rebalance the venue refused is completed intraday"
          test_a_rebalance_the_venue_refused_is_completed_intraday;
        case "a completion is tried at most three times a session"
          test_a_completion_is_tried_at_most_three_times_a_session;
        case "a rebalance held in part records no sizing, and is retried"
          test_a_rebalance_held_in_part_records_no_sizing_and_is_retried;
        case "a crash left pending is retried, unless its orders were journaled"
          test_a_crash_left_pending_is_retried_unless_its_orders_were_journaled;
      ] );
    ( "desk",
      [
        case "a raising restore idles fill_read, and a later read still runs"
          test_a_raising_restore_idles_fill_read_and_a_later_read_still_runs;
        case "a failed clock read keeps the reading it had, which ages to stale"
          test_a_failed_clock_read_keeps_the_reading_it_had_which_ages_to_stale;
        case "a second clock refresh while one is out starts no second read"
          test_a_second_clock_refresh_while_one_is_out_starts_no_second_read;
        case "a disabled venue takes no clock read and stays unknown"
          test_a_disabled_venue_takes_no_clock_read_and_stays_unknown;
      ] );
    ( "intake",
      [
        case "a raising pass is logged, and the next still runs"
          test_a_raising_pass_is_logged_and_the_next_still_runs;
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

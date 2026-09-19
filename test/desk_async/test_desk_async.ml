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
   one a case has wrapped, and [read] its read half; without them the manager
   uses the venue directly. [now] is the wall clock unless a case gives the
   manager its own, and the book is the account's unless a case says when it
   stops being so. [on_change] is the page's nudge, a no-op unless a case
   makes it raise; [on_event] runs after each line is kept in [f.events], for
   a case whose log must raise. Its lookups keep production's schedule -- 2, 10 and 30 s --
   on the fixture's clock, so they fire only when a case advances it. *)
let manager ?trade ?read ?(now = Time_ns.now) ?(book_is_current = fun () -> true)
    ?(halt = D.Halt.create D.Halt.Source.none) ?(on_change = ignore) ?(on_event = ignore)
    f =
  let trade =
    match trade with Some t -> t | None -> D.Sim_venue.trade ~auto:false f.venue
  in
  let read = match read with Some r -> r | None -> D.Sim_venue.read f.venue in
  incr managers_built;
  let oms =
    D.Oms.create ~graph:f.graph ~journal:f.journal
      ~spec:{ Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled }
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
let forwarded (sim : D.Venue.Trade.t) =
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
   and the venue still has the order resting: no DELETE ever reached it. The
   restart's reconciliation sends it again by the venue's id. The first
   answer is not a confirmation, so the next reconciliation that finds it
   resting sends it again; that one the venue takes, though its reports
   still say new until it acts, so a third reconciliation sends nothing --
   one DELETE per venue id. When the venue acts, the restart's stream
   reports the order cancelled. *)
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
  (* The restart, with the key fixed. *)
  let venue = D.Sim_venue.trade ~auto:false f.venue in
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
        }
      f
  in
  let%bind () = D.Oms.reconcile restarted in
  let%bind () = settle () in
  Alcotest.(check (list string)) "the restart sends it again" [ "sim-1" ] !deletes;
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
  Alcotest.(check string) "and then cancelled" "cancelled" (state f resting);
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
        (fun r ->
          don't_wait_for (Deferred.ignore_m (sim.D.Venue.Trade.submit r));
          Deferred.never ());
    }
  in
  let never_sent =
    { answer_lost with D.Venue.Trade.submit = (fun _ -> Deferred.never ()) }
  in
  let quiet (trade : D.Venue.Trade.t) =
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
        (fun r ->
          incr submitted;
          sim.D.Venue.Trade.submit r);
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
        (fun r ->
          let%map (_ : D.Venue.Submission.t) = sim.D.Venue.Trade.submit r in
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
     made it before the cancel is kept for the late copy below. *)
  let before_the_cancel = at_venue x in
  let%bind () = D.Oms.kill oms ~why:"testing the switch" in
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
          };
        ]
      ~universe:[ Symbol.of_string "SPY" ]
      ~on_event:(Queue.enqueue events)
      ~now:(fun () -> Time_source.now clock)
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
        case "the clock is read again when its session ends"
          test_the_clock_is_read_again_when_its_session_ends;
        case "preview and propose agree about a resting order"
          test_preview_and_propose_agree_about_a_resting_order;
      ] );
    ( "desk",
      [
        case "a raising restore idles fill_read, and a later read still runs"
          test_a_raising_restore_idles_fill_read_and_a_later_read_still_runs;
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

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
   updates wait behind it, which is the order they happened in. What the
   sequencer does NOT promise is an order between orders: the simulated venue
   walks a map keyed by id, so once ids reach two digits the updates for two
   different orders can arrive in neither the order they were submitted nor
   the order they filled. Nothing here reads one order's state to decide
   another's, so that is a fact about the venue and not a bug here.

   JOURNAL BEFORE WIRE (invariant 10). An order is written as pending_submit
   before the request that submits it is sent. A journal write that fails
   raises, which is how the send is stopped: no request leaves for an order
   the record does not hold. An unknown answer is resolved by the client order
   id -- the venue's own update, or lookups two, ten and thirty seconds apart
   (42 s in all), after which an order nobody found has failed; a lookup that
   errors is tried again every minute -- and never by sending the order again.
   ONE miss is not the answer: a request can still be arriving, so a lookup
   that finds nothing while a delay remains only schedules the next one.

   FILLS ARE FACTS (invariant 11). A fill is journaled whatever state its
   order is in. The graph's position is then set from the venue's
   position_qty when the venue gives one, and the account is re-read
   ([after_fill]), because the venue's ledger is the one that is true.
   Positions follow the fills and the read corrects them: bin/main.ml's
   [after_fill] is Desk.after_fill, which refuses any account read already
   out when the fill landed, so a read that did not see the fill never undoes
   it (A1's final review, M4). That is the whole of the answer to a REST read
   lagging the stream: this module starts no read of its own.

   ITS OWN CLOCK, WHEN A TEST GIVES ONE. Every wait here -- the lookups and
   the arrival quote's bound -- is on [time_source], the wall clock unless the
   caller passes another, so the scheduler suite crosses 42 s of lookups
   without waiting on the wall's. *)

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

let create ~graph ~journal ~spec ~read ~trade ~halt ~accepts_tickets ~adv ~now ~rng
    ~on_change ~on_event ~after_fill ~book_is_current
    ?(resolve_delays =
      [ Time_ns.Span.of_sec 2.0; Time_ns.Span.of_sec 10.0; Time_ns.Span.of_sec 30.0 ])
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
  && Result.is_ok t.trade && t.book_is_current ()

let set_market t ~session_open ~adv20 =
  t.session_open <- session_open;
  t.adv20 <- Symbol.Map.of_alist_reduce adv20 ~f:(fun _ latest -> latest)

let key (o : Order.t) =
  Ids.Client_order_id.to_string o.Order.request.Order.Request.client_order_id

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
      (if held && Float.( > ) (Price.to_float (Graph.price t.graph symbol)) 0.0 then
         Some (Graph.price t.graph symbol)
       else None);
    stale =
      held
      && (listed health.Graph.Feed_health.stale
         || listed health.Graph.Feed_health.never_seen);
    adv20 =
      (match t.adv with
      | Adv.Fixed n -> Some n
      | Adv.From_venue -> Map.find t.adv20 symbol);
    recent =
      List.filter t.recent ~f:(fun r ->
          Float.( < )
            (Time_ns.Span.to_sec (Time_ns.diff now r.Rules.Recent.at))
            t.spec.Desk_spec.duplicate_window_s);
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

  let passed t =
    List.is_empty t.failures
    && match t.verdict with Some v -> v.Gate.Verdict.passed | None -> false

  let reasons t =
    List.map t.failures ~f:(fun f ->
        sprintf "%s: %s" f.Rules.Failure.rule f.Rules.Failure.why)
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
          Option.value_map (Order.Kind.limit_price r.Order.Request.kind) ~default:`Null
            ~f:(fun p -> jnum (Price.to_float p)) );
        ( "decision_price",
          Option.value_map t.decision_price ~default:`Null ~f:(fun p ->
              jnum (Price.to_float p)) );
        ( "rules",
          `List
            (List.map t.failures ~f:(fun f ->
                 `Assoc
                   [
                     ("rule", `String f.Rules.Failure.rule);
                     ("why", `String f.Rules.Failure.why);
                   ])) );
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
  let request =
    Ticket.to_request ticket
      ~client_order_id:(Ids.Client_order_id.generate ~now ~rng:t.rng)
  in
  let ctx = context t ~symbol:ticket.Ticket.symbol in
  let decision_price =
    match ticket.Ticket.kind with
    | Order.Kind.Limit p -> Some p
    | Order.Kind.Market -> ctx.Rules.Context.mark
  in
  let verdict =
    match decision_price with
    | Some price
      when Set.mem ctx.Rules.Context.universe ticket.Ticket.symbol
           && ticket.Ticket.qty > 0 ->
        let qty =
          Qty.of_float
            (Order.Side.sign ticket.Ticket.side *. Float.of_int ticket.Ticket.qty)
        in
        Some
          (Gate.check t.graph
             ~fills:[ { Gate.Fill.symbol = ticket.Ticket.symbol; qty; price } ])
    | _ -> None
  in
  { Preview.request; failures = Rules.check ctx request; verdict; decision_price }

(* One event, all the way down: the machine; the journal, the fill first when
   there is one; the open set; the page. Task 15 adds the switch's clause. *)
let record t (o : Order.t) (event : Order.Event.t) : Order.t =
  let o', anomaly = Order.apply o event in
  (match event with
  | Order.Event.Venue_fill f -> ignore (Journal.record_fill t.journal o' f : bool)
  | _ -> ());
  Journal.update_order t.journal o' ~event ~anomaly ~at:(t.now ());
  Option.iter anomaly ~f:(fun a ->
      t.on_event (sprintf "desk      %s: %s" (key o') (Order.Anomaly.to_string a)));
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

(* Invariant 10's other half: the only question an unknown outcome licenses is
   "do you have this client order id". A miss while a delay remains schedules
   the next lookup and nothing else -- never a second request for the same
   order, because the first may be arriving at the venue as this one is
   asked. *)
let rec resolve t (client : Ids.Client_order_id.t) ~(delays : Time_ns.Span.t list) :
    unit Deferred.t =
  let delay, rest =
    match delays with d :: rest -> (d, rest) | [] -> (Time_ns.Span.of_min 1.0, [])
  in
  let%bind () = Time_source.after t.time_source delay in
  let%bind again =
    enqueue t (fun () ->
        match (Map.find t.open_ (Ids.Client_order_id.to_string client), t.trade) with
        | Some o, Ok trade when Order.State.equal o.Order.state Order.State.Submit_unknown
          -> (
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
                  (sprintf "desk      %s is still unknown; the lookup failed: %s"
                     (Ids.Client_order_id.to_string client)
                     (Error.to_string_hum e));
                true)
        | _ -> return false)
  in
  if again then resolve t client ~delays:rest else Deferred.unit

let propose t ?(source = "manual") (ticket : Ticket.t) : (Preview.t * Order.t) Deferred.t
    =
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
              match%map
                Time_source.with_timeout t.time_source (Time_ns.Span.of_sec 2.0)
                  (read.Venue.Read.latest_quote symbol)
              with
              | `Result (Ok (Some q)) -> Some (q.Venue.Quote.bid, q.Venue.Quote.ask)
              | `Result (Ok None) | `Result (Error _) | `Timeout -> None)
        in
        (* The switch may have tripped while the quote was fetched, and the
           book's last applied read may have aged past the window. *)
        match (Halt.reason t.halt, t.trade) with
        | Some why, _ ->
            let p =
              {
                p with
                Preview.failures =
                  p.Preview.failures @ [ { Rules.Failure.rule = "kill_switch"; why } ];
              }
            in
            return (p, refuse t p ~source)
        | None, Error why ->
            let p =
              { p with Preview.failures = [ { Rules.Failure.rule = "trading"; why } ] }
            in
            return (p, refuse t p ~source)
        | None, Ok _ when not (t.book_is_current ()) ->
            let p =
              {
                p with
                Preview.failures =
                  [ { Rules.Failure.rule = "trading"; why = Session_close.not_current } ];
              }
            in
            return (p, refuse t p ~source)
        | None, Ok trade ->
            let o = Order.create request in
            (* The journal, and only then the wire. This write raises if the
               record cannot be made, which stops the send below. *)
            Journal.insert_order t.journal o ~source
              ~decision_price:(Option.value_exn p.Preview.decision_price)
              ~arrival ~verdict:(Preview.to_json p) ~at:(t.now ());
            t.open_ <- Map.set t.open_ ~key:(key o) ~data:o;
            t.recent <-
              {
                Rules.Recent.symbol;
                side = request.Order.Request.side;
                qty = request.Order.Request.qty;
                at = t.now ();
              }
              :: (context t ~symbol).Rules.Context.recent;
            t.on_change ();
            let%map submission = trade.Venue.Trade.submit request in
            let o =
              match submission with
              | Venue.Submission.Accepted v ->
                  record t o (Order.Event.Acknowledged v.Venue.Venue_order.id)
              | Venue.Submission.Rejected why ->
                  record t o (Order.Event.Venue_rejected_submission why)
              | Venue.Submission.Unknown why ->
                  let o = record t o (Order.Event.Outcome_unknown why) in
                  don't_wait_for
                    (resolve t request.Order.Request.client_order_id
                       ~delays:t.resolve_delays);
                  o
            in
            (p, o))

let apply_to_graph t (o : Order.t) (f : Order.Fill.t) =
  let symbol = o.Order.request.Order.Request.symbol in
  if List.mem (Graph.symbols t.graph) symbol ~equal:Symbol.equal then (
    Graph.apply_fill t.graph
      {
        Fill.symbol;
        qty =
          Qty.of_float
            (Order.Side.sign o.Order.request.Order.Request.side *. f.Order.Fill.qty);
        price = f.Order.Fill.price;
        time = f.Order.Fill.at;
      };
    Option.iter f.Order.Fill.position_qty ~f:(fun q ->
        Graph.set_qty t.graph symbol (Qty.of_float q));
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
                Option.map (Journal.load_order t.journal c) ~f:(fun r ->
                    r.Journal.Order_row.order))
      in
      (match known with
      | None ->
          t.on_event
            (sprintf "desk      %s for an order this desk has no record of (%s %s)"
               u.Venue.Update.event raw
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
                 "desk      the venue reports %s, which this desk declared failed when \
                  its lookups found nothing; its fills are journaled and the book \
                  follows the venue, but the switch cannot cancel it"
                 raw);
          let o =
            match u.Venue.Update.fill with
            | Some f when Reconcile.fill_is_new o u ->
                let o = record t o (Order.Event.Venue_fill f) in
                apply_to_graph t o f;
                o
            | Some f ->
                t.on_event
                  (sprintf "desk      %s: execution %s is already counted" raw
                     f.Order.Fill.execution_id);
                o
            | None -> o
          in
          match (u.Venue.Update.event, Venue.Update.to_event u) with
          | ("fill" | "partial_fill"), _ -> ()
          | _, Some e -> ignore (record t o e : Order.t)
          | _, None ->
              t.on_event
                (sprintf "desk      %s: %s changes nothing the order's states describe"
                   raw u.Venue.Update.event)));
      Deferred.unit)

(* THE STREAM CAN STOP, and a desk that only hears about fills on it must not
   go on believing its orders are being watched. The trade_updates socket ends
   its pipe when the venue closes it -- an Unauthorized reply ends it at once
   -- and nothing here reconnects. So the end of the pipe is said aloud rather
   than returned in silence: the orders still open at that moment will be
   settled by the next restart's reconciliation, not by this process. *)
let run t : unit Deferred.t =
  match t.trade with
  | Error _ -> Deferred.unit
  | Ok trade ->
      let%map () = Pipe.iter trade.Venue.Trade.updates ~f:(on_update t) in
      t.on_event
        (sprintf
           "desk      the venue's stream of order updates has ended; %d order(s) are \
            open and nothing on this connection will report them again"
           (Map.length t.open_))

let jnum x = if Float.is_finite x then `Float x else `Null
let jstr_opt = Option.value_map ~default:`Null ~f:(fun s -> `String s)

let order_json (r : Journal.Order_row.t) : Yojson.Safe.t =
  let o = r.Journal.Order_row.order in
  let q = o.Order.request in
  `Assoc
    [
      ( "client_order_id",
        `String (Ids.Client_order_id.to_string q.Order.Request.client_order_id) );
      ("venue_order_id", jstr_opt o.Order.venue_order_id);
      ("source", `String r.Journal.Order_row.source);
      ("symbol", `String (Symbol.to_string q.Order.Request.symbol));
      ("side", `String (Order.Side.to_string q.Order.Request.side));
      ("qty", `Int q.Order.Request.qty);
      ("type", `String (Order.Kind.to_string q.Order.Request.kind));
      ( "limit_price",
        Option.value_map (Order.Kind.limit_price q.Order.Request.kind) ~default:`Null
          ~f:(fun p -> jnum (Price.to_float p)) );
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
   the mid to the far side. Tca.of_fill never reads the book, so this is the
   one place the two meet, and the number it is handed must mean what
   [versus_model_bps] subtracts it from -- a slippage against the arrival
   mid, which is half a spread wide and not a whole one. *)
let model_half_spread_bps t symbol =
  Option.value
    (List.Assoc.find t.spec.Desk_spec.spread_bps (Symbol.to_string symbol)
       ~equal:String.equal)
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
      ( inputs,
        Tca.of_fill
          ~model_half_spread_bps:(model_half_spread_bps t r.Journal.Fill_row.symbol)
          inputs ))

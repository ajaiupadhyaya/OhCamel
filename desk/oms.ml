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
   A failed order the venue later reports it still works is cancelled by the
   venue's id ([cancel_failed]), and so is a pending_cancel order a
   reconciliation finds still resting ([resend_cancel]): a cancel is not a
   resend.
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

   ITS OWN CLOCK, WHEN A TEST GIVES ONE. Every wait here -- the lookups, a
   cancel's retries under the switch, the arrival quote's bound and the refresh
   loop -- is on [time_source], the wall clock unless the caller passes
   another, so the scheduler suite crosses 42 s of lookups without waiting on
   the wall's. *)

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
  (* When the last daily bars that answered were asked for, on [time_source]:
     [refresh_forever] asks again once an hour has passed since, whatever its
     own interval and however long each refresh's requests take. *)
  mutable bars_fetched_at : Time_ns.t option;
  (* The venue's clock as last read, which the session rule reads at each
     order's own time (Rules.Session); None until a clock answers, and after
     a read that failed. *)
  mutable session : Rules.Session.t option;
  (* The [resolve] and [retry_cancel] loops running now, each as its loop's
     name and the client order id ([one_loop]). *)
  live_loops : String.Hash_set.t;
  (* The venue ids of failed orders a DELETE has been sent for, and not
     refused ([cancel_failed]). *)
  failed_cancels : String.Hash_set.t;
  (* The venue ids of pending_cancel orders a reconciliation has sent the
     DELETE again for, and not refused ([resend_cancel]). *)
  resent_cancels : String.Hash_set.t;
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
    bars_fetched_at = None;
    session = None;
    live_loops = String.Hash_set.create ();
    failed_cancels = String.Hash_set.create ();
    resent_cancels = String.Hash_set.create ();
  }

let halt t = t.halt
let journal t = t.journal
let accepts_tickets t = t.accepts_tickets
let open_count t = Map.length t.open_

(* The one line every error in this module already prints through, so a
   caller outside it -- the routes, catching an exception that must not
   reach the wire in its own words -- can still say what happened, on the
   process's own log rather than in the response body. *)
let on_event t = t.on_event

(* Whether an order could go now: the book enables trading, there is a
   trading half, and the graph holds the account's book. The frame's
   [trading] is this, so the page cannot read "on" while the trading rule
   would refuse every order for a book that is not the account's. *)
let can_trade t =
  Desk_spec.equal_trading t.spec.Desk_spec.trading Desk_spec.Enabled
  && Result.is_ok t.trade && t.book_is_current ()

(* The clock's two times, kept; [refresh] calls this with each answer, and
   with None when the read fails. *)
let set_clock t (clock : Venue.Session_clock.t option) =
  t.session <-
    Option.map clock ~f:(fun c ->
        {
          Rules.Session.next_open = c.Venue.Session_clock.next_open;
          next_close = c.Venue.Session_clock.next_close;
        })

(* A market set by hand, for a caller with no clock to read: a session open
   with no close in sight -- both times at the end of representable time -- or
   none; and the twenty-day volumes. *)
let set_market t ~session_open ~adv20 =
  t.session <-
    (if session_open then
       Some
         {
           Rules.Session.next_open = Time_ns.max_value_representable;
           next_close = Time_ns.max_value_representable;
         }
     else None);
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
    session = t.session;
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

(* At most one loop of each kind per order, however many callers start one.
   [reconcile] runs at every live start and again each time the update stream
   connects, and each run would otherwise start another [resolve] for an order
   still unknown -- and a lookup that errors for good is asked once a minute
   without end, by every one of them, from the 200 requests a minute the desk
   has. A kill by hand and then a trip each cancel every open order, and would
   otherwise start two [retry_cancel] loops for one DELETE. A start while a
   loop of the same kind runs for the order is skipped.

   The key is the loop's name with the client order id, not the id alone: a
   [resolve] still waiting out its delay on an order the stream has since
   named must not keep the switch's retries from starting for it.

   A loop that ends calls [stop] inside the sequencer job that decided so, so
   the next job -- the only place a loop is started -- can start another at
   once. A loop that raises never reaches it, and [finally] takes the key out
   instead; [stop] runs once per loop, so a late [finally] cannot take out the
   key of a loop started after it. *)
let one_loop t ~(loop : string) (client : Ids.Client_order_id.t)
    (body : stop:(unit -> unit) -> unit Deferred.t) : unit Deferred.t =
  let k = loop ^ " " ^ Ids.Client_order_id.to_string client in
  if Hash_set.mem t.live_loops k then Deferred.unit
  else (
    Hash_set.add t.live_loops k;
    let stopped = ref false in
    let stop () =
      if not !stopped then (
        stopped := true;
        Hash_set.remove t.live_loops k)
    in
    Monitor.protect
      (fun () -> body ~stop)
      ~finally:(fun () ->
        stop ();
        Deferred.unit))

(* One event, all the way down: the machine; the journal, the fill first when
   there is one; the open set; the page. And the switch: an order the venue
   first gives an id while the switch is not clear -- a kill that landed while
   its request was in flight, an unknown found after a trip -- is cancelled at
   once, by that id. *)
let rec record t (o : Order.t) (event : Order.Event.t) : Order.t =
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
  (* Only the event that first gives the order a venue id: every later
     transition of the same order would queue another cancel for an order
     already pending_cancel, which the venue refuses and the log would print
     in the middle of a kill. *)
  if
    Option.is_some (Halt.reason t.halt)
    && Option.is_none o.Order.venue_order_id
    && Option.is_some o'.Order.venue_order_id
    && not (Order.State.is_terminal o'.Order.state)
  then
    don't_wait_for
      (Deferred.ignore_m (cancel t o'.Order.request.Order.Request.client_order_id));
  o'

(* A cancel is never a resend: the only request it can make is
   [Venue.Trade.cancel], by the venue's own id, and an order the venue has not
   named yet has no cancel to send -- [record] above sends it the moment the id
   arrives. An error is not a refusal, and nothing here can tell it from one:
   desk/alpaca_trade.ml answers a timeout, a 5xx and a 422 with the same error,
   and the first two may or may not have reached the venue. So the order stays
   pending_cancel until the venue reports, and while the switch is not clear
   the DELETE is sent again on a bounded schedule ([retry_cancel]). *)
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
                   (sprintf
                      "the order is %s, and the venue has not given it an id to cancel \
                       by yet"
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
                     the cancel, or a fill if it could not be cancelled -- says
                     what became of it. Under the switch that update cannot be
                     waited for, because a DELETE that never arrived brings
                     none. *)
                  let halted = Option.is_some (Halt.reason t.halt) in
                  t.on_event
                    (sprintf
                       "desk      the venue's answer to cancelling %s was not a \
                        confirmation (%s); it stays pending_cancel until the venue \
                        reports%s"
                       (key o) (Error.to_string_hum e)
                       (if halted then
                          ", and the desk is halted, so the cancel will be sent again"
                        else ""));
                  if halted then
                    don't_wait_for (retry_cancel t client ~id ~delays:t.resolve_delays);
                  Error
                    (sprintf
                       "the venue's answer was not a confirmation (%s); the order stays \
                        pending_cancel until the venue reports"
                       (Error.to_string_hum e)))))

(* A DELETE whose answer was not a confirmation, sent again while the switch
   is not clear. desk/alpaca_trade.ml answers a timeout, a 5xx and a 422 with
   the same error, and a timeout may never have reached the venue: under a
   halt the only safe reading is that the order may still be resting there.
   A retry resends the DELETE for the same venue id and nothing else, and only
   while the order is still open under that id, pending_cancel or partially
   filled, and the switch still not clear -- the venue's update that settles
   the order, or a reset, ends the retries. The delays are the lookups' (2, 10
   and 30 s by default), so they are bounded: after the last the order is left
   pending_cancel.

   A partial fill does not end them. It moves a pending_cancel order to
   partially_filled, and the rest of it is still resting at the venue if the
   DELETE never arrived; so the retry asks for the cancel again, journaled as
   one, before it resends. Every end under the switch but the order leaving
   the open set is said aloud -- a confirmation, the last retry, an order this
   retry does not cancel -- because until the venue reports, the order may
   still be working there. *)
and retry_cancel t (client : Ids.Client_order_id.t) ~(id : string)
    ~(delays : Time_ns.Span.t list) : unit Deferred.t =
  let name = Ids.Client_order_id.to_string client in
  let ended why =
    t.on_event (sprintf "desk      stopped sending the cancel of %s again: %s" name why)
  in
  let exhausted =
    "no retry is left; it stays pending_cancel and may still be resting at the venue \
     until the venue reports"
  in
  one_loop t ~loop:"retry_cancel" client (fun ~stop ->
      let rec go = function
        | [] ->
            ended exhausted;
            stop ();
            Deferred.unit
        | delay :: rest ->
            let%bind () = Time_source.after t.time_source delay in
            let%bind again =
              enqueue t (fun () ->
                  match (Map.find t.open_ name, t.trade, Halt.reason t.halt) with
                  | None, _, _ | _, _, None ->
                      stop ();
                      return false
                  | Some o, Ok trade, Some _
                    when Option.equal String.equal o.Order.venue_order_id (Some id)
                         && (Order.State.equal o.Order.state Order.State.Pending_cancel
                            || Order.State.equal o.Order.state
                                 Order.State.Partially_filled) -> (
                      if Order.State.equal o.Order.state Order.State.Partially_filled then
                        ignore (record t o Order.Event.Cancel_requested : Order.t);
                      match%map trade.Venue.Trade.cancel id with
                      | Ok () ->
                          ended
                            "the venue confirmed it, and the order stays pending_cancel \
                             until the venue reports";
                          stop ();
                          false
                      | Error e ->
                          t.on_event
                            (sprintf
                               "desk      the venue's answer to cancelling %s again was \
                                not a confirmation either: %s"
                               name (Error.to_string_hum e));
                          if List.is_empty rest then (
                            ended exhausted;
                            stop ();
                            false)
                          else true)
                  | Some o, _, Some _ ->
                      ended
                        (sprintf
                           "it is %s under venue id %s, which is not an order this retry \
                            cancels"
                           (Order.State.to_string o.Order.state)
                           (Option.value o.Order.venue_order_id ~default:"(none)"));
                      stop ();
                      return false)
            in
            if again then go rest else Deferred.unit
      in
      go delays)

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
let resolve t (client : Ids.Client_order_id.t) ~(delays : Time_ns.Span.t list) :
    unit Deferred.t =
  one_loop t ~loop:"resolve" client (fun ~stop ->
      let rec go delays =
        let delay, rest =
          match delays with d :: rest -> (d, rest) | [] -> (Time_ns.Span.of_min 1.0, [])
        in
        let%bind () = Time_source.after t.time_source delay in
        let%bind again =
          enqueue t (fun () ->
              match
                (Map.find t.open_ (Ids.Client_order_id.to_string client), t.trade)
              with
              | Some o, Ok trade
                when Order.State.equal o.Order.state Order.State.Submit_unknown -> (
                  match%map trade.Venue.Trade.find_order client with
                  | Ok (Some v) ->
                      ignore
                        (record t o (Order.Event.Found v.Venue.Venue_order.id) : Order.t);
                      stop ();
                      false
                  | Ok None when List.is_empty rest ->
                      ignore (record t o Order.Event.Not_found : Order.t);
                      stop ();
                      false
                  | Ok None -> true
                  | Error e ->
                      t.on_event
                        (sprintf "desk      %s is still unknown; the lookup failed: %s"
                           (Ids.Client_order_id.to_string client)
                           (Error.to_string_hum e));
                      true)
              | _ ->
                  stop ();
                  return false)
        in
        if again then go rest else Deferred.unit
      in
      go delays)

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

(* An order this desk declared failed -- every lookup missed -- that the venue
   reports it still works. The desk has given up on it: it is out of the open
   set, so no kill or trip reaches it, and nothing else would ever cancel it.
   It would rest until the venue's day ends and could fill with nobody having
   decided it should. So it is cancelled at once, by the venue's own id, in
   any switch state, and the log says so. A cancel is not a resend (invariant
   10): the only request is a DELETE of the order the venue named.

   One DELETE per venue id. The stream and a reconciliation can each report
   the same order, and a report written before the venue took the DELETE
   still says it rests. A DELETE whose answer was not a confirmation is sent
   again the next time the venue reports the order resting, and not before. *)
let cancel_failed t (trade : Venue.Trade.t) ~(client : string) (v : Venue.Venue_order.t) :
    unit Deferred.t =
  let id = v.Venue.Venue_order.id in
  if Hash_set.mem t.failed_cancels id then Deferred.unit
  else (
    Hash_set.add t.failed_cancels id;
    t.on_event
      (sprintf
         "desk      the venue reports %s %s under id %s, which this desk declared failed \
          when its lookups found nothing; nothing manages it, so the desk is cancelling \
          it by that id"
         client v.Venue.Venue_order.status id);
    match%map trade.Venue.Trade.cancel id with
    | Ok () -> ()
    | Error e ->
        Hash_set.remove t.failed_cancels id;
        t.on_event
          (sprintf
             "desk      the venue's answer to cancelling %s (%s), which this desk \
              declared failed, was not a confirmation (%s); it may still be resting \
              there, and is cancelled again the next time the venue reports it resting"
             client id (Error.to_string_hum e)))

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
      match known with
      | None ->
          t.on_event
            (sprintf "desk      %s for an order this desk has no record of (%s %s)"
               u.Venue.Update.event raw
               (Symbol.to_string u.Venue.Update.order.Venue.Venue_order.symbol));
          Deferred.unit
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
             journaled as fills after failed and the book follows the venue. An
             order the venue still works is cancelled below; one it has
             finished with is said, loudly. *)
          let failed = Order.State.equal o.Order.state Order.State.Failed in
          let resting = Venue.Venue_order.rests u.Venue.Update.order in
          if failed && not resting then
            t.on_event
              (sprintf
                 "desk      the venue reports %s %s, which this desk declared failed \
                  when its lookups found nothing; its fills are journaled and the book \
                  follows the venue"
                 raw u.Venue.Update.order.Venue.Venue_order.status);
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
          (match (u.Venue.Update.event, Venue.Update.to_event u) with
          | ("fill" | "partial_fill"), _ -> ()
          | _, Some e -> ignore (record t o e : Order.t)
          | _, None ->
              t.on_event
                (sprintf "desk      %s: %s changes nothing the order's states describe"
                   raw u.Venue.Update.event));
          match t.trade with
          | Ok trade when failed && resting ->
              cancel_failed t trade ~client:raw u.Venue.Update.order
          | Ok _ | Error _ -> Deferred.unit))

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

let changed t = t.on_change ()

(* Every open order the venue has an id for. One without an id yet is
   cancelled by [record] the moment the venue gives it one. The set is
   [t.open_], which is the journal's open orders held -- [reconcile] re-seeds
   it from [Journal.open_orders] -- and not the venue's [open_orders], which is
   a different set: the desk's own orders as the VENUE has them, which cannot
   include one the desk journaled and never managed to send. *)
let cancel_all t : unit Deferred.t =
  Deferred.List.iter ~how:`Sequential (Map.data t.open_) ~f:(fun o ->
      match o.Order.venue_order_id with
      | None -> Deferred.unit
      | Some _ ->
          Deferred.ignore_m (cancel t o.Order.request.Order.Request.client_order_id))

(* A kill's first half, synchronous: the halt, its line in the log, and the
   page told. The halt comes first, so anything that raises after it raises
   with the switch already set -- which is what lets the kill route answer
   the person who pressed it, and still send the cancels. *)
let halt_by_hand t ~why =
  Halt.halt t.halt ~why ~at:(t.now ());
  t.on_event ("desk      HALTED by hand: " ^ why);
  t.on_change ()

(* The halt, then the cancels -- the cancels whether or not anything after the
   halt raised, as the kill route sends them, because the halt is set and
   orders left open under it could still fill. A raise still reaches the
   caller, once the cancels have started. *)
let kill t ~why : unit Deferred.t =
  let raised = Result.try_with (fun () -> halt_by_hand t ~why) in
  let cancels = cancel_all t in
  Result.ok_exn raised;
  cancels

(* The engine's stop (§3.8): the venue's order updates have ended for good,
   so no fill would be heard. The switch first, so nothing new goes out; then
   every open order is asked to be cancelled, as a trip and a kill ask. The
   cancels go on behind, one at a time, under a monitor of their own, so a
   raise among them reaches the log and not the process -- and every answer
   the venue gives them after the DELETE would travel on the stream that
   ended, so none can be confirmed here: the orders stay pending_cancel until
   a restart reconciles, and [reconcile] sends again any DELETE that never
   arrived. Only then the line, and the page told. *)
let stop t ~why =
  Halt.stop t.halt ~why ~at:(t.now ());
  don't_wait_for
    (match%map Monitor.try_with ~extract_exn:true ~rest:`Log (fun () -> cancel_all t) with
    | Ok () ->
        t.on_event
          "desk      the engine's stop has asked the venue to cancel every open order it \
           has an id for"
    | Error exn ->
        t.on_event
          (sprintf "desk      the engine's stop's cancels raised: %s" (Exn.to_string exn)));
  t.on_event ("desk      STOPPED by the engine: " ^ why);
  t.on_change ()

(* THE STREAM CAN STOP, and a desk that only hears about fills on it must not
   go on believing its orders are being watched. The trade_updates socket ends
   its pipe when the venue closes it -- an Unauthorized reply ends it at once
   -- and nothing here reconnects. So the end of the pipe is said aloud rather
   than returned in silence, and the engine stops the desk ([stop]): no order
   may go out whose fill nothing would apply. That is the switch's own state,
   not a halt by hand -- a reset cannot lift it, because the stream has not
   come back -- and only a restart, which reconnects and reconciles, clears
   it. A manager with no trading half has no stream to lose, and returns at
   once without stopping anything. *)
let run t : unit Deferred.t =
  match t.trade with
  | Error _ -> Deferred.unit
  | Ok trade ->
      let%map () = Pipe.iter trade.Venue.Trade.updates ~f:(on_update t) in
      t.on_event
        (sprintf
           "desk      the venue's stream of order updates has ended; %d order(s) are \
            open and nothing on this connection will report them again"
           (Map.length t.open_));
      stop t
        ~why:
          "the venue's order updates stopped, so no fill would be heard; restart the \
           engine to reconnect and reconcile"

(* The other half of §3.8. The switch already refuses new orders through
   [Halt.reason]; a trip must also cancel the open ones. The handler runs
   inside stabilization, so it only schedules. *)
let watch_alerts t (alerts : Ohcamel.Alerts.t) =
  Ohcamel.Alerts.on_trip alerts ~f:(fun event ->
      upon Deferred.unit (fun () ->
          t.on_event
            (sprintf
               "desk      the kill switch tripped on %s: cancelling every open order"
               event.Ohcamel.Alerts.Event.limit_name);
          t.on_change ();
          don't_wait_for (cancel_all t)))

(* An order this desk asked the venue to cancel -- journaled pending_cancel --
   that a reconciliation finds still resting. The DELETE may never have
   arrived: [cancel] journals the request before it sends it, and the engine's
   stop sends its cancels when the stream has ended, which it does only when
   the paper host refuses the key -- so the same key's DELETEs were likely
   refused too, and the retries died with the process. Nothing else would send
   it again: a reconciliation's [Venue_accepted] changes nothing in
   pending_cancel. So it is sent again, by the venue's own id, in any switch
   state, because the desk already decided to cancel it; a partial fill the
   reconciliation recovered first moved it to partially_filled, so the cancel
   is asked for again, journaled as one, as [retry_cancel] does.

   One DELETE per venue id, as [cancel_failed] sends: a live start reconciles
   twice, and a report written before the venue acts on a DELETE still says
   the order rests. A DELETE whose answer was not a confirmation is sent again
   the next time a reconciliation finds the order resting, and not before. *)
let resend_cancel t (trade : Venue.Trade.t) (o : Order.t) (v : Venue.Venue_order.t) :
    unit Deferred.t =
  let id = v.Venue.Venue_order.id in
  if Hash_set.mem t.resent_cancels id then Deferred.unit
  else (
    Hash_set.add t.resent_cancels id;
    let o =
      if Order.State.equal o.Order.state Order.State.Pending_cancel then o
      else record t o Order.Event.Cancel_requested
    in
    t.on_event
      (sprintf
         "desk      reconcile: %s is pending_cancel on the desk and %s at the venue \
          under id %s; the DELETE may never have arrived, so it is sent again"
         (key o) v.Venue.Venue_order.status id);
    match%map trade.Venue.Trade.cancel id with
    | Ok () -> ()
    | Error e ->
        Hash_set.remove t.resent_cancels id;
        t.on_event
          (sprintf
             "desk      reconcile: the venue's answer to cancelling %s (%s) again was \
              not a confirmation (%s); it may still be resting there, and is cancelled \
              again the next time a reconciliation finds it resting"
             (key o) id (Error.to_string_hum e)))

(* The failed orders the venue still works, found in the venue's own list of
   its open orders: the journal's open set cannot name them, failed being
   terminal. Each gets the venue's id, if it had none, and [cancel_failed]. *)
let reconcile_failed t (trade : Venue.Trade.t) : unit Deferred.t =
  match%bind trade.Venue.Trade.open_orders () with
  | Error e ->
      t.on_event
        (sprintf
           "desk      reconcile: the venue's open orders could not be read, so no order \
            this desk declared failed was looked for there: %s"
           (Error.to_string_hum e));
      Deferred.unit
  | Ok resting ->
      Deferred.List.iter ~how:`Sequential resting ~f:(fun v ->
          let failed =
            Option.bind
              (Ids.Client_order_id.of_string v.Venue.Venue_order.client_order_id)
              ~f:(Journal.load_order t.journal)
            |> Option.map ~f:(fun r -> r.Journal.Order_row.order)
            |> Option.filter ~f:(fun o ->
                Order.State.equal o.Order.state Order.State.Failed)
          in
          match failed with
          | Some o when Venue.Venue_order.rests v ->
              if Option.is_none o.Order.venue_order_id then
                ignore (record t o (Order.Event.Found v.Venue.Venue_order.id) : Order.t);
              cancel_failed t trade ~client:v.Venue.Venue_order.client_order_id v
          | Some _ | None -> Deferred.unit)

(* On start, and after every reconnection of the venue's update stream: the
   journal's open orders, each looked up by client order id and told what the
   venue knows (Reconcile.events_for), a pending_cancel one the venue reports
   resting sent its DELETE again ([resend_cancel]), and then the venue's open
   orders, for any this desk declared failed ([reconcile_failed]). Then the account is
   re-read, because fills nobody heard about moved it. *)
let reconcile t : unit Deferred.t =
  enqueue t (fun () ->
      let rows = Journal.open_orders t.journal in
      t.open_ <-
        String.Map.of_alist_reduce
          (List.map rows ~f:(fun r ->
               (key r.Journal.Order_row.order, r.Journal.Order_row.order)))
          ~f:(fun _ latest -> latest);
      match t.trade with
      | Error _ -> Deferred.unit
      | Ok trade ->
          let%bind () =
            Deferred.List.iter ~how:`Sequential rows ~f:(fun r ->
                let o = r.Journal.Order_row.order in
                (* The journal's word, before the venue's: this desk asked for
                   the cancel. *)
                let asked_to_cancel =
                  Order.State.equal o.Order.state Order.State.Pending_cancel
                in
                let%bind looked_up =
                  trade.Venue.Trade.find_order
                    o.Order.request.Order.Request.client_order_id
                in
                let o =
                  match looked_up with
                  | Error e ->
                      (* A lookup that errors decides no more than one that
                         misses. A pending order becomes what it was, an outcome
                         nobody learned, so the schedule below takes it; an
                         order that stayed pending would never be asked about
                         again, and with no venue id no kill could cancel it. *)
                      t.on_event
                        (sprintf "desk      reconcile: %s could not be looked up: %s"
                           (key o) (Error.to_string_hum e));
                      if Order.State.equal o.Order.state Order.State.Pending_submit then
                        record t o
                          (Order.Event.Outcome_unknown
                             "the process stopped before the venue answered, and the \
                              restart's lookup failed")
                      else o
                  | Ok venue ->
                      let events = Reconcile.events_for o venue ~at:(t.now ()) in
                      if Option.is_none venue && Option.is_some o.Order.venue_order_id
                      then
                        t.on_event
                          (sprintf
                             "desk      reconcile: the venue has no order %s, which it \
                              once acknowledged"
                             (key o));
                      List.fold events ~init:o ~f:(record t)
                in
                (* An order nobody has found yet -- this lookup missed it or
                   failed -- is decided by the lookups a timed-out submission
                   gets, not by this one question. [resolve] asks again after a
                   lookup that errors, as it does after one that misses. *)
                if Order.State.equal o.Order.state Order.State.Submit_unknown then
                  don't_wait_for
                    (resolve t o.Order.request.Order.Request.client_order_id
                       ~delays:t.resolve_delays);
                match looked_up with
                | Ok (Some v) when asked_to_cancel && Venue.Venue_order.rests v ->
                    resend_cancel t trade o v
                | Ok _ | Error _ -> Deferred.unit)
          in
          let%map () = reconcile_failed t trade in
          if not (List.is_empty rows) then t.after_fill ();
          t.on_event
            (sprintf "desk      reconciled %d open order%s against the venue"
               (List.length rows)
               (if List.length rows = 1 then "" else "s")))

let refresh ?(bars = true) t : unit Deferred.t =
  match t.read with
  | None -> Deferred.unit
  | Some read ->
      let%bind () =
        match%map read.Venue.Read.clock () with
        | Ok c -> set_clock t (Some c)
        | Error e ->
            set_clock t None;
            t.on_event
              ("desk      the venue's clock is unavailable, so the session reads closed: "
             ^ Error.to_string_hum e)
      in
      let%map () =
        match t.adv with
        | Adv.Fixed _ -> Deferred.unit
        | Adv.From_venue when not bars -> Deferred.unit
        | Adv.From_venue -> (
            (* When they were asked for, not when they answered: the hour
               [refresh_forever] waits is not lengthened by this request. *)
            let asked_at = Time_source.now t.time_source in
            match%map read.Venue.Read.daily_bars (Graph.symbols t.graph) ~days:20 with
            | Error e ->
                t.on_event
                  ("desk      twenty-day volume unavailable: " ^ Error.to_string_hum e)
            | Ok by_symbol ->
                t.bars_fetched_at <- Some asked_at;
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

(* The manager's own time source, never the wall clock: a test advances its
   clock and the session and the volumes follow. The session every [every],
   because it opens and closes by the minute; the bars once an hour has passed
   on that clock since the last bars that answered were asked for, because
   twenty-day volume moves by the day. Measured by the clock and not by turns
   of the loop: a count of turns is an hour at one interval only, and drifts by
   every refresh's own requests. Bars that failed are asked for again on the
   next turn, not an hour later: until they answer, a name with no volume is
   refused by the adv rule and one with volume is sized against an older
   average.

   And the clock again at the moment the one last read says the session
   changes, when that comes before [every]. Past the next close that clock no
   longer says when the session after it ends, so the rule reads closed until
   the next read (Rules.Session); the simulated venue's sessions follow one
   another with no gap, and a read up to a minute late would refuse the
   demo's own orders for that minute. The moment is on [now], the clock the
   rule reads; the wait is on [time_source]. Both are the wall clock outside
   a test. *)
let refresh_forever t ~every : unit Deferred.t =
  let hour = Time_ns.Span.of_hr 1.0 in
  let rec loop () =
    let bars =
      match t.bars_fetched_at with
      | None -> true
      | Some at ->
          Time_ns.Span.( >= ) (Time_ns.diff (Time_source.now t.time_source) at) hour
    in
    let%bind () = refresh t ~bars in
    let now = t.now () in
    let wait =
      match Option.bind t.session ~f:(Rules.Session.next_change ~now) with
      | Some change -> Time_ns.Span.min every (Time_ns.diff change now)
      | None -> every
    in
    let%bind () = Time_source.after t.time_source wait in
    loop ()
  in
  loop ()

let venue_name t = Option.value_map t.read ~default:"none" ~f:(fun r -> r.Venue.Read.name)

let fills_json t (rows : Journal.Fill_row.t list) : Yojson.Safe.t =
  `List
    (List.map2_exn rows (costs t rows)
       ~f:(fun (r : Journal.Fill_row.t) (_, (c : Tca.Costs.t)) ->
         let f = r.Journal.Fill_row.fill in
         let opt = Option.value_map ~default:`Null ~f:jnum in
         `Assoc
           [
             ("execution_id", `String f.Order.Fill.execution_id);
             ( "client_order_id",
               `String (Ids.Client_order_id.to_string r.Journal.Fill_row.client_order_id)
             );
             ("symbol", `String (Symbol.to_string r.Journal.Fill_row.symbol));
             ("side", `String (Order.Side.to_string r.Journal.Fill_row.side));
             ("qty", jnum f.Order.Fill.qty);
             ("price", jnum (Price.to_float f.Order.Fill.price));
             ("at", `String (Desk_time.rfc3339 f.Order.Fill.at));
             ("decision_price", jnum (Price.to_float r.Journal.Fill_row.decision_price));
             ( "arrival_bid",
               opt
                 (Option.map r.Journal.Fill_row.arrival ~f:(fun (b, _) ->
                      Price.to_float b)) );
             ( "arrival_ask",
               opt
                 (Option.map r.Journal.Fill_row.arrival ~f:(fun (_, a) ->
                      Price.to_float a)) );
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
      "The demo's venue fills every order half a spread from the mark, so these costs \
       are that half-spread, by construction."
  | _ ->
      "On the paper account every cost here measures Alpaca's fill simulator, against \
       IEX's quote -- one venue's, not the national best."

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
      ( "by_symbol",
        `Assoc
          (List.map (Tca.by_symbol rows) ~f:(fun (s, x) ->
               (Symbol.to_string s, summary x))) );
    ]

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

   ONE SUBMIT SITE. [submit_journaled] is the only place in this module, and
   so in the desk, that calls the venue's submit. Both adapters build their
   trading half at a permit only desk/wire.ml can make, so their submit is
   reached only through that module's one function, and a call written
   against the field does not compile. That this is the one place that
   calls the wire module, and the only file that names [submit_journaled],
   is held by test/test_rebalance.ml, which reads desk/ and bin/; so is that
   the simulator's own way in, which takes no permit
   (Sim_venue.For_testing), is named nowhere there but in its own file, where
   its trading half calls it. A ticket
   ([propose]) and a live strategy's rebalance ([propose_rebalance]) both
   journal first and then hand each order to it, and it asks, at that
   instant, what time can change since the rules ran -- the switch, the
   trading half, the book's currency and the session ([pre_wire]) -- before
   the wire.

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
  (* The book's kill_switch_trips_on: a limit named here that the gate
     cannot evaluate refuses a proposal that raises gross (Gate.check). *)
  trips_on : string list;
  read : Venue.Read.t option;
  trade : (Wire.permit Venue.Trade.t, string) Result.t;
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
  (* The client order ids of the orders [submit_journaled] sent while a
     limit's trip stood, because they only shrink a position (Rules.reduces,
     R2). [record] does not cancel one of them when the venue names it, as it
     cancels any other order that gets its id under the switch: it went out
     because the trip lets it. A hand's halt or the engine's stop landing
     while it is in flight still cancels it. *)
  through_trip : String.Hash_set.t;
  (* The sub-ledger's breaks as the checks have seen them ([check_subledger]):
     [seen_breaks], the names that broke at the last check that judged them;
     [standing_breaks], those that broke at two such checks running, each
     with the figures it was last judged at -- each alerted once, when it
     first stood, and what a break refuses ([check_rebalance]), whether or
     not the name can be judged at that moment. A name leaves both when a
     check finds it whole, and neither moves while it is not judged. *)
  mutable seen_breaks : Symbol.Set.t;
  mutable standing_breaks : Subledger.break Symbol.Map.t;
  (* Whether the last check could not read the sub-ledger, so a journal that
     stays unreadable is said once and not once a minute. *)
  mutable subledger_unread : bool;
}

let create ~graph ~journal ~spec ~trips_on ~read ~trade ~halt ~accepts_tickets ~adv ~now
    ~rng ~on_change ~on_event ~after_fill ~book_is_current
    ?(resolve_delays =
      [ Time_ns.Span.of_sec 2.0; Time_ns.Span.of_sec 10.0; Time_ns.Span.of_sec 30.0 ])
    ?(time_source = Time_source.wall_clock ()) () =
  {
    graph;
    journal;
    spec;
    trips_on;
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
    through_trip = String.Hash_set.create ();
    seen_breaks = Symbol.Set.empty;
    standing_breaks = Symbol.Map.empty;
    subledger_unread = false;
  }

let halt t = t.halt
let spec t = t.spec
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

(* The live mark for a symbol the book holds: a price of zero is a cell
   nobody has written, not a price, and a symbol outside the universe has
   none either. [context] uses this for a ticket's own symbol; [resting_fills]
   below uses it for every resting order, a market order's price and the mark
   a limit is priced no better than -- one function, so neither can answer
   differently for the same symbol at the same moment. *)
let live_mark t symbol =
  if
    List.mem (Graph.symbols t.graph) symbol ~equal:Symbol.equal
    && Float.( > ) (Price.to_float (Graph.price t.graph symbol)) 0.0
  then Some (Graph.price t.graph symbol)
  else None

(* The mark a rebalance is sized at (Task 1.7: the current mark, not
   yesterday's close): the feed's live price, and only while the feed calls
   the name fresh -- a name it calls stale, or has never seen print, has no
   current mark, and nothing is sized on an old one. *)
let fresh_mark t symbol : (float, string) Result.t =
  let health = Graph.feed_health t.graph in
  let listed names = List.mem names symbol ~equal:Symbol.equal in
  match live_mark t symbol with
  | None -> Error (sprintf "%s has no live mark" (Symbol.to_string symbol))
  | Some _
    when listed health.Graph.Feed_health.stale
         || listed health.Graph.Feed_health.never_seen ->
      Error
        (sprintf "%s's mark is stale, and a rebalance is sized only at a current one"
           (Symbol.to_string symbol))
  | Some p -> Ok (Price.to_float p)

(* The newest recorded session's close for [symbol], and its date: the
   calendar check's reading (Rebalance.calendar_refusal) -- that the record
   is current -- and no longer any order's price (Task 1.7). *)
let recorded_close t symbol =
  Option.map (Journal.latest_close t.journal symbol) ~f:(fun (date, close) ->
      (date, Price.of_float close))

(* The orders proposed within the duplicate rule's window, as of [now]. *)
let recent_within t ~now =
  List.filter t.recent ~f:(fun r ->
      Float.( < )
        (Time_ns.Span.to_sec (Time_ns.diff now r.Rules.Recent.at))
        t.spec.Desk_spec.duplicate_window_s)

(* The account's position in [symbol] as the gate reads it: the graph's book.
   A name the graph does not hold is flat here. *)
let account_position t symbol =
  if Graph.knows_symbol t.graph symbol then Qty.to_float (Graph.qty t.graph symbol)
  else 0.0

(* Failed orders the venue may still be working. A Failed order is one every
   lookup missed, and a lookup that misses does not mean the venue never took
   the order: it may still hold it, and the first thing the desk hears of it
   may be a fill. So the gate counts it, at its remaining quantity -- ordered
   less filled, the fills rebuilt from the journal -- until a venue report
   finishes it: a fill of the whole of it, or its cancel, expiry or rejection,
   which [on_update] journals against the failed order (Order.apply keeps the
   state and records the event beside an anomaly), so the journal is the
   record that the venue is done with it.

   And only while its session lasts. A day order does not outlive its
   session's close (and a market-on-open order on an old row, sent after one
   close, was worked at the next open and not after it) -- before the next
   close either way. The bound is
   the latest recorded session's DATE, not when that row was RECORDED
   (Session_close writes it a few minutes after the close): a close recorded
   late, during the following session -- Session_close missed one and caught
   up, or a restart re-ran it -- must not drop a failed order that is still
   live in that following session, which bounding by [recorded_at] would do.
   A failed order counts only if it was created at or after the start of the
   latest recorded session's date ([Journal.unfinished_failed_orders]); one
   created before that is over, whatever the venue said or did not say. When
   [created_at] is compared as UTC text, "the start of that date" reads as
   midnight UTC, which is earlier than the exchange's own midnight -- so this
   errs toward counting one already-dead session's failed orders once more,
   never toward dropping a live one. A close that went unrecorded -- a book
   not current, an engine down at the close -- leaves the order counted until
   the next one is recorded: longer than it lives, never shorter.
   [failed_cancels] is not the test: a DELETE the venue took is not the venue
   saying the order is done, and until it says so the order may fill.
   [reconcile_failed] cancels one it finds still resting; the venue's answer
   to that cancel is the report that ends its count.

   A journal that cannot be read is an Error, as an order that cannot be
   priced is ([resting_fills]): the proposal is refused, saying so, and
   [preview] does not raise. *)
let failed_still_working t : (Order.t list, string) Result.t =
  match
    Or_error.try_with (fun () ->
        let since =
          Option.map
            (List.hd (Journal.recent_sessions t.journal ~limit:1))
            ~f:(fun s -> s.Journal.Session.date)
        in
        Journal.unfinished_failed_orders t.journal ~since)
  with
  | Error e ->
      Error
        (sprintf
           "the journal's failed orders could not be read (%s), so the gate cannot count \
            any the venue may still be working; the proposal is refused rather than \
            judged without them"
           (Error.to_string_hum e))
  | Ok rows ->
      Ok
        (List.map rows ~f:(fun r -> r.Journal.Order_row.order)
        |> List.filter ~f:(fun o ->
            Float.( > ) (Order.remaining_qty o) Order.epsilon
            && not (Map.mem t.open_ (key o))))

(* The account's position in [symbol] if every order of the desk's own that
   may still work in it and would shrink it filled -- the room a reduction
   through a limit's trip is judged against (Rules.reduces). Two reductions
   in one name (two strategies, or a hand's sell resting) are each no larger
   than the account, and together could take it through zero; netting the
   others out refuses the second. Counted: every open order, and every failed
   one the venue may still be working ([failed_still_working], the orders
   [strategy_position] also calls "may still fill"), at its remaining
   quantity. Orders that would grow it are not counted: they may never fill.
   [except] is the order being judged, which [admit] has already put among
   the open ones by the time [submit_journaled] asks. A journal whose failed
   orders cannot be read leaves the room unknown -- nan, which [reduces]
   never passes -- rather than judged without them. *)
let reducible_account ?except t symbol =
  let held = account_position t symbol in
  let shrinking (o : Order.t) =
    let r = o.Order.request in
    if
      (not (Symbol.equal r.Order.Request.symbol symbol))
      || Option.exists except ~f:(String.equal (key o))
    then 0.0
    else
      match r.Order.Request.side with
      | Order.Side.Sell when Float.( > ) held 0.0 -> Order.remaining_qty o
      | Order.Side.Buy when Float.( < ) held 0.0 -> Order.remaining_qty o
      | Order.Side.Sell | Order.Side.Buy -> 0.0
  in
  match failed_still_working t with
  | Error _ -> Float.nan
  | Ok failed ->
      let taken = List.sum (module Float) (Map.data t.open_ @ failed) ~f:shrinking in
      if Float.( > ) held 0.0 then held -. taken
      else if Float.( < ) held 0.0 then held +. taken
      else held

(* ------------------------------------------------------------------------ *)
(* One sub-ledger check (desk/subledger.ml)                                  *)
(* ------------------------------------------------------------------------ *)

(* What one reading of the sub-ledger found. [checked]: the names judged;
   [unsettled]: the names a strategy trades that were not, because an order
   of the desk's in them may still fill. *)
module Subledger_reading = struct
  type t = {
    breaks : Subledger.break list;
    checked : Symbol.Set.t;
    unsettled : Symbol.Set.t;
  }
end

(* The sub-ledger, read: every journaled fill's net quantity by its order's
   source, grouped by owner (Subledger.owner -- each strategy, and the hand
   for every ticket), against the account's position as the gate reads it
   ([account_position]), by Subledger.check.

   WHICH NAMES. Those a strategy has a fill in, ever: the names strategies
   trade. A name no strategy has traded has no strategy's sizing or stop
   that depends on it, and the account may hold lots there that no journal
   row explains -- the droplet's book holds AAPL 400, MSFT 200, NVDA 60, JPM
   250, XOM -500 and CVX -300, held by no strategy and bought before the
   journal, or by hand -- which would otherwise read as breaks forever. In a
   name a strategy does trade, the hand's own fills are one more sub-ledger,
   so a ticket there sums too.

   WHEN. A name with an order of the desk's that may still fill -- open, or
   failed and possibly still worked ([failed_still_working]), the orders
   [strategy_position] calls unsettled -- is not judged at all: the venue
   can move the account before the fill's report reaches the journal, and a
   sum read in that instant is not a break. [except] is an order that is
   not counted, the one [pre_wire] is judging, which [admit] has already put
   among the open ones. A journal that cannot be read is an Error, never a
   reading with nothing in it. *)
let subledger ?except t : (Subledger_reading.t, string) Result.t =
  match Or_error.try_with (fun () -> Journal.fills_by_source t.journal) with
  | Error e ->
      Error (sprintf "the journal's fills could not be read (%s)" (Error.to_string_hum e))
  | Ok rows -> (
      match failed_still_working t with
      | Error why -> Error why
      | Ok failed ->
          let own =
            List.map rows ~f:(fun (source, symbol, q) ->
                (Subledger.owner ~source, symbol, q))
          in
          let traded =
            Symbol.Set.of_list
              (List.filter_map own ~f:(fun (owner, symbol, _) ->
                   Option.some_if (not (String.equal owner Subledger.hand)) symbol))
          in
          let in_flight =
            Symbol.Set.of_list
              (List.filter_map
                 (Map.data t.open_ @ failed)
                 ~f:(fun o ->
                   Option.some_if
                     (Float.( > ) (Order.remaining_qty o) Order.epsilon
                     && not (Option.exists except ~f:(String.equal (key o))))
                     o.Order.request.Order.Request.symbol))
          in
          let checked = Set.diff traded in_flight in
          Ok
            {
              Subledger_reading.breaks =
                Subledger.check
                  ~own:(List.filter own ~f:(fun (_, symbol, _) -> Set.mem checked symbol))
                  ~account:
                    (List.map (Set.to_list checked) ~f:(fun s ->
                         (s, account_position t s)))
                  ~tolerance:Subledger.tolerance;
              checked;
              unsettled = Set.inter traded in_flight;
            })

(* Rules.Context's [netted] for [symbol]: judged in this reading, and whole.
   Read only under a limit's trip with the book's (reduce_through_trip) word
   on, the one place it decides anything; false otherwise, and false when
   the sub-ledger cannot be read. *)
let netted ?except t symbol =
  match Halt.state t.halt with
  | Halt.State.Tripped _ when t.spec.Desk_spec.reduce_through_trip -> (
      match subledger ?except t with
      | Error _ -> false
      | Ok r ->
          Set.mem r.Subledger_reading.checked symbol
          && not
               (List.exists r.Subledger_reading.breaks ~f:(fun b ->
                    Symbol.equal b.Subledger.symbol symbol)))
  | Halt.State.Tripped _ | Halt.State.Clear | Halt.State.Halted _ | Halt.State.Stopped _
    ->
      false

(* One break said: a line on the log and a row in the journal's alerts,
   kind subledger_break, the name as its limit. The write is caught: a
   failed alert is said and changes nothing else. *)
let say_break t ~at ~(occasion : string) (b : Subledger.break) =
  let name = Symbol.to_string b.Subledger.symbol in
  let line =
    sprintf
      "sub-ledger %s: %s; nothing that grows a strategy's position in %s is sent, and a \
       reduction only if it also shrinks the account's, until a person settles it. %s"
      occasion (Subledger.describe b) name Subledger.settle
  in
  (match
     Or_error.try_with (fun () ->
         Journal.record_alert t.journal
           { Journal.Alert.at; kind = "subledger_break"; limit_name = name; line })
   with
  | Ok () -> ()
  | Error e ->
      t.on_event
        (sprintf "desk      the sub-ledger alert for %s could NOT be journaled (%s)" name
           (Error.to_string_hum e)));
  t.on_event ("desk      " ^ line)

(* A reading, folded into what the checks have seen. A break STANDS when the
   name broke at this check and at the last one that judged it -- a break
   must persist across a settled state to count, so a sum read between a
   fill reaching the account and its report reaching the journal, or a
   positions read the venue has not yet caught up on, is never alerted nor
   refuses -- and it is alerted once, when it first stands ([alert]).
   A name judged whole leaves both sets, said once if it had stood; a name
   not judged keeps whatever it had. *)
let fold_reading ?(alert = true) t ~at (r : Subledger_reading.t) =
  let broken =
    Symbol.Set.of_list
      (List.map r.Subledger_reading.breaks ~f:(fun b -> b.Subledger.symbol))
  in
  let standing = Set.inter broken t.seen_breaks in
  if alert then
    List.iter r.Subledger_reading.breaks ~f:(fun b ->
        if
          Set.mem standing b.Subledger.symbol
          && not (Map.mem t.standing_breaks b.Subledger.symbol)
        then say_break t ~at ~occasion:"at a sync" b);
  let whole = Set.diff r.Subledger_reading.checked broken in
  Set.iter whole ~f:(fun s ->
      if Map.mem t.standing_breaks s then
        t.on_event (sprintf "desk      sub-ledger: %s sums again" (Symbol.to_string s)));
  t.seen_breaks <- Set.union (Set.diff t.seen_breaks whole) broken;
  t.standing_breaks <-
    List.fold r.Subledger_reading.breaks
      ~init:(Map.filter_keys t.standing_breaks ~f:(fun s -> not (Set.mem whole s)))
      ~f:(fun acc b ->
        if Set.mem standing b.Subledger.symbol then
          Map.set acc ~key:b.Subledger.symbol ~data:b
        else acc)

(* The sync pass's check (Desk.sync_with, after each read of the account is
   applied): one alert per break, when it first stands, and none after
   while it stands. *)
let check_subledger t ~at =
  match subledger t with
  | Error why ->
      if not t.subledger_unread then (
        t.subledger_unread <- true;
        t.on_event
          (sprintf
             "desk      the sub-ledger could not be checked (%s); a rebalance is refused \
              while it cannot"
             why))
  | Ok r ->
      t.subledger_unread <- false;
      fold_reading t ~at r

(* The nightly close's check (Session_close.run_forever, once the session
   is recorded): every break standing at it said again, one alert each --
   the night's record that the sub-ledgers sum, or which name breaks. A
   break that first stands at this check is said here and not twice. *)
let close_subledger t ~(date : Date.t) ~at =
  let day = Date.to_string date in
  match subledger t with
  | Error why ->
      t.on_event
        (sprintf "desk      sub-ledger at the close of %s could not be checked: %s" day
           why)
  | Ok r -> (
      fold_reading ~alert:false t ~at r;
      (* Every break standing, a name not judged tonight among them, at the
         figures it was last judged at. *)
      let standing = Map.data t.standing_breaks in
      List.iter standing ~f:(fun b ->
          say_break t ~at
            ~occasion:
              (if Set.mem r.Subledger_reading.checked b.Subledger.symbol then
                 sprintf "at the close of %s" day
               else
                 sprintf
                   "at the close of %s (as last judged; an order in it may still fill)"
                   day)
            b);
      let n = Set.length r.Subledger_reading.checked in
      let unsettled =
        match Set.to_list r.Subledger_reading.unsettled with
        | [] -> ""
        | names ->
            sprintf "; not judged, an order in them may still fill: %s"
              (String.concat ~sep:", " (List.map names ~f:Symbol.to_string))
      in
      match (standing, r.Subledger_reading.breaks) with
      | _ :: _, _ -> ()
      | [], [] ->
          t.on_event
            (sprintf
               "desk      sub-ledger at the close of %s: %d name%s checked, no break%s"
               day n
               (if n = 1 then "" else "s")
               unsettled)
      | [], seen ->
          t.on_event
            (sprintf
               "desk      sub-ledger at the close of %s: %d name%s checked, no standing \
                break; seen once, and judged again at the next sync: %s%s"
               day n
               (if n = 1 then "" else "s")
               (String.concat ~sep:"; " (List.map seen ~f:Subledger.describe))
               unsettled))

(* [own]: the proposer's own position in [symbol] -- a strategy's, from its
   fills; 0 for a ticket, which is no strategy's. *)
let context ?(origin = Rules.Origin.Hand) ?(own = 0.0) t ~(source : string)
    ~(symbol : Symbol.t) : Rules.Context.t =
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
    halt_state = Halt.state t.halt;
    halt_why = Halt.reason t.halt;
    own;
    account = reducible_account t symbol;
    netted = netted t symbol;
    session = t.session;
    mark = live_mark t symbol;
    origin;
    new_york = Lazy.force Desk_time.new_york;
    stale =
      held
      && (listed health.Graph.Feed_health.stale
         || listed health.Graph.Feed_health.never_seen);
    adv20 =
      (match t.adv with
      | Adv.Fixed n -> Some n
      | Adv.From_venue -> Map.find t.adv20 symbol);
    recent = recent_within t ~now;
    open_orders = Map.length t.open_;
    resting =
      List.filter_map (Map.data t.open_) ~f:(fun o ->
          if Float.( > ) (Order.remaining_qty o) Order.epsilon then
            Some (o.Order.request.Order.Request.symbol, o.Order.request.Order.Request.side)
          else None);
    source;
    now;
  }

(* The ways the orders already resting might fill, each a base the gate
   applies before the proposal ([Gate.check]'s [base]). Nobody knows which
   resting orders will fill, so a proposal is judged against every one of
   these and passes only if it passes under each: assuming they all fill is
   one guess among several, and on its own it loosens the gate -- a resting
   sell assumed filled makes room for a buy the book as it stands cannot
   take (Task 3's review, C1).

   - [As_it_stands]: no base. The check before resting orders counted, so the
     gate is never looser than it was.
   - [Resting_buys], [Resting_sells]: every resting buy, or every resting
     sell. A per-name or sector notional is the absolute value of a sum whose
     coefficients (the marks) are all positive, so over any subset of resting
     fills it is largest at one of these two.
   - [Growing_side]: for each name the PROPOSAL ITSELF TRADES, that name's
     resting buys when the proposal's own net quantity in it is positive, its
     resting sells when negative -- because |position + fills| is
     non-decreasing in however much more moves the same way the proposal
     already moves it, so the worst case is the resting side that agrees with
     the proposal's own direction, not simply whichever side is larger on its
     own: the larger-magnitude side can be the one the proposal is shrinking,
     which lets through a fill combination that actually breaches (Task 3's
     re-review, the Important). A name the proposal leaves flat, or does not
     trade at all, falls back to whichever of that name's resting buys or
     resting sells takes |position now + that side| further from zero, as
     before.

     Gross is a sum of per-name absolute values, so FOR A SINGLE-NAME
     PROPOSAL this scenario is the exact worst case for gross. A proposal
     that trades several names at once (Task 15's rebalance) is bounded only
     approximately: growing every name on its own worst side is not
     necessarily the one combination of fills that maximizes the sum, since
     several names can trade off against each other in ways a single-name
     proposal cannot.
   - [Every_resting]: all of them. Each resting fill is priced no better than
     the mark ([resting_fills]), so each can only lower equity, and this is
     where drawdown is deepest.

   VaR and the Greek limits are not linear in the positions, so for them the
   five are a bound only approximately. *)
module Scenario = struct
  type t = As_it_stands | Resting_buys | Resting_sells | Growing_side | Every_resting
  [@@deriving equal, enumerate]

  let describe = function
    | As_it_stands -> "on the book as it stands"
    | Resting_buys -> "if the resting buys fill"
    | Resting_sells -> "if the resting sells fill"
    | Growing_side -> "if each name's resting orders fill on the side that grows it"
    | Every_resting -> "if every resting order fills"
end

(* Every distinct scenario's verdict, in [Scenario.all]'s order, so the book
   as it stands is always first. Two scenarios with the same base are gated
   once, under the first one's name: with nothing resting there is one. *)
module Scenarios = struct
  type t = (Scenario.t * Gate.Verdict.t) list

  let passed (t : t) = List.for_all t ~f:(fun (_, v) -> Gate.Verdict.passed v)
  let as_it_stands (t : t) = snd (List.hd_exn t)
  let first_failure (t : t) = List.find t ~f:(fun (_, v) -> not (Gate.Verdict.passed v))

  (* The first failing scenario's reasons, each naming it. *)
  let reasons (t : t) =
    match first_failure t with
    | None -> []
    | Some (s, v) ->
        List.map (Gate.Verdict.reasons v) ~f:(fun r ->
            sprintf "%s (%s)" r (Scenario.describe s))
end

module Preview = struct
  type t = {
    request : Order.Request.t;
    failures : Rules.Failure.t list;
    (* None: no gate was run -- there was no price to run it at, or the
       ticket names a symbol outside the book, which a rule already refuses.
       Error: an order already resting could not be counted, and why; the
       proposal is refused for it ([resting_fills]). *)
    gate : (Scenarios.t, string) Result.t option;
    decision_price : Price.t option;
  }

  let passed t =
    List.is_empty t.failures
    &&
    match t.gate with
    | Some (Ok s) -> Scenarios.passed s
    | Some (Error _) | None -> false

  let reasons t =
    List.map t.failures ~f:(fun f ->
        sprintf "%s: %s" f.Rules.Failure.rule f.Rules.Failure.why)
    @
    match t.gate with
    | Some (Ok s) -> Scenarios.reasons s
    | Some (Error why) -> [ why ]
    | None -> []

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

  (* The gate's answer as a word (Gate.Outcome.word, matched whole), and
     the trip-limits refused for being unknown -- names only, as every other
     refusal names its limits; they stand beside a breach that outranked
     them too. *)
  let outcome_json (v : Gate.Verdict.t) =
    let names = v.Gate.Verdict.unknown_trips in
    [
      ("outcome", `String (Gate.Outcome.word v.Gate.Verdict.outcome));
      ("refused_unevaluable", `List (List.map names ~f:(fun n -> `String n)));
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
        (* [passed] is every scenario's; the moves and the figures beside it
           are the book as it stands -- the live book the page shows, and
           what this order alone does to it -- and [scenarios] says how the
           order fares under each way the resting orders might fill. A gate
           that could not be run is null, and the reasons say why. *)
        ( "gate",
          match t.gate with
          | None | Some (Error _) -> `Null
          | Some (Ok s) ->
              let v = Scenarios.as_it_stands s in
              `Assoc
                ([ ("passed", `Bool (Scenarios.passed s)) ]
                @ outcome_json v
                @ [
                    ("created", moves_json v.Gate.Verdict.created);
                    ("worsened", moves_json v.Gate.Verdict.worsened);
                    ("cleared", moves_json v.Gate.Verdict.cleared);
                    ("unevaluable", moves_json v.Gate.Verdict.unevaluable);
                    ("gross_before", money v.Gate.Verdict.gross_before);
                    ("gross_after", money v.Gate.Verdict.gross_after);
                    ("equity_before", money v.Gate.Verdict.equity_before);
                    ("equity_after", money v.Gate.Verdict.equity_after);
                    ( "scenarios",
                      `List
                        (List.map s ~f:(fun (scenario, v) ->
                             `Assoc
                               ([
                                  ("scenario", `String (Scenario.describe scenario));
                                  ("passed", `Bool (Gate.Verdict.passed v));
                                ]
                               @ outcome_json v
                               @ [
                                   ("created", moves_json v.Gate.Verdict.created);
                                   ("worsened", moves_json v.Gate.Verdict.worsened);
                                 ]))) );
                  ]) );
        ("reasons", `List (List.map (reasons t) ~f:(fun s -> `String s)));
      ]
end

(* An order as a refusal names it: its client id, its state, and what of it
   rests. *)
let describe_resting (o : Order.t) =
  let r = o.Order.request in
  sprintf "%s (%s: %s %s, %.17g remaining, %s)" (key o)
    (Order.State.to_string o.Order.state)
    (Symbol.to_string r.Order.Request.symbol)
    (Order.Side.to_string r.Order.Request.side)
    (Order.remaining_qty o)
    (match r.Order.Request.kind with
    | Order.Kind.Market -> "market"
    | Order.Kind.Limit p -> sprintf "limit %.2f" (Price.to_float p))

(* Every order that may still fill, as the fill it would be: the open orders
   ([t.open_], the set [open_count] and [cancel_all] read) and the failed
   ones the venue may still work ([failed_still_working]), each at its
   remaining quantity (Order.remaining_qty), signed by side.

   PRICED NO BETTER THAN THE MARK: a buy at the higher of its limit and the
   mark, a sell at the lower; at its limit alone when there is no mark; a
   market order at the mark. A passive limit priced at its own limit would
   book as equity the difference between that limit and the mark, which
   nobody has earned -- and the gate's drawdown would read it. A notional
   limit measures quantity times the fork's mark, so the price moves equity
   and drawdown and nothing else.

   FAILING CLOSED, WITH A REASON. An order that cannot be priced -- a market
   order in a name that has not printed -- or whose symbol the book does not
   hold (a journal written against another book) cannot be counted, and is
   not dropped: dropping it would judge the proposal as if it were not there.
   The whole list is an Error instead, naming the order, and the proposal is
   refused for it, journaled with that reason like any other refusal. The
   symbol is checked before anything reads the graph, which raises for a
   name it does not hold. *)
let resting_fills t : (Gate.Fill.t list, string) Result.t =
  let%bind.Result failed = failed_still_working t in
  Map.data t.open_ @ failed
  |> List.map ~f:(fun (o : Order.t) ->
      let r = o.Order.request in
      let symbol = r.Order.Request.symbol in
      let cannot why =
        Error
          (sprintf
             "the resting order %s %s, so the gate cannot count it; the proposal is \
              refused rather than judged as if that order were not there"
             (describe_resting o) why)
      in
      if not (Graph.knows_symbol t.graph symbol) then
        cannot "is in a symbol this book does not hold"
      else
        let limit =
          Option.map (Order.Kind.limit_price r.Order.Request.kind) ~f:Price.to_float
        in
        let mark = Option.map (live_mark t symbol) ~f:Price.to_float in
        let price =
          match (limit, mark) with
          | Some l, Some m ->
              Some
                (match r.Order.Request.side with
                | Order.Side.Buy -> Float.max l m
                | Order.Side.Sell -> Float.min l m)
          | Some l, None -> Some l
          | None, m -> m
        in
        match price with
        | None -> cannot "has no limit price and no mark"
        | Some p ->
            Ok
              {
                Gate.Fill.symbol;
                qty =
                  Qty.of_float
                    (Order.Side.sign r.Order.Request.side *. Order.remaining_qty o);
                price = Price.of_float p;
              })
  |> Result.all

(* The gate as the desk runs it: [fills] -- a proposal's own -- judged under
   every scenario ([Scenario]) of what already rests, each a fork of its own.
   The one function [preview] gates through, and so [propose], which takes
   its verdict from [preview]. Every symbol in [fills] must be one the book
   holds; [preview] runs no gate for a ticket that names another. *)
let gate_scenarios t ~(fills : Gate.Fill.t list) : (Scenarios.t, string) Result.t =
  Result.map (resting_fills t) ~f:(fun resting ->
      let by_symbol fs =
        List.fold fs ~init:Symbol.Map.empty ~f:(fun acc (f : Gate.Fill.t) ->
            Map.update acc f.Gate.Fill.symbol ~f:(fun q ->
                Option.value q ~default:0.0 +. Qty.to_float f.Gate.Fill.qty))
      in
      let buy (f : Gate.Fill.t) = Float.( > ) (Qty.to_float f.Gate.Fill.qty) 0.0 in
      let sell (f : Gate.Fill.t) = Float.( < ) (Qty.to_float f.Gate.Fill.qty) 0.0 in
      let proposed = by_symbol fills in
      let buys = by_symbol (List.filter resting ~f:buy) in
      let sells = by_symbol (List.filter resting ~f:sell) in
      let total m symbol = Option.value (Map.find m symbol) ~default:0.0 in
      (* Whether a name's resting buys take |q0 + proposal + side| at least as
         far from zero as its resting sells, q0 being the position now. Only
         the fallback for a name the proposal itself leaves flat or does not
         trade -- see [growing_side_buys]. *)
      let buys_grow symbol =
        let q = Qty.to_float (Graph.qty t.graph symbol) +. total proposed symbol in
        Float.( >= )
          (Float.abs (q +. total buys symbol))
          (Float.abs (q +. total sells symbol))
      in
      (* [Growing_side]'s choice for [symbol]: the proposal's own direction in
         it, when the proposal trades that name at all (a positive net picks
         buys, a negative one sells), since |x + p| only grows as more of it
         moves the same way [p] does; the larger-magnitude side on its own can
         be the side the proposal is shrinking instead. Falls back to
         [buys_grow] when the proposal's net in [symbol] is exactly zero. *)
      let growing_side_buys symbol =
        let net = total proposed symbol in
        if Float.( > ) net 0.0 then true
        else if Float.( < ) net 0.0 then false
        else buys_grow symbol
      in
      let takes (scenario : Scenario.t) (f : Gate.Fill.t) =
        match scenario with
        | Scenario.As_it_stands -> false
        | Scenario.Resting_buys -> buy f
        | Scenario.Resting_sells -> sell f
        | Scenario.Growing_side ->
            if growing_side_buys f.Gate.Fill.symbol then buy f else sell f
        | Scenario.Every_resting -> true
      in
      List.fold Scenario.all ~init:[] ~f:(fun seen scenario ->
          let taken = List.map resting ~f:(takes scenario) in
          if List.exists seen ~f:(fun (_, t') -> List.equal Bool.equal t' taken) then seen
          else (scenario, taken) :: seen)
      |> List.rev
      |> List.map ~f:(fun (scenario, taken) ->
          let base =
            List.filter_map (List.zip_exn resting taken) ~f:(fun (f, took) ->
                Option.some_if took f)
          in
          (scenario, Gate.check t.graph ~base ~trips_on:t.trips_on ~fills)))

(* The gate for orders at their decision prices, each signed by its side:
   what [preview] runs for a ticket's one order and [check_rebalance] for a
   rebalance's many, one call for each combination of them that could be
   its fills (Rebalance.fill_sets). *)
let gate_orders t (priced : (Order.Request.t * Price.t) list) =
  gate_scenarios t
    ~fills:
      (List.map priced ~f:(fun ((r : Order.Request.t), price) ->
           {
             Gate.Fill.symbol = r.Order.Request.symbol;
             qty =
               Qty.of_float
                 (Order.Side.sign r.Order.Request.side *. Float.of_int r.Order.Request.qty);
             price;
           }))

(* The rules and the gate, and nothing created. The gate runs whenever there
   is a price to run it at, even after a rule has failed: someone fixing a
   ticket wants to know what the trade would do to the limits as well as why
   it cannot be sent. A market order is gated at the mark, a limit order at
   its limit (§3.7). *)
let preview ?(source = "manual") t (ticket : Ticket.t) : Preview.t =
  let now = t.now () in
  let request =
    Ticket.to_request ticket
      ~client_order_id:(Ids.Client_order_id.generate ~now ~rng:t.rng)
  in
  let ctx = context t ~source ~symbol:ticket.Ticket.symbol in
  let decision_price =
    match ticket.Ticket.kind with
    | Order.Kind.Limit p -> Some p
    | Order.Kind.Market -> ctx.Rules.Context.mark
  in
  let gate =
    match decision_price with
    | Some price
      when Set.mem ctx.Rules.Context.universe ticket.Ticket.symbol
           && ticket.Ticket.qty > 0 ->
        Some (gate_orders t [ (request, price) ])
    | _ -> None
  in
  { Preview.request; failures = Rules.check ctx request; gate; decision_price }

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
  let through_trip =
    match Halt.state t.halt with
    | Halt.State.Tripped _ -> Hash_set.mem t.through_trip (key o')
    | Halt.State.Clear | Halt.State.Halted _ | Halt.State.Stopped _ -> false
  in
  if Order.State.is_terminal o'.Order.state then Hash_set.remove t.through_trip (key o');
  if
    Option.is_some (Halt.reason t.halt)
    && (not through_trip)
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

(* What time can change between the rules and the wire, asked again at the
   instant an order is about to leave: the switch (a kill sets it outside the
   sequencer, so it can land while a quote or an earlier submit is awaited),
   the trading half, the book's currency (a book current when the rules ran
   can age past its window), the session -- a day order's regular session,
   and for the desk's own order 09:35 New York (Rules.session_refusal, the
   rules' own function) -- and, for the desk's own order, its quote: one
   fetched more than the book's quote_max_age_s ago no longer prices it
   (Rebalance.quote_refusal, the [quote] rule's own function). The first
   that fails is the refusal, in the rules' words.

   The switch is the rules' own function too (Rules.switch_refusal): under a
   limit's trip an order that only shrinks the proposer's own position [own]
   and either the account's or a sub-ledger known to sum (Rules.reduces)
   passes, the account's read here, at this instant, net of the desk's other
   resting reductions in the name ([reducible_account]), and the sub-ledger
   read here too ([netted]), the order itself -- open since [admit] -- not
   counted as one that may still fill. [own] is what the rules were given:
   a strategy's fills change only in a sequencer job, and this runs inside
   the one that checked it. *)
let pre_wire ?(origin = Rules.Origin.Hand) t ~(own : float) (request : Order.Request.t) :
    (Wire.permit Venue.Trade.t, Rules.Failure.t) Result.t =
  let fail rule why = Error { Rules.Failure.rule; why } in
  let tif = request.Order.Request.tif in
  let except = Ids.Client_order_id.to_string request.Order.Request.client_order_id in
  match
    ( Rules.switch_refusal ~spec:t.spec ~halt_state:(Halt.state t.halt)
        ~halt_why:(Halt.reason t.halt) ~own
        ~account:(reducible_account ~except t request.Order.Request.symbol)
        ~netted:(netted ~except t request.Order.Request.symbol)
        ~side:request.Order.Request.side ~qty:request.Order.Request.qty,
      t.trade )
  with
  | Some why, _ -> fail "kill_switch" why
  | None, Error why -> fail "trading" why
  | None, Ok _ when not (t.book_is_current ()) -> fail "trading" Session_close.not_current
  | None, Ok trade -> (
      let now = t.now () in
      match
        Rules.session_refusal ~desk:(Rules.Origin.desk origin) ~tif
          ~zone:(Lazy.force Desk_time.new_york)
          ~session:t.session ~now ()
      with
      | Some why -> fail "session" why
      | None -> (
          match origin with
          | Rules.Origin.Hand -> Ok trade
          | Rules.Origin.Desk q -> (
              match
                Rebalance.quote_refusal
                  ~shrinks:
                    (Rules.shrinks_position ~position:own ~side:request.Order.Request.side
                       ~qty:request.Order.Request.qty)
                  ~now ~max_age_s:t.spec.Desk_spec.quote_max_age_s q
              with
              | None -> Ok trade
              | Some why ->
                  fail "quote"
                    (sprintf "%s: %s" (Symbol.to_string request.Order.Request.symbol) why)
              )))

let reason (f : Rules.Failure.t) =
  sprintf "%s: %s" f.Rules.Failure.rule f.Rules.Failure.why

(* Two seconds for a quote from the venue's REST endpoint. The sequencer is
   held while it is fetched. A ticket is better sent without one than held
   while a request hangs; its costs then carry shortfall alone. A desk order
   is priced off it (Task 1.7), and with none is refused ([quote]).

   WHY THE VENUE'S QUOTE AND NOT THE FEED'S. The feed's quote stream keeps a
   midpoint per name for the print filter (lib/feed/alpaca_ws.ml), with no
   bid or ask beside it and an age bound of 60 s, and it may be refused
   altogether on the Basic plan (Task 1.3); the REST quote is the venue's
   own bid and ask, stamped, asked for at the instant the order is about to
   leave -- the quote it is priced off is the quote it is sent against, and
   the one its costs are measured from. *)
let fetch_quote t symbol : Venue.Quote.t option Deferred.t =
  match t.read with
  | None -> return None
  | Some read -> (
      match%map
        Time_source.with_timeout t.time_source (Time_ns.Span.of_sec 2.0)
          (read.Venue.Read.latest_quote symbol)
      with
      | `Result (Ok (Some q)) -> Some q
      | `Result (Ok None) | `Result (Error _) | `Timeout -> None)

let arrival_quote t symbol =
  Deferred.map (fetch_quote t symbol)
    ~f:(Option.map ~f:(fun (q : Venue.Quote.t) -> (q.Venue.Quote.bid, q.Venue.Quote.ask)))

(* The journal, and only then the wire (invariant 10): every order written
   pending_submit in ONE transaction, then held open and remembered for the
   duplicate rule. The write raises if the record cannot be made, and a raise
   here is before any submit, so nothing leaves for an order the record does
   not hold -- and, the transaction being one, no rebalance is left half
   journaled. *)
let admit t (orders : Journal.Insert.t list) =
  let now = t.now () in
  Journal.insert_orders t.journal orders ~at:now;
  List.iter orders ~f:(fun (i : Journal.Insert.t) ->
      let o = i.Journal.Insert.order in
      let r = o.Order.request in
      t.open_ <- Map.set t.open_ ~key:(key o) ~data:o;
      t.recent <-
        {
          Rules.Recent.symbol = r.Order.Request.symbol;
          side = r.Order.Request.side;
          qty = r.Order.Request.qty;
          at = now;
          source = i.Journal.Insert.source;
        }
        :: recent_within t ~now);
  t.on_change ()

(* THE ONE SUBMIT SITE: the only call of the venue's submit in the desk. [o]
   is journaled pending_submit already ([admit]). [pre_wire] is asked at this
   instant; if it fails, the order is moved to rejected_pre_trade with the
   reason -- a legal move from pending_submit -- and nothing is sent.
   Otherwise the order goes, and the venue's answer is recorded and returned
   as what it was: acknowledged, or not -- refused, or unknown, which is
   resolved by lookups and never by sending it again. A rebalance goes on to
   its next order only after an acknowledgement. An order that passes while a
   limit's trip stands passed because it only shrinks a position, and is
   remembered as such ([through_trip]) before it is sent. *)
let submit_journaled ?origin t ~(own : float) (o : Order.t) :
    [ `Acknowledged of Order.t
    | `Not_acknowledged of Order.t * string
    | `Stopped of Rules.Failure.t * Order.t ]
    Deferred.t =
  let request = o.Order.request in
  match pre_wire ?origin t ~own request with
  | Error f ->
      return (`Stopped (f, record t o (Order.Event.Pre_trade_rejected [ reason f ])))
  | Ok trade -> (
      (match Halt.state t.halt with
      | Halt.State.Tripped _ -> Hash_set.add t.through_trip (key o)
      | Halt.State.Clear | Halt.State.Halted _ | Halt.State.Stopped _ -> ());
      let%map submission = Wire.submit trade request in
      match submission with
      | Venue.Submission.Accepted v ->
          `Acknowledged (record t o (Order.Event.Acknowledged v.Venue.Venue_order.id))
      | Venue.Submission.Rejected why ->
          `Not_acknowledged
            ( record t o (Order.Event.Venue_rejected_submission why),
              "the venue refused it: " ^ why )
      | Venue.Submission.Unknown why ->
          let o = record t o (Order.Event.Outcome_unknown why) in
          don't_wait_for
            (resolve t request.Order.Request.client_order_id ~delays:t.resolve_delays);
          `Not_acknowledged (o, "its outcome is unknown: " ^ why))

let propose t ?(source = "manual") (ticket : Ticket.t) : (Preview.t * Order.t) Deferred.t
    =
  enqueue t (fun () ->
      let p = preview t ~source ticket in
      if not (Preview.passed p) then return (p, refuse t p ~source)
      else
        let request = p.Preview.request in
        let%bind arrival = arrival_quote t request.Order.Request.symbol in
        (* Time passed while the quote was fetched: the switch may have
           tripped, the book's last applied read may have aged past the
           window, and the session may have closed (Task 1's carried item).
           A refusal here is journaled with its reason, as any refusal is. *)
        match pre_wire t ~own:0.0 request with
        | Error f ->
            let p = { p with Preview.failures = [ f ] } in
            return (p, refuse t p ~source)
        | Ok _ -> (
            let o = Order.create request in
            admit t
              [
                {
                  Journal.Insert.order = o;
                  source;
                  decision_price = Option.value_exn p.Preview.decision_price;
                  arrival;
                  verdict = Preview.to_json p;
                };
              ];
            match%map submit_journaled t ~own:0.0 o with
            | `Acknowledged o | `Not_acknowledged (o, _) -> (p, o)
            | `Stopped (f, o) -> ({ p with Preview.failures = [ f ] }, o)))

(* ------------------------------------------------------------------------ *)
(* A live strategy's rebalance (Task 15)                                     *)
(* ------------------------------------------------------------------------ *)

(* E, the equity a rebalance is sized on: the book's equity as the gate reads
   it -- a snapshot of the live graph -- and only when that graph is the
   account's book as of a recent read, which is the condition the trading
   rule holds every order to. A held name with no price makes equity a
   number about a book that does not exist, so it is unknown too. *)
let sizing_equity t : (float, string) Result.t =
  if not (t.book_is_current ()) then Error Session_close.not_current
  else
    match
      List.find (Graph.symbols t.graph) ~f:(fun s ->
          (not (Qty.is_zero (Graph.qty t.graph s)))
          && not (Float.( > ) (Price.to_float (Graph.price t.graph s)) 0.0))
    with
    | Some s ->
        Error
          (sprintf "%s is held and has no price, so the book's equity is not known"
             (Symbol.to_string s))
    | None ->
        let e = Notional.to_float (Graph.Snapshot.equity (Graph.snapshot t.graph)) in
        if Float.is_finite e && Float.( > ) e 0.0 then Ok e
        else Error (sprintf "the book's equity reads %g" e)

(* The strategy's OWN position: the net of the desk's fills of orders whose
   source is signal:<slug>:*, from the journal through orders_by_source --
   never the account's, so a lot bought by hand is never the strategy's to
   sell. Known only when nothing of the strategy's may still fill: an open
   order, or a failed one the venue may still work ([failed_still_working]'s
   rule). Sizing on top of such an order would count its shares once as
   resting and again as the target, and buy them twice. *)
let strategy_position t ~(strategy : string) : (float Symbol.Map.t, string) Result.t =
  let prefix = Rebalance.Source.prefix ~strategy in
  match
    Or_error.try_with (fun () ->
        let since =
          Option.map
            (List.hd (Journal.recent_sessions t.journal ~limit:1))
            ~f:(fun s -> s.Journal.Session.date)
        in
        let unsettled =
          Journal.source_orders_unsettled t.journal ~prefix ~since
          |> List.filter ~f:(fun (r : Journal.Order_row.t) ->
              let o = r.Journal.Order_row.order in
              (not (Order.State.equal o.Order.state Order.State.Failed))
              || Float.( > ) (Order.remaining_qty o) Order.epsilon)
        in
        (unsettled, Journal.source_fills t.journal ~prefix))
  with
  | Error e ->
      Error (sprintf "the journal could not be read (%s)" (Error.to_string_hum e))
  | Ok ((_ :: _ as unsettled), _) ->
      Error
        (sprintf
           "%d of its orders may still fill (%s), so its position is not yet known, and \
            nothing is sized on top of them"
           (List.length unsettled)
           (String.concat ~sep:"; "
              (List.map unsettled ~f:(fun r -> describe_resting r.Journal.Order_row.order))))
  | Ok ([], fills) -> Ok (Symbol.Map.of_alist_reduce fills ~f:( +. ))

(* The targets for an accepted signal, as legs, or no targets and why: E from
   [sizing_equity], each price the CURRENT MARK ([fresh_mark]: the feed's
   live price while the feed calls it fresh -- never yesterday's close, which
   sized the market-on-open orders this replaced, Task 1.7; a name with no
   current mark is no targets and says so), and the strategy's own position
   from [strategy_position], at [strategy]'s capital_fraction -- the
   caller's to set to the effective fraction an incubation strategy is sized
   at -- times [scale], which Rebalance.plan refuses outside [0, 1].
   Rebalance.plan decides. Then, with [floor] (the adjust pass's re-sizes),
   every leg worth less than the book's min_leg_notional is dropped
   (Rebalance.drop_small) -- never a leg whose target is 0, which closes a
   name and would otherwise leave a residue the desk could never sell:
   (kept, dropped), so the caller can say what it dropped. A signal's own
   rebalance drops nothing (the stage review's A-I6, withdrawing the floor
   there; kept under day limits). *)
let plan_rebalance ?(floor = true) t
    ~(strategy : Ohcamel.Config.Book.Signals_spec.Strategy.t)
    ~(targets : (Symbol.t * float) list) ~(scale : float) :
    (Rebalance.Leg.t list * Rebalance.Leg.t list, string) Result.t =
  let module S = Ohcamel.Config.Book.Signals_spec.Strategy in
  let symbols = List.map strategy.S.symbols ~f:Symbol.of_string in
  let marks =
    Symbol.Map.of_alist_reduce
      ~f:(fun first _ -> first)
      (List.map symbols ~f:(fun s -> (s, fresh_mark t s)))
  in
  (* The account's holdings as the gate reads them: the graph's book, which
     is the account's while [sizing_equity] answers at all. *)
  let account =
    Symbol.Map.of_alist_reduce
      ~f:(fun first _ -> first)
      (List.filter_map symbols ~f:(fun s ->
           Option.some_if
             (Graph.knows_symbol t.graph s)
             (s, Qty.to_float (Graph.qty t.graph s))))
  in
  Rebalance.plan ~symbols ~weights:targets ~capital_fraction:strategy.S.capital_fraction
    ~scale ~equity:(sizing_equity t) ~account ~marks
    ~current:(strategy_position t ~strategy:strategy.S.name)
  |> Result.map ~f:(fun legs ->
      if floor then
        Rebalance.drop_small ~legs ~min_leg_notional:t.spec.Desk_spec.min_leg_notional
      else (legs, []))

(* Whether a strategy's names all have a current mark ([fresh_mark]), and
   why not: inside the send window a name the book holds that the feed calls
   stale, or has not seen print, is a reason to WAIT (fix round 1, I1) -- a
   thin ETF goes minutes without a print, a name may not have printed since a
   restart -- not to close the day's document; the judgement's own max_age
   bounds the wait. A name the book does not hold at all is no wait: the plan
   refuses it for good. *)
let marks_wait t ~(strategy : Ohcamel.Config.Book.Signals_spec.Strategy.t) : string option
    =
  let module S = Ohcamel.Config.Book.Signals_spec.Strategy in
  List.filter_map strategy.S.symbols ~f:(fun name ->
      let s = Symbol.of_string name in
      if not (Graph.knows_symbol t.graph s) then None
      else match fresh_mark t s with Ok _ -> None | Error why -> Some why)
  |> function
  | [] -> None
  | whys -> Some (String.concat ~sep:"; " whys)

(* Whether the desk may send its own orders now, and why not: the session
   rule for a desk order (Rules.session_refusal: the regular session open,
   and 09:35 New York come). The intake asks it BEFORE it plans anything
   (Intake.rebalance), so a rebalance is never sized off the evening's stale
   marks: a judgement waits, pending, until it may go. *)
let send_window t : string option =
  Rules.session_refusal ~desk:true ~tif:Order.Tif.Day
    ~zone:(Lazy.force Desk_time.new_york)
    ~session:t.session ~now:(t.now ()) ()

(* What a rebalance came to, in the words the intake records beside the
   judgement. [Refused]: nothing was journaled and nothing sent. [Sent]:
   every order was journaled and handed to the wire, and the venue
   acknowledged each. [Stopped]: the switch, the book or the session failed
   between two submits, or the venue did not acknowledge an order -- refused
   it, or left its outcome unknown; what went out went, each in the state the
   venue's answer left it, and every order after it was moved to
   rejected_pre_trade and never sent. When the order the venue did not
   acknowledge was the last, [unsent] is empty: every order went, and the
   sentence says the last was not acknowledged rather than that all were
   sent. [Waved]: a rebalance of more than Rebalance.max_legs
   orders, sent in waves (desk/waves.ml), or one of any size whose wait ran
   out, each wave's own outcome in the order they went; a wave that was not
   [Sent] is the last, and so is one whose wait ran out, [timed_out], after
   which every open order of the rebalance was cancelled. Its first wave is never [Refused]: a rebalance
   whose first wave is refused is [Refused]. [Not_now]: refused as [Refused]
   is, nothing journaled and nothing sent, for a reason of TIMING alone
   (Rules.is_timing_refusal: the session closed, before 09:35, no venue
   clock yet, no fresh quote, a wash) -- the same rebalance may go
   later, and the intake keeps its judgement pending and tries it again. Its
   sentence is a refusal's, word for word. *)
module Rebalance_outcome = struct
  type t =
    | Refused of string
    | Not_now of string
    | Sent of Order.t list
    | Stopped of { sent : Order.t list; unsent : Order.t list; why : string }
    | Waved of { waves : t list; timed_out : bool }

  let describe_order (o : Order.t) =
    let r = o.Order.request in
    sprintf "%s %s %s %d %s" (key o)
      (Symbol.to_string r.Order.Request.symbol)
      (Order.Side.to_string r.Order.Request.side)
      r.Order.Request.qty
      (match o.Order.reason with
      | Some why -> sprintf "(%s: %s)" (Order.State.to_string o.Order.state) why
      | None -> sprintf "(%s)" (Order.State.to_string o.Order.state))

  let orders os = String.concat ~sep:"; " (List.map os ~f:describe_order)

  (* What a rebalance's orders are, by their time in force: one mode's orders
     share one. *)
  let kind os =
    match
      List.dedup_and_sort ~compare:Order.Tif.compare
        (List.map os ~f:(fun (o : Order.t) -> o.Order.request.Order.Request.tif))
    with
    | [ Order.Tif.Opg ] -> "market-on-open"
    | [ Order.Tif.Day ] -> "day limit"
    | [ Order.Tif.Cls ] -> "market-on-close"
    | _ -> "rebalance"

  let rec to_string = function
    | Refused why | Not_now why -> "refused, and nothing journaled or sent: " ^ why
    | Sent os ->
        sprintf "%d %s order%s journaled, then sent, and acknowledged: %s"
          (List.length os) (kind os)
          (if List.length os = 1 then "" else "s")
          (orders os)
    | Waved { waves; timed_out } ->
        let n = List.length waves in
        sprintf "in %d wave%s%s: %s" n
          (if n = 1 then "" else "s")
          (if timed_out then
             sprintf
               ", and wave %d was not filled or cancelled within its wait, so every open \
                order of the rebalance was cancelled and no wave after it was sent"
               n
           else "")
          (String.concat ~sep:" | "
             (List.mapi waves ~f:(fun i w -> sprintf "wave %d: %s" (i + 1) (to_string w))))
    | Stopped { sent; unsent = []; why } ->
        sprintf "every order journaled, then sent, and the last not acknowledged (%s): %s"
          why (orders sent)
    | Stopped { sent; unsent; why } ->
        sprintf
          "stopped between submits (%s): sent %s; journaled and never sent, now \
           rejected_pre_trade: %s"
          why
          (if List.is_empty sent then "none" else orders sent)
          (orders unsent)
end

(* A leg as a refusal names it: its place in the rebalance and its order. *)
let name_leg ~i ~n (r : Order.Request.t) =
  sprintf "order %d of %d (%s %s %d %s)" (i + 1) n
    (Symbol.to_string r.Order.Request.symbol)
    (Order.Side.to_string r.Order.Request.side)
    r.Order.Request.qty
    (match (r.Order.Request.tif, r.Order.Request.kind) with
    | Order.Tif.Opg, _ -> "market-on-open"
    | Order.Tif.Cls, _ -> "market-on-close"
    | Order.Tif.Day, Order.Kind.Limit p -> sprintf "day limit at %.2f" (Price.to_float p)
    | Order.Tif.Day, Order.Kind.Market -> "day market")

module Checked_rebalance = struct
  (* [own]: the strategy's own position the rules judged each order against,
     which [pre_wire] asks again with at the wire. *)
  type t = {
    orders : (Order.Request.t * Price.t) list;
    gate : Scenarios.t;
    own : float Symbol.Map.t;
  }
end

(* Everything a rebalance must pass before anything is written, and nothing
   written: synchronous, so the main suite can ask it. In order:
   - the strategy's fill history must be settled ([strategy_position]);
   - WHAT THE PLAN ASSUMED, ASKED AGAIN HERE. [plan_rebalance] runs outside
     the queue (the intake calls it, then this), so a job that ran between
     the two -- a fill, a read of the account -- could have changed what it
     read. The legs are not re-planned: the rules and every gate below judge
     them against the book as it is now, whatever E they were sized at, and
     re-planning would take the legs out of the caller's hands. What is asked
     again is what makes the legs wrong rather than merely sized at another
     E: that the book's equity is known ([sizing_equity]);
   - THE SUB-LEDGER ([subledger]): in a name whose sub-ledger has a
     STANDING break ([fold_reading]: broken at two checks running) -- until a
     reading judges the name whole, so an order resting in it does not lift
     the break -- no order may grow the strategy's own position
     (Rebalance.grows), and a reduction goes only if it also shrinks the
     account's position by no more than it holds (Rules.reduces with
     [netted] false); each refusal names both figures (Subledger.refuses,
     Subledger.reduction_refusal). Every order in a name that sums goes. One sum per name replaced the
     per-strategy test that the account held the strategy's own position,
     which two strategies on opposite sides of one name could never pass. A
     sub-ledger that cannot be read refuses the rebalance;
   - THE CALENDAR (Rebalance.calendar_refusal): the signal's as_of, the
     closes and the newest recorded session are one date, and no weekday
     has closed since it unrecorded -- for a document with an order of its
     source in the journal (standing), the as_of no later than that date;
   - one order per name: a name twice is refused, since which order shrinks
     and which grows it would depend on which filled first;
   - THE RULES, PER ORDER, the orders that shrink a position first and those
     that grow one last (Rebalance.shrinking_first, on the book the gate
     reads), each a day limit priced off its quote (below), each counting
     the rebalance's earlier orders against the open-order cap;
   - THE GATE, ON EVERY COMBINATION OF THE ORDERS THAT COULD BE THE FILLS
     (Rebalance.fill_sets): every non-empty subset, each one call with
     those orders' fills and the resting orders (every scenario of
     [gate_scenarios]). What reaches the venue is a prefix of the send order
     -- the loop stops at the first order the venue does not acknowledge, or
     when the switch, the book or the session fails between two submits
     ([send_rebalance]) -- and any order that reached it can still go
     unfilled, or be refused after it was acknowledged, so the fills can be
     any subset. A sector's notional is
     the size of a net sum, so an order can take a sector over its line
     without the order that offsets it, whether it shrinks its own name's
     position or grows it. The unit first, then the rest smallest first;
     a fill set met twice is gated once, and a single order's only subset is
     the unit. It passes only if every one passes. Hence at most
     Rebalance.max_legs orders (six: 63 calls), refused before anything
     else with the reason.
   Any failure refuses the whole, naming the order and its rules, or the
   limits and the subset that breached them.

   EVERY ORDER IS A DAY LIMIT OFF THE QUOTE (Task 1.7). [quotes] are the
   quotes [send_rebalance] fetched from the venue as it began; each order's
   limit is Rebalance.quote_limit of its name's -- a buy the book's
   intraday_slip_bps above the ask, a sell as far below the bid -- and it is
   judged by the rules as the desk's own day order (Rules.Origin.Desk): the
   regular session and 09:35 New York, the live mark (stale is timing),
   the quote (Rebalance.quote_refusal: its fetch within quote_max_age_s, its
   stamp within 60 s for an order that grows a position, rule [quote]), the
   tick, the collar at that limit and the order cap at the larger of it and
   the mark; and
   gated at that limit, the price a ticket's limit is gated at. Everything
   else is the same, the max_legs refusal included: a rebalance with more
   orders is split into waves before it gets here ([propose_rebalance]).

   [timing], when given, is set when the refusal is the rules' and every
   refused order failed for timing alone (Rules.is_timing_refusal): the
   rebalance may go later unchanged ([Rebalance_outcome.Not_now]). Every
   other refusal leaves it false. *)
let check_rebalance ?(timing = ref false) t
    ~(quotes : Symbol.t -> Rebalance.Fetched.t option) ~(source : Rebalance.Source.t)
    ~(legs : Rebalance.Leg.t list) : (Checked_rebalance.t, string) Result.t =
  let open Result.Let_syntax in
  (* The book as the gate reads it: a name it does not hold is flat here, and
     the universe rule refuses its order below. *)
  let position = account_position t in
  let legs =
    List.stable_sort legs ~compare:(fun (a : Rebalance.Leg.t) b ->
        Rebalance.shrinking_first ~position
          (a.Rebalance.Leg.side, a.Rebalance.Leg.symbol, a.Rebalance.Leg.qty)
          (b.Rebalance.Leg.side, b.Rebalance.Leg.symbol, b.Rebalance.Leg.qty))
  in
  let n = List.length legs in
  let%bind () = if n = 0 then Error "the rebalance has no order" else Ok () in
  let%bind () =
    if n <= Rebalance.max_legs then Ok ()
    else
      Error
        (sprintf
           "the rebalance has %d orders, and the desk takes at most %d: it gates every \
            combination of a rebalance's orders that could be its fills, %d for %d \
            orders and %d for %d"
           n Rebalance.max_legs
           (Int.pow 2 n - 1)
           n
           (Int.pow 2 Rebalance.max_legs - 1)
           Rebalance.max_legs)
  in
  let%bind () =
    match
      List.find_a_dup legs ~compare:(fun (a : Rebalance.Leg.t) b ->
          Symbol.compare a.Rebalance.Leg.symbol b.Rebalance.Leg.symbol)
    with
    | Some l ->
        Error
          (sprintf "the rebalance names %s twice; it trades each name once"
             (Symbol.to_string l.Rebalance.Leg.symbol))
    | None -> Ok ()
  in
  let%bind own =
    Result.map_error (strategy_position t ~strategy:source.Rebalance.Source.strategy)
      ~f:(fun why -> "the strategy's fill history is unknown: " ^ why)
  in
  let%bind (_ : float) =
    Result.map_error (sizing_equity t) ~f:(fun why ->
        "the book's equity is unknown: " ^ why)
  in
  let%bind () =
    match subledger t with
    | Error why ->
        Error
          (sprintf
             "the sub-ledger could not be read (%s), so no order is judged against it; \
              the rebalance is refused rather than sent without it"
             why)
    | Ok r -> (
        (* A name with a standing break binds until a reading judges it
           whole: a name not judged now -- an order in it may still fill,
           which with several strategies in one ETF is the common case --
           keeps its break, at the figures this reading has or else the
           last ones judged. *)
        let binding symbol =
          match Map.find t.standing_breaks symbol with
          | None -> None
          | Some last -> (
              match
                List.find r.Subledger_reading.breaks ~f:(fun b ->
                    Symbol.equal b.Subledger.symbol symbol)
              with
              | Some now -> Some now
              | None when Set.mem r.Subledger_reading.checked symbol -> None
              | None -> Some last)
        in
        match
          List.find_mapi legs ~f:(fun i (leg : Rebalance.Leg.t) ->
              let symbol = leg.Rebalance.Leg.symbol in
              let side = leg.Rebalance.Leg.side and qty = leg.Rebalance.Leg.qty in
              let held = Option.value (Map.find own symbol) ~default:0.0 in
              Option.bind (binding symbol) ~f:(fun b ->
                  if Rebalance.grows ~position:held ~side ~qty then
                    Subledger.refuses [ b ] ~symbol ~increases:true
                  else
                    (* A reduction of the strategy's own goes only if it also
                       shrinks the account's, by no more than it holds (the
                       1.2 line): with the sum broken, nothing says the
                       account's move is the strategy's, and own +100 against
                       an account of 60 would sell it through zero to a
                       short nobody holds. *)
                    let account = reducible_account t symbol in
                    if Rules.reduces ~own:held ~account ~netted:false ~side ~qty then None
                    else Some (Subledger.reduction_refusal b ~account ~side ~qty))
              |> Option.map ~f:(fun why -> (i, leg, why)))
        with
        | Some (i, leg, why) ->
            Error
              (sprintf "order %d of %d (%s %s %d): %s" (i + 1) n
                 (Symbol.to_string leg.Rebalance.Leg.symbol)
                 (Order.Side.to_string leg.Rebalance.Leg.side)
                 leg.Rebalance.Leg.qty why)
        | None -> Ok ())
  in
  let now = t.now () in
  let%bind () =
    match
      Rebalance.calendar_refusal
        ~zone:(Lazy.force Desk_time.new_york)
        ~now ~as_of:source.Rebalance.Source.as_of
        ~standing:
          (Journal.source_has_orders t.journal
             ~source:(Rebalance.Source.to_string source))
        ~latest:
          (Option.map
             (List.hd (Journal.recent_sessions t.journal ~limit:1))
             ~f:(fun s -> s.Journal.Session.date))
        ~closes:
          (List.filter_map legs ~f:(fun (l : Rebalance.Leg.t) ->
               Option.map (recorded_close t l.Rebalance.Leg.symbol) ~f:fst))
    with
    | None -> Ok ()
    | Some why -> Error why
  in
  let max_age_s = t.spec.Desk_spec.quote_max_age_s in
  let judged =
    List.mapi legs ~f:(fun i (leg : Rebalance.Leg.t) ->
        let quote = quotes leg.Rebalance.Leg.symbol in
        let ctx =
          context ~origin:(Rules.Origin.Desk quote)
            ~own:(Option.value (Map.find own leg.Rebalance.Leg.symbol) ~default:0.0)
            t
            ~source:(Rebalance.Source.to_string source)
            ~symbol:leg.Rebalance.Leg.symbol
        in
        let ctx =
          { ctx with Rules.Context.open_orders = ctx.Rules.Context.open_orders + i }
        in
        (* The order's limit, and the price it is gated at: off a quote that
           can price it, else none -- the quote rule then says why. *)
        let kind, price =
          match quote with
          | Some { Rebalance.Fetched.quote = q; _ }
            when Option.is_none
                   (Rebalance.quote_refusal
                      ~shrinks:
                        (Rules.shrinks_position ~position:ctx.Rules.Context.own
                           ~side:leg.Rebalance.Leg.side ~qty:leg.Rebalance.Leg.qty)
                      ~now ~max_age_s quote) ->
              let limit =
                Price.of_float
                  (Rebalance.quote_limit ~side:leg.Rebalance.Leg.side
                     ~bid:(Price.to_float q.Venue.Quote.bid)
                     ~ask:(Price.to_float q.Venue.Quote.ask)
                     ~band_bps:t.spec.Desk_spec.intraday_slip_bps)
              in
              (Order.Kind.Limit limit, Some limit)
          | Some _ | None -> (Order.Kind.Market, None)
        in
        let request =
          {
            Order.Request.client_order_id = Ids.Client_order_id.generate ~now ~rng:t.rng;
            symbol = leg.Rebalance.Leg.symbol;
            side = leg.Rebalance.Leg.side;
            qty = leg.Rebalance.Leg.qty;
            kind;
            tif = Order.Tif.Day;
          }
        in
        (i, request, price, Rules.check ctx request))
  in
  let%bind priced =
    match
      List.filter judged ~f:(fun (_, _, _, failures) -> not (List.is_empty failures))
    with
    | _ :: _ as failed ->
        timing :=
          List.for_all failed ~f:(fun (_, _, _, failures) ->
              Rules.is_timing_refusal ~desk:true failures);
        Error
          (String.concat ~sep:"; "
             (List.map failed ~f:(fun (i, r, _, failures) ->
                  sprintf "%s: %s" (name_leg ~i ~n r)
                    (String.concat ~sep:"; " (List.map failures ~f:reason)))))
    | [] ->
        List.map judged ~f:(fun (i, r, price, _) ->
            match price with
            | Some p -> Ok (r, p)
            | None -> Error (sprintf "%s has no quote to price it" (name_leg ~i ~n r)))
        |> Result.all
  in
  let named orders =
    String.concat ~sep:"; "
      (List.map orders ~f:(fun (i, (r, p)) ->
           sprintf "%s at %.2f" (name_leg ~i ~n r) (Price.to_float p)))
  in
  let by_place = Array.of_list priced in
  (* A subset as the refusal names it: the unit, or which orders alone
     fill, the others refused, never sent or left unfilled. *)
  let describe ids =
    let k = List.length ids in
    if k = n then "as a unit"
    else
      let places = List.map ids ~f:(fun i -> Int.to_string (i + 1)) in
      let listed =
        match List.rev places with
        | [] | [ _ ] -> String.concat places
        | last :: rest -> String.concat ~sep:", " (List.rev rest) ^ " and " ^ last
      in
      sprintf
        "if only %s %s of %d fill%s, as when the other%s %s refused, never sent or left \
         unfilled"
        (if k = 1 then "order" else "orders")
        listed n
        (if k = 1 then "s" else "")
        (if n - k = 1 then "" else "s")
        (if n - k = 1 then "is" else "are")
  in
  let gate ids =
    let orders = List.map ids ~f:(fun i -> (i, by_place.(i))) in
    match gate_orders t (List.map orders ~f:snd) with
    | Error why -> Error why
    | Ok s when Scenarios.passed s -> Ok s
    | Ok s ->
        Error
          (sprintf "the gate refuses the rebalance %s (%s): %s" (describe ids)
             (named orders)
             (String.concat ~sep:"; " (Scenarios.reasons s)))
  in
  (* A subset's fills, as the gate is handed them: two subsets with the same
     fills are one gate call. The names are distinct, so today no two are. *)
  let fills ids =
    List.map ids ~f:(fun i ->
        let r, p = by_place.(i) in
        sprintf "%s %s %d %.17g"
          (Symbol.to_string r.Order.Request.symbol)
          (Order.Side.to_string r.Order.Request.side)
          r.Order.Request.qty (Price.to_float p))
    |> List.sort ~compare:String.compare
  in
  match Rebalance.fill_sets n with
  | [] -> Error "the rebalance has no order"
  | unit :: subsets ->
      let%bind whole = gate unit in
      let%map (_ : string list list) =
        List.fold_result subsets
          ~init:[ fills unit ]
          ~f:(fun seen ids ->
            let these = fills ids in
            if List.mem seen these ~equal:[%equal: string list] then Ok seen
            else Result.map (gate ids) ~f:(fun (_ : Scenarios.t) -> these :: seen))
      in
      { Checked_rebalance.orders = priced; gate = whole; own }

(* A live strategy's rebalance, through the machinery a ticket goes through,
   in ONE sequencer job: the send window first ([send_window]: the session
   open and 09:35 come -- before it nothing is fetched, and the rebalance is
   [Not_now]); the venue's quote for every name, fetched together, each
   bounded at two seconds ([fetch_quote]); [check_rebalance] on them (at
   most six orders; what the plan read, asked again; each order's limit off
   its quote and the rules per order; the gate on every combination of the
   orders that could be the fills); [pre_wire] again for every order,
   because the quotes took time; then every order journaled in one
   transaction ([admit]), its quote as its arrival, before any is sent; then
   each handed to [submit_journaled], which asks [pre_wire] once more at its
   own instant -- the quote's age among it, and a kill sets the switch
   outside the sequencer, so it can land between two submits. When that fails, the
   order it was asked for and every order after it move from pending_submit
   to rejected_pre_trade with the reason, and none of them is sent.

   All or nothing up to the journal: a refusal before it journals nothing and
   sends nothing. After it, what the venue has taken it has; an order already
   acknowledged when the switch is set is cancelled by the switch's own
   machinery ([record], [cancel_all]), as any open order is. *)
let send_rebalance t ~(source : Rebalance.Source.t) ~(legs : Rebalance.Leg.t list) :
    Rebalance_outcome.t Deferred.t =
  enqueue t (fun () ->
      match send_window t with
      | Some why -> return (Rebalance_outcome.Not_now ("session: " ^ why))
      | None -> (
          let symbols =
            List.dedup_and_sort ~compare:Symbol.compare
              (List.map legs ~f:(fun (l : Rebalance.Leg.t) -> l.Rebalance.Leg.symbol))
          in
          let%bind fetched =
            Deferred.List.map ~how:`Parallel symbols ~f:(fun s ->
                Deferred.map (fetch_quote t s) ~f:(fun q ->
                    ( s,
                      Option.map q ~f:(fun quote ->
                          { Rebalance.Fetched.quote; at = t.now () }) )))
          in
          let quote_of symbol =
            Option.join (List.Assoc.find fetched symbol ~equal:Symbol.equal)
          in
          let timing = ref false in
          match check_rebalance ~timing t ~quotes:quote_of ~source ~legs with
          | Error why when !timing -> return (Rebalance_outcome.Not_now why)
          | Error why -> return (Rebalance_outcome.Refused why)
          | Ok { Checked_rebalance.orders; gate; own } -> (
              let n = List.length orders in
              let own_of (r : Order.Request.t) =
                Option.value (Map.find own r.Order.Request.symbol) ~default:0.0
              in
              let origin_of (r : Order.Request.t) =
                Rules.Origin.Desk (quote_of r.Order.Request.symbol)
              in
              let arrivals =
                List.map orders ~f:(fun ((r : Order.Request.t), _) ->
                    Option.map (quote_of r.Order.Request.symbol)
                      ~f:(fun { Rebalance.Fetched.quote = q; _ } ->
                        (q.Venue.Quote.bid, q.Venue.Quote.ask)))
              in
              match
                List.find_mapi orders ~f:(fun i (r, _) ->
                    match pre_wire ~origin:(origin_of r) t ~own:(own_of r) r with
                    | Ok _ -> None
                    | Error f -> Some (i, r, f))
              with
              | Some (i, r, f) ->
                  let why =
                    sprintf "after the quotes, %s: %s" (name_leg ~i ~n r) (reason f)
                  in
                  return
                    (if Rules.is_timing_refusal ~desk:true [ f ] then
                       Rebalance_outcome.Not_now why
                     else Rebalance_outcome.Refused why)
              | None ->
                  let created = List.map orders ~f:(fun (r, _) -> Order.create r) in
                  admit t
                    (List.map3_exn created orders arrivals ~f:(fun o (r, price) arrival ->
                         {
                           Journal.Insert.order = o;
                           source = Rebalance.Source.to_string source;
                           decision_price = price;
                           arrival;
                           verdict =
                             Preview.to_json
                               {
                                 Preview.request = r;
                                 failures = [];
                                 gate = Some (Ok gate);
                                 decision_price = Some price;
                               };
                         }));
                  (* On only after an acknowledgement. An order the venue refused,
                 or left unknown, stops the ones after it: they were gated with
                 it, and without it they are a different trade -- a buy whose
                 sell never happened. Each is moved to rejected_pre_trade,
                 naming the order that stopped it. *)
                  let stop_after ~i o ~sent ~rest why =
                    let because =
                      sprintf "rebalance: %s was not acknowledged (%s)%s"
                        (name_leg ~i ~n o.Order.request)
                        why
                        (if List.is_empty rest then ""
                         else ", so no order after it is sent")
                    in
                    match rest with
                    | [] ->
                        Rebalance_outcome.Stopped
                          { sent = List.rev (o :: sent); unsent = []; why = because }
                    | _ :: _ ->
                        Rebalance_outcome.Stopped
                          {
                            sent = List.rev (o :: sent);
                            unsent =
                              List.map rest ~f:(fun o ->
                                  record t o (Order.Event.Pre_trade_rejected [ because ]));
                            why = because;
                          }
                  in
                  let rec go i sent = function
                    | [] -> return (Rebalance_outcome.Sent (List.rev sent))
                    | o :: rest -> (
                        match%bind
                          submit_journaled ~origin:(origin_of o.Order.request) t
                            ~own:(own_of o.Order.request) o
                        with
                        | `Acknowledged o -> go (i + 1) (o :: sent) rest
                        | `Not_acknowledged (o, why) ->
                            return (stop_after ~i o ~sent ~rest why)
                        | `Stopped (f, o) ->
                            let unsent =
                              o
                              :: List.map rest ~f:(fun o ->
                                  record t o (Order.Event.Pre_trade_rejected [ reason f ]))
                            in
                            return
                              (Rebalance_outcome.Stopped
                                 { sent = List.rev sent; unsent; why = reason f }))
                  in
                  go 0 [] created)))

(* Whether every order of a rebalance has finished -- filled, cancelled,
   expired or refused -- waiting at most [timeout]: the journal's open orders
   whose source is [source]'s, read now and every two seconds after on the
   order manager's own clock, [time_source]. OUTSIDE THE SEQUENCER: the fills
   it waits for are journaled by [on_update], a sequencer job, so a wait
   inside one would never see one. A journal read is one synchronous call, so
   it cannot interleave with a job. *)
let await_settled t ~(source : Rebalance.Source.t) ~(timeout : Time_ns.Span.t) :
    [ `Settled | `Timed_out ] Deferred.t =
  let name = Rebalance.Source.to_string source in
  let every = Time_ns.Span.of_sec 2.0 in
  let deadline = Time_ns.add (Time_source.now t.time_source) timeout in
  let resting () =
    List.exists (Journal.open_orders t.journal) ~f:(fun (r : Journal.Order_row.t) ->
        String.equal r.Journal.Order_row.source name)
  in
  let rec poll () =
    if not (resting ()) then return `Settled
    else
      let now = Time_source.now t.time_source in
      if Time_ns.( >= ) now deadline then return `Timed_out
      else
        let%bind () =
          Time_source.after t.time_source
            (Time_ns.Span.min every (Time_ns.diff deadline now))
        in
        poll ()
  in
  poll ()

(* Every open order of a rebalance, cancelled through [cancel] -- journaled
   pending_cancel before the DELETE, cancelled when the venue says so -- one
   at a time. A cancel that is not sent is said in the log: the order stays
   open, and the next pass finds the strategy's position unknown until it
   finishes ([strategy_position]). *)
let cancel_rebalance t ~(source : Rebalance.Source.t) : unit Deferred.t =
  let name = Rebalance.Source.to_string source in
  Journal.open_orders t.journal
  |> List.filter ~f:(fun (r : Journal.Order_row.t) ->
      String.equal r.Journal.Order_row.source name)
  |> Deferred.List.iter ~how:`Sequential ~f:(fun (r : Journal.Order_row.t) ->
      let client =
        r.Journal.Order_row.order.Order.request.Order.Request.client_order_id
      in
      match%map cancel t client with
      | Ok _ -> ()
      | Error why ->
          t.on_event
            (sprintf
               "desk      %s: the wave's wait ran out, and its cancel was not sent: %s"
               (Ids.Client_order_id.to_string client)
               why))

(* A strategy's rebalance: day limits off the quote (Task 1.7), in waves,
   EVERY ONE OF THEM WAITED ON. At most Rebalance.max_legs orders are one
   wave; more are split (Waves.split: the orders that shrink a position
   first, then the growing ones, each group largest first, six at a time).
   Each wave is one [send_rebalance], checked and gated on the book as it is
   when its turn comes -- after the waves before it have filled -- then
   waited on ([await_settled], at most the book's wave_timeout_s): an
   unfilled order is cancelled at its timeout and the strategy re-sized on a
   later pass (design §4.3; the adjust pass's completion, Intake.adjust).
   One the session's close reaches first expires at the venue, which the
   desk hears like a cancel. EACH WAVE IS ITS OWN SEQUENCER JOB, and the wait
   is between them, not inside one: the fills that settle a wave are
   journaled and applied to the book by sequencer jobs, and a wait that held
   the sequencer would see none of them -- nor let a kill's cancels through.
   What may run between two waves is any other job; none can size this
   strategy again, because [strategy_position] refuses while one of its
   orders may fill, and the next wave's check asks it again.

   A [Stopped] wave is waited on as a [Sent] one is -- what the venue
   acknowledged rests there like any other order of the wave -- and ends the
   rebalance; so does a refused wave. When a wait runs out, every open order
   of the rebalance is cancelled ([cancel_rebalance]) and no later wave is
   sent. The outcome: a first wave refused is [Refused] (or [Not_now]),
   nothing journaled; a rebalance of one wave that settled inside its wait
   is that wave's own outcome ([Sent], or [Stopped]); anything else -- more
   than one wave, or a wait that ran out -- is [Waved]. *)
let propose_rebalance t ~(source : Rebalance.Source.t) ~(legs : Rebalance.Leg.t list) :
    Rebalance_outcome.t Deferred.t =
  let position symbol =
    if Graph.knows_symbol t.graph symbol then Qty.to_float (Graph.qty t.graph symbol)
    else 0.0
  in
  let waves =
    if List.length legs <= Rebalance.max_legs then [ legs ]
    else Waves.split ~legs ~position ~max_legs:Rebalance.max_legs
  in
  let finish sent ~timed_out =
    match (List.rev sent, timed_out) with
    | [ only ], false when List.length waves = 1 -> only
    | waves, timed_out -> Rebalance_outcome.Waved { waves; timed_out }
  in
  let timeout = Time_ns.Span.of_sec t.spec.Desk_spec.wave_timeout_s in
  let rec go sent = function
    | [] -> return (finish sent ~timed_out:false)
    | wave :: rest -> (
        match%bind send_rebalance t ~source ~legs:wave with
        | (Rebalance_outcome.Refused _ | Rebalance_outcome.Not_now _) as refused
          when List.is_empty sent ->
            return refused
        | ( Rebalance_outcome.Refused _ | Rebalance_outcome.Not_now _
          | Rebalance_outcome.Waved _ ) as last ->
            return (finish (last :: sent) ~timed_out:false)
        | (Rebalance_outcome.Sent _ | Rebalance_outcome.Stopped _) as this -> (
            match%bind await_settled t ~source ~timeout with
            | `Settled -> (
                match this with
                | Rebalance_outcome.Sent _ -> go (this :: sent) rest
                | _ -> return (finish (this :: sent) ~timed_out:false))
            | `Timed_out ->
                let%map () = cancel_rebalance t ~source in
                finish (this :: sent) ~timed_out:true))
  in
  go [] waves

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
   again the next time the venue reports the order resting, and not before.

   The request is journaled before it is sent, as [cancel] journals its own:
   a cancel_requested event on the failed order, which stays failed
   (Order.apply), so the record shows the desk asked before the venue's
   answer to it arrives -- and that it asked, if none ever does. *)
let cancel_failed t (trade : Wire.permit Venue.Trade.t) (o : Order.t)
    (v : Venue.Venue_order.t) : unit Deferred.t =
  let id = v.Venue.Venue_order.id in
  let client = key o in
  if Hash_set.mem t.failed_cancels id then Deferred.unit
  else (
    Hash_set.add t.failed_cancels id;
    t.on_event
      (sprintf
         "desk      the venue reports %s %s under id %s, which this desk declared failed \
          when its lookups found nothing; nothing manages it, so the desk is cancelling \
          it by that id"
         client v.Venue.Venue_order.status id);
    ignore (record t o Order.Event.Cancel_requested : Order.t);
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
          let o =
            match (u.Venue.Update.event, Venue.Update.to_event u) with
            | ("fill" | "partial_fill"), _ -> o
            | _, Some e -> record t o e
            | _, None ->
                t.on_event
                  (sprintf "desk      %s: %s changes nothing the order's states describe"
                     raw u.Venue.Update.event);
                o
          in
          match t.trade with
          | Ok trade when failed && resting ->
              cancel_failed t trade o u.Venue.Update.order
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
      ("time_in_force", `String (Order.Tif.to_string q.Order.Request.tif));
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

(* The failed orders the venue still works, found in the venue's own list of
   its open orders: the journal's open set cannot name them, failed being
   terminal. Each gets the venue's id, if it had none, and [cancel_failed].
   [reconcile] runs it, and so does [cancel_all] -- a kill, a trip and the
   engine's stop -- each inside the sequencer. *)
let reconcile_failed t (trade : Wire.permit Venue.Trade.t) : unit Deferred.t =
  match%bind trade.Venue.Trade.open_orders () with
  | Error e ->
      t.on_event
        (sprintf
           "desk      the venue's open orders could not be read, so no order this desk \
            declared failed was looked for there: %s"
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
              let o =
                if Option.is_none o.Order.venue_order_id then
                  record t o (Order.Event.Found v.Venue.Venue_order.id)
                else o
              in
              cancel_failed t trade o v
          | Some _ | None -> Deferred.unit)

(* Every order that may still fill and that the desk can cancel. First every
   open order the venue has an id for; one without an id yet is cancelled by
   [record] the moment the venue gives it one. That set is [t.open_], which
   is the journal's open orders held -- [reconcile] re-seeds it from
   [Journal.open_orders] -- and not the venue's [open_orders], which is a
   different set: the desk's own orders as the VENUE has them, which cannot
   include one the desk journaled and never managed to send.

   Then the orders this desk declared failed that the venue still works
   ([reconcile_failed]): out of the open set, so no cancel above reaches
   them, and a market-on-open order the venue holds rests there for some
   fourteen hours until the auction. Without this a kill, a trip or the
   engine's stop would leave them working until the next reconciliation.
   Read from the venue's own list, so a venue that cannot list its open
   orders cancels none of them, and says so. They are looked for even when
   a cancel above raised, and the raise still reaches the caller after. *)
let cancel_all t : unit Deferred.t =
  Monitor.protect
    (fun () ->
      Deferred.List.iter ~how:`Sequential (Map.data t.open_) ~f:(fun o ->
          match o.Order.venue_order_id with
          | None -> Deferred.unit
          | Some _ ->
              Deferred.ignore_m (cancel t o.Order.request.Order.Request.client_order_id)))
    ~finally:(fun () ->
      enqueue t (fun () ->
          match t.trade with
          | Error _ -> Deferred.unit
          | Ok trade -> reconcile_failed t trade))

(* A kill's first half, synchronous: the halt, its line in the log, and the
   page told. The halt comes first, so anything that raises after it raises
   with the switch already set -- which is what lets the kill route answer
   the person who pressed it, and still send the cancels. *)
let halt_by_hand t ~why =
  let at = t.now () in
  Halt.halt t.halt ~why ~at;
  (* The first press only, as the switch keeps it: its reason is the one the
     reset has to answer. *)
  if Option.is_none (Journal.recorded_halt t.journal) then
    Journal.record_halt t.journal ~why ~at;
  t.on_event ("desk      HALTED by hand: " ^ why);
  t.on_change ()

(* The deliberate reset. The journal first, in one transaction that lifts the
   halt by hand and the standing trip together: if it fails the switch stays as
   it was and the route says so, where the other order would lift the switch
   here and bring it back at the next start. Nothing while the engine has stopped
   the desk, which [Halt.reset] does not lift and the route refuses. *)
let reset_switch t =
  match Halt.state t.halt with
  | Halt.State.Stopped _ -> ()
  | Halt.State.Clear | Halt.State.Tripped _ | Halt.State.Halted _ ->
      Or_error.ok_exn (Journal.record_reset t.journal ~at:(t.now ()));
      Halt.reset t.halt

(* A new switch at startup takes up what the last process left standing: the
   halt by hand, and the limit's trip no reset has lifted. The trip matters
   most inside the hysteresis band, where the new process's fresh tracker
   reads the limit as quiet and its kernel switch comes back armed. *)
let restore_switch ~(halt : Halt.t) ~(journal : Journal.t) =
  Option.iter (Journal.recorded_halt journal) ~f:(fun (why, at) ->
      Halt.halt halt ~why ~at);
  Option.iter (Journal.standing_trip journal) ~f:(fun (limit, at) ->
      Halt.restore_trip halt ~limit ~at)

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
   every open order, and every failed one the venue still works, is asked to
   be cancelled ([cancel_all]), as a trip and a kill ask. The
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
           has an id for, and every order it declared failed that the venue lists as \
           working"
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
   it. The stop comes before the line that says the stream ended: a log that
   raises must not keep the switch from being set. A manager with no trading
   half has no stream to lose, and returns at once without stopping
   anything. *)
let run t : unit Deferred.t =
  match t.trade with
  | Error _ -> Deferred.unit
  | Ok trade ->
      let%map () = Pipe.iter trade.Venue.Trade.updates ~f:(on_update t) in
      stop t
        ~why:
          "the venue's order updates stopped, so no fill would be heard; restart the \
           engine to reconnect and reconcile";
      t.on_event
        (sprintf
           "desk      the venue's stream of order updates has ended; %d order(s) are \
            open and nothing on this connection will report them again"
           (Map.length t.open_))

(* A limit's trip, journaled so a restart takes it up ([restore_switch]). The
   write is a few rows in SQLite and touches no graph, so it is made at once,
   leaving no window in which a crash forgets a trip that happened; it returns
   an error rather than raising, and [report] is how the failure is said. *)
let journal_trip journal ~limit ~at ~(report : string -> unit) =
  match Journal.record_trip journal ~limit ~at with
  | Ok () -> ()
  | Error e ->
      report
        (sprintf
           "desk      the trip on %s could NOT be journaled (%s): the switch is tripped \
            now, but a restart inside the limit's hysteresis band would come back clear"
           limit (Error.to_string_hum e))

(* The journal's own ear on the kernel's switch, with no manager behind it:
   bin/main.ml registers it as soon as the alerts and the journal both exist,
   between the alerts' hold and their release at the first sync -- a release
   that can trip the switch twenty seconds and a reconciliation before
   [watch_alerts] is wired. It writes to SQLite and nothing else, and its
   failure's line is scheduled, as the handler runs inside stabilization. The
   handler [watch_alerts] adds writes the same trip again, which changes
   nothing: the standing trip is one row pair, replaced. *)
let journal_trips ~(journal : Journal.t) ~(on_event : string -> unit)
    (alerts : Ohcamel.Alerts.t) =
  Ohcamel.Alerts.on_trip alerts ~f:(fun event ->
      journal_trip journal ~limit:event.Ohcamel.Alerts.Event.limit_name
        ~at:event.Ohcamel.Alerts.Event.at ~report:(fun line ->
          upon Deferred.unit (fun () -> on_event line)))

(* The other half of §3.8. The switch already refuses new orders through
   [Halt.reason]; a trip must also cancel the open ones. The handler runs
   inside stabilization, so beyond the journal's write it only schedules --
   the failure's line too. A trip that came before the wiring is journaled
   here as the handler would have: on the live host [journal_trips] has
   already written it, and writing it again changes nothing; a caller with no
   such hook -- the demo -- is covered all the same. *)
let watch_alerts t (alerts : Ohcamel.Alerts.t) =
  (match Ohcamel.Alerts.Kill_switch.state (Ohcamel.Alerts.kill_switch alerts) with
  | Ohcamel.Alerts.Kill_switch.Tripped { by; at } ->
      journal_trip t.journal ~limit:by ~at ~report:t.on_event
  | Ohcamel.Alerts.Kill_switch.Armed | Ohcamel.Alerts.Kill_switch.Disarmed -> ());
  Ohcamel.Alerts.on_trip alerts ~f:(fun event ->
      let limit = event.Ohcamel.Alerts.Event.limit_name in
      journal_trip t.journal ~limit ~at:event.Ohcamel.Alerts.Event.at ~report:(fun line ->
          upon Deferred.unit (fun () -> t.on_event line));
      upon Deferred.unit (fun () ->
          t.on_event
            (sprintf
               "desk      the kill switch tripped on %s: cancelling every open order"
               limit);
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
let resend_cancel t (trade : Wire.permit Venue.Trade.t) (o : Order.t)
    (v : Venue.Venue_order.t) : unit Deferred.t =
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

(* The incubation tier sizes on a paper venue only (the charter's paper
   tier, design §4.3): Alpaca's paper account (desk/alpaca_paper.ml) or the
   demo's simulator (desk/sim_venue.ml), by the name the read half gives.
   Any other venue -- one this desk does not know to be paper -- refuses. *)
let incubation_permitted t : (unit, string) Result.t =
  match venue_name t with
  | "alpaca-paper" | "simulated" -> Ok ()
  | name -> Error (sprintf "incubation sizing is refused on venue %s" name)

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
             (* A market-on-open order's arrival quote is read when it is
                proposed, after 19:00 ET, from a thin after-hours book: its
                delay and slippage are noisy, and say so. *)
             ( "arrival_after_hours",
               `Bool (Order.Tif.equal r.Journal.Fill_row.tif Order.Tif.Opg) );
           ]))

(* Design §7: say whose fills these are. On the paper account a cost measures
   Alpaca's simulator against one venue's quote; on the demo it is the
   simulated half-spread, by construction. And when any of them is a
   market-on-open order's, say that its arrival quote was an after-hours one:
   read at 19:15 ET from a thin book, so the delay and slippage it gives are
   noise more than cost. *)
let tca_note t (rows : Journal.Fill_row.t list) =
  let venue =
    match venue_name t with
    | "simulated" ->
        "The demo's venue fills every order half a spread from the mark, so these costs \
         are that half-spread, by construction."
    | _ ->
        "On the paper account every cost here measures Alpaca's fill simulator, against \
         IEX's quote -- one venue's, not the national best."
  in
  if
    List.exists rows ~f:(fun (r : Journal.Fill_row.t) ->
        Order.Tif.equal r.Journal.Fill_row.tif Order.Tif.Opg)
  then
    venue
    ^ " A market-on-open fill's arrival quote was read after hours, when its order was \
       proposed after 19:00 ET, from a thin book: its delay and slippage are noisy, and \
       each such fill is marked arrival_after_hours."
  else venue

let tca_json t : Yojson.Safe.t =
  let fills = Journal.recent_fills t.journal ~limit:500 in
  let rows = costs t fills in
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
      ("note", `String (tca_note t fills));
      ("overall", summary (Tca.summarize rows));
      ( "by_symbol",
        `Assoc
          (List.map (Tca.by_symbol rows) ~f:(fun (s, x) ->
               (Symbol.to_string s, summary x))) );
    ]

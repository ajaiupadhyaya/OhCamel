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

let missing_fill (o : Order.t) (v : Venue.Venue_order.t) ~(at : Time_ns.t) :
    Order.Event.t option =
  let missing = v.Venue.Venue_order.filled_qty -. o.Order.filled_qty in
  match v.Venue.Venue_order.filled_avg_price with
  | Some avg when Float.( > ) missing Order.epsilon ->
      let price =
        ((v.Venue.Venue_order.filled_qty *. avg) -. o.Order.filled_notional) /. missing
      in
      Some
        (Order.Event.Venue_fill
           {
             Order.Fill.execution_id =
               sprintf "%s%s:%g" reconciled_prefix v.Venue.Venue_order.id
                 v.Venue.Venue_order.filled_qty;
             qty = missing;
             price = Price.of_float price;
             at;
             position_qty = None;
           })
  | _ -> None

let events_for (o : Order.t) (venue : Venue.Venue_order.t option) ~(at : Time_ns.t) :
    Order.Event.t list =
  (* A pending order first becomes what it was, an outcome nobody learned. A
     lookup that misses must leave an unknown for the scheduled lookups to
     decide, not a pending order nobody will look up again. *)
  let stopped =
    match o.Order.state with
    | Order.State.Pending_submit ->
        [ Order.Event.Outcome_unknown "the process stopped before the venue answered" ]
    | _ -> []
  in
  match venue with
  | None -> stopped
  | Some v ->
      (* The venue's id, whenever the journal's order has none -- not only while
         it is unacknowledged. A fill the stream delivered before the POST's
         answer was read moved the order on without one (Task 1). *)
      let found =
        stopped
        @
        if Option.is_none o.Order.venue_order_id then
          [ Order.Event.Found v.Venue.Venue_order.id ]
        else []
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
      && ((not
             (Set.exists o.Order.execution_ids
                ~f:(String.is_prefix ~prefix:reconciled_prefix)))
         || Float.( > ) u.Venue.Update.order.Venue.Venue_order.filled_qty
              (o.Order.filled_qty +. Order.epsilon))

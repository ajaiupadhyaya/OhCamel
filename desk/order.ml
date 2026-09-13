(* An order's life, as a pure function of the events that happen to it.

   Nothing here does IO, reads a clock or knows a venue exists. The order
   manager feeds it events -- its own, the venue's HTTP answers, the venue's
   trade updates, reconciliation's findings -- and journals what comes back.
   A pure machine is what lets every transition be tested by writing the
   event down, and what lets a restart rebuild an order by replaying its
   journaled events through the same function.

   Two rules from the design shape it (§2, invariants 10 and 11).

   AN UNKNOWN OUTCOME IS A STATE. A submission that timed out may or may not
   have reached the venue, and the machine will not guess: Submit_unknown is
   left only through the venue's answer to "do you have this client order id"
   (Found, Not_found) or through a venue event that settles it. No event means
   "try again".

   FILLS ARE FACTS. Money moved whether or not the machine thinks it should
   have, so a fill is never refused. A fill in a terminal state still adds to
   the filled quantity and comes back with an anomaly for the journal to
   record beside it. An order that says it was cancelled while shares arrived
   is a thing to put in front of a person, not a thing to hide by dropping
   the shares. *)

open Core
open Ohcamel.Types

(* Core deprecated [Time_ns.sexp_of_t] to a value that does not typecheck
   (see lib/types.ml's [Time] module for the same fix): [Alternate_sexp] is
   the same [t] with a real [sexp_of_t], which is all [Fill.t] below derives
   against. Shadowed locally rather than switching the field to
   [Ohcamel.Types.Time.t], because the brief and the venue both speak of
   fills at a [Time_ns.t] and that is the type this module's callers expect. *)
module Time_ns = Time_ns.Alternate_sexp

module Side = struct
  type t = Buy | Sell [@@deriving sexp, compare, equal]

  let to_string = function Buy -> "buy" | Sell -> "sell"
  let of_string = function "buy" -> Some Buy | "sell" -> Some Sell | _ -> None

  (* +1 buys, -1 sells: the sign a fill carries into a position and the sign
     every cost formula multiplies by, written once. *)
  let sign = function Buy -> 1.0 | Sell -> -1.0
end

module Kind = struct
  type t = Market | Limit of Price.t [@@deriving sexp_of, compare, equal]

  let to_string = function Market -> "market" | Limit _ -> "limit"
  let limit_price = function Market -> None | Limit p -> Some p
end

module State = struct
  type t =
    | Rejected_pre_trade
    | Pending_submit
    | Submit_unknown
    | Submitted
    | Accepted
    | Partially_filled
    | Filled
    | Pending_cancel
    | Cancelled
    | Expired
    | Rejected_by_venue
    | Failed
  [@@deriving sexp, compare, equal, enumerate]

  let to_string = function
    | Rejected_pre_trade -> "rejected_pre_trade"
    | Pending_submit -> "pending_submit"
    | Submit_unknown -> "submit_unknown"
    | Submitted -> "submitted"
    | Accepted -> "accepted"
    | Partially_filled -> "partially_filled"
    | Filled -> "filled"
    | Pending_cancel -> "pending_cancel"
    | Cancelled -> "cancelled"
    | Expired -> "expired"
    | Rejected_by_venue -> "rejected_by_venue"
    | Failed -> "failed"

  let of_string s = List.find all ~f:(fun t -> String.equal (to_string t) s)

  let is_terminal = function
    | Rejected_pre_trade | Filled | Cancelled | Expired | Rejected_by_venue | Failed ->
        true
    | Pending_submit | Submit_unknown | Submitted | Accepted | Partially_filled
    | Pending_cancel ->
        false
end

module Request = struct
  (* Whole shares, day orders, regular hours (spec §3.6): an int makes a
     fractional quantity unrepresentable rather than merely rejected. *)
  type t = {
    client_order_id : Ids.Client_order_id.t;
    symbol : Symbol.t;
    side : Side.t;
    qty : int;
    kind : Kind.t;
  }
  [@@deriving sexp_of, compare, equal]
end

module Fill = struct
  (* [qty] is the shares in THIS execution, unsigned: the order's side says
     which way they went. [position_qty] is the venue's own statement of the
     position afterwards, when it gives one -- the number a position is SET
     to, so a replayed event cannot double it. *)
  type t = {
    execution_id : string;
    qty : float;
    price : Price.t;
    at : Time_ns.t;
    position_qty : float option;
  }
  [@@deriving sexp_of, compare, equal]
end

module Event = struct
  type t =
    | Pre_trade_rejected of string list
    | Acknowledged of string
    | Venue_rejected_submission of string
    | Outcome_unknown of string
    | Found of string
    | Not_found
    | Venue_accepted
    | Venue_fill of Fill.t
    | Cancel_requested
    | Venue_cancelled
    | Venue_expired
    | Venue_rejected of string
  [@@deriving sexp_of, compare, equal]

  let name = function
    | Pre_trade_rejected _ -> "pre_trade_rejected"
    | Acknowledged _ -> "acknowledged"
    | Venue_rejected_submission _ -> "venue_rejected_submission"
    | Outcome_unknown _ -> "outcome_unknown"
    | Found _ -> "found"
    | Not_found -> "not_found"
    | Venue_accepted -> "venue_accepted"
    | Venue_fill _ -> "venue_fill"
    | Cancel_requested -> "cancel_requested"
    | Venue_cancelled -> "venue_cancelled"
    | Venue_expired -> "venue_expired"
    | Venue_rejected _ -> "venue_rejected"
end

module Anomaly = struct
  type t =
    | Illegal of { event : string; state : State.t }
    | Fill_after_terminal of State.t
    | Overfill of { filled : float; ordered : int }
  [@@deriving sexp_of, compare, equal]

  (* The journal writes this text (order_events.anomaly), and the journal is
     the record: an overfill's figure is printed with every digit its float
     has, not the six "%g" keeps. "%.17g" still drops trailing zeros, so a
     whole number of shares prints as one. *)
  let to_string = function
    | Illegal { event; state } ->
        sprintf "illegal: %s in %s" event (State.to_string state)
    | Fill_after_terminal state -> sprintf "fill after %s" (State.to_string state)
    | Overfill { filled; ordered } ->
        sprintf "overfill: %.17g filled against %d ordered" filled ordered
end

type t = {
  request : Request.t;
  state : State.t;
  venue_order_id : string option;
  filled_qty : float;
  (* Sum of qty x price over the distinct fills: the numerator of the average
     price, kept rather than the average itself so a new fill is one addition
     and not a re-weighting that accumulates rounding. *)
  filled_notional : float;
  execution_ids : String.Set.t;
  reason : string option;
}
[@@deriving sexp_of]

let create (request : Request.t) : t =
  {
    request;
    state = State.Pending_submit;
    venue_order_id = None;
    filled_qty = 0.0;
    filled_notional = 0.0;
    execution_ids = String.Set.empty;
    reason = None;
  }

let avg_fill_price (t : t) : float option =
  if Float.( > ) t.filled_qty 0.0 then Some (t.filled_notional /. t.filled_qty) else None

let remaining_qty (t : t) : float =
  Float.max 0.0 (Float.of_int t.request.Request.qty -. t.filled_qty)

(* A tolerance on share counts: venues report quantities as decimal strings,
   and a partial fill of 33.333333333 three times must read as the order being
   done, not as 1e-9 shares outstanding. *)
let epsilon = 1e-9

let apply_fill (t : t) (f : Fill.t) : t * Anomaly.t option =
  if Set.mem t.execution_ids f.Fill.execution_id then (t, None)
  else
    let filled = t.filled_qty +. f.Fill.qty in
    let counted =
      {
        t with
        filled_qty = filled;
        filled_notional = t.filled_notional +. (f.Fill.qty *. Price.to_float f.Fill.price);
        execution_ids = Set.add t.execution_ids f.Fill.execution_id;
      }
    in
    let ordered = t.request.Request.qty in
    let over =
      if Float.( > ) filled (Float.of_int ordered +. epsilon) then
        Some (Anomaly.Overfill { filled; ordered })
      else None
    in
    if State.is_terminal t.state then
      (* Counted, state unchanged, and the first anomaly worth a person's
         attention: a fill after a terminal state explains an overfill, not
         the other way round. *)
      (counted, Some (Anomaly.Fill_after_terminal t.state))
    else if Float.( >= ) filled (Float.of_int ordered -. epsilon) then
      ({ counted with state = State.Filled }, over)
    else ({ counted with state = State.Partially_filled }, None)

let illegal t event =
  (t, Some (Anomaly.Illegal { event = Event.name event; state = t.state }))

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

let apply (t : t) (event : Event.t) : t * Anomaly.t option =
  let move state = ({ t with state }, None) in
  match (t.state, event) with
  | _, Event.Venue_fill f -> apply_fill t f
  | State.Pending_submit, Event.Pre_trade_rejected reasons ->
      ( {
          t with
          state = State.Rejected_pre_trade;
          reason = Some (String.concat ~sep:"; " reasons);
        },
        None )
  | (State.Pending_submit | State.Submit_unknown), (Event.Acknowledged id | Event.Found id)
    ->
      ({ t with state = State.Submitted; venue_order_id = Some id }, None)
  | _, (Event.Acknowledged id | Event.Found id) -> keep_venue_id t event id
  | State.Pending_submit, Event.Venue_rejected_submission why ->
      ({ t with state = State.Rejected_by_venue; reason = Some why }, None)
  | State.Pending_submit, Event.Outcome_unknown why ->
      ({ t with state = State.Submit_unknown; reason = Some why }, None)
  | State.Submit_unknown, Event.Not_found -> move State.Failed
  | (State.Pending_submit | State.Submit_unknown | State.Submitted), Event.Venue_accepted
    ->
      move State.Accepted
  | (State.Accepted | State.Pending_cancel), Event.Venue_accepted -> (t, None)
  | (State.Submitted | State.Accepted | State.Partially_filled), Event.Cancel_requested ->
      move State.Pending_cancel
  | State.Pending_cancel, Event.Cancel_requested -> (t, None)
  | ( ( State.Pending_submit | State.Submit_unknown | State.Submitted | State.Accepted
      | State.Partially_filled | State.Pending_cancel ),
      Event.Venue_cancelled ) ->
      move State.Cancelled
  | ( ( State.Pending_submit | State.Submit_unknown | State.Submitted | State.Accepted
      | State.Partially_filled | State.Pending_cancel ),
      Event.Venue_expired ) ->
      move State.Expired
  | ( ( State.Pending_submit | State.Submit_unknown | State.Submitted | State.Accepted
      | State.Partially_filled ),
      Event.Venue_rejected why ) ->
      ({ t with state = State.Rejected_by_venue; reason = Some why }, None)
  | _, _ -> illegal t event

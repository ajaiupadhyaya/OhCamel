(** INC-01's stop rules, decided at the close: stop.ml's header says what fires and why
    nothing un-stops on its own. *)

open Core
module Spec := Ohcamel.Config.Book.Signals_spec

val check :
  spec:Spec.Stop.t ->
  record:Record.summary ->
  sessions:int ->
  [ `Continue | `Stop of string * string ]
(** [`Stop (rule, sentence)], the rule being ["drawdown"] or ["sessions"]. *)

val sessions :
  journal:Journal.t ->
  zone:Time_float.Zone.t ->
  strategy:string ->
  since:Date.t option ->
  Record.session list
(** The strategy's record from the journal, from its first fill, or from [since]. *)

val arrival_mid : zone:Time_float.Zone.t -> Journal.Strategy_fill.t -> float option
(** The midpoint of the quote a fill's order was sent against, when it is an arrival: a
    two-sided, uncrossed, finite quote journaled for a day order created 09:30-16:00 in
    [zone] on a weekday, with the quote's own stamp no more than 60 s from the order's
    creation. None -- the cost unknown -- for no quote, an auction order (market-on-open's
    was read after hours), an order created outside the session, a one-sided, locked or
    crossed quote, or a quote with no stamp or an older one: every order journaled before
    orders.arrival_at existed is unknown. *)

val dividends_of :
  cash_dividends:Venue.Cash_dividend.t list ->
  activities:Venue.Activity.t list ->
  Record.dividend list
(** One per cash-dividend record, at its ex-date; the per-share amount of the executed DIV
    activity for the name on its payable date when one carries it, else the record's rate.
    The activity itself is never a second dividend. *)

val unmatched_dividends :
  cash_dividends:Venue.Cash_dividend.t list ->
  activities:Venue.Activity.t list ->
  Venue.Activity.t list
(** Executed cash DIV activities no record names by symbol and a payable date within three
    days: paid, ex-date unknown, credited to no strategy. *)

val fees_by_order : Venue.Activity.t list -> float String.Map.t * float * int
(** Executed FEE activities as positive dollars: by the venue order id a fee names, and
    the total and count of those that name none (the account's, assigned to no strategy).
*)

val sessions_under :
  rule:[ `Before_1_8 | `Current ] ->
  ?dividends:Record.dividend list ->
  ?activities:Venue.Activity.t list ->
  journal:Journal.t ->
  zone:Time_float.Zone.t ->
  strategy:string ->
  since:Date.t option ->
  unit ->
  Record.session list
(** [sessions] under a rule: [`Current] is [sessions] (with [dividends] and [activities]
    in place of the journal's when given -- [record-diff] passes them, for a journal older
    than their tables); [`Before_1_8] is the record as it was computed before Task 1.8 --
    no dividend, no fee, no arrival mid -- for [record-diff]'s before column. *)

val held_before :
  journal:Journal.t ->
  zone:Time_float.Zone.t ->
  strategy:string ->
  Date.t ->
  Ohcamel.Types.Symbol.t list
(** The names the strategy held, from its own fills, at the close before the date. *)

val evaluate :
  defer_drawdown:(string -> string option) ->
  journal:Journal.t ->
  strategies:Spec.Strategy.t list ->
  zone:Time_float.Zone.t ->
  at:Time_ns.t ->
  on_event:(string -> unit) ->
  string list
(** Stops every strategy past a rule that is not stopped already, and names them. A
    strategy for which [defer_drawdown] answers a reason has its drawdown rule skipped
    tonight, said with that reason; its sessions rule still runs. *)

val reinstate :
  journal:Journal.t ->
  zone:Time_float.Zone.t ->
  strategies:Spec.Strategy.t list ->
  on_event:(string -> unit) ->
  unit
(** At startup: a [(reinstated DATE)] later than a strategy's stop re-activates it. *)

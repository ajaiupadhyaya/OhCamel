(** A strategy's live record as statistics, pure and with no verdict: record.ml's header
    says why. *)

open Core

type session = {
  date : Date.t;
  allocated : float;  (** Dollars of capital the strategy held that session. *)
  pnl : float;
      (** Marked to this close from the last, less what the session's fills paid, less
          their fees, plus the session's [dividends]. The fill price is what was paid, so
          the part of [cost] that is price against the arrival mid is already in here and
          is not taken a second time; the fee is cash the price does not show, so it is.
      *)
  cost : float;
      (** Dollars, positive when worse than the arrival mid: {!fill_cost} summed over the
          session's fills, a fill with no arrival mid counting its fee alone. *)
  turnover : float;  (** Dollars traded. *)
  dividends : float;
      (** Credited on an ex-date to the shares held at the close before it, a short
          debited; inside [pnl]. *)
  fees : float;  (** The fills' fees, inside both [pnl] and [cost]. *)
  cost_measured : float;  (** [cost] over the fills that had an arrival mid. *)
  cost_notional : float;  (** Their [|qty| x arrival_mid], what [cost_bps] divides by. *)
  cost_unknown : int;  (** Fills with no arrival mid: cost not measured, fee counted. *)
}

type summary = {
  sessions : int;  (** Those with capital allocated; the others are skipped. *)
  compounded : float;
  sharpe : float option;  (** Annualised by the square root of 252. *)
  psr : float option;  (** Against a Sharpe of zero. *)
  max_drawdown : float;  (** A positive fraction of the compounded path's peak. *)
  hit_rate : float option;
  turnover_mean : float option;  (** Of allocated capital, per session. *)
  cost_bps : float option;
      (** OVER THE FILLS WITH AN ARRIVAL MID ONLY, notional-weighted: the sum of their
          [cost_measured] over the sum of their [cost_notional], times 10,000. None when
          no fill had one. A fill with no arrival mid is in [cost_unknown] and not here.
      *)
  cost_unknown : int;  (** Fills whose cost against the arrival mid is unknown. *)
  dividends : float;  (** Dollars credited (debited, for a short) over the sessions. *)
}

type fill = {
  date : Date.t;
  symbol : Ohcamel.Types.Symbol.t;
  qty : float;  (** Signed, bought positive. *)
  price : float;
  arrival_mid : float option;
      (** The midpoint of the venue's quote the order was sent against, or None when that
          quote is not usable as an arrival (stop.ml's [arrival_mid] says which): never a
          zero and never a guess. *)
  fee : float;  (** Dollars, positive: the venue's fee for this fill, 0 when none. *)
}
(** One of a strategy's fills. *)

type dividend = { ex_date : Date.t; symbol : Ohcamel.Types.Symbol.t; per_share : float }
(** A cash dividend: who held the name at the close before [ex_date] is owed [per_share].
*)

val fill_cost : fill -> float
(** [sign(qty) x (price - arrival_mid) x |qty| + fee], in dollars, positive when worse
    than the mid on either side, so price improvement is a NEGATIVE cost. A BUY of 100 at
    50.05 against a mid of 50.00 with a fee of 0.10 costs 5.10 (10.2 bps of the 5,000.00
    at mid); a SELL of 100 at 49.95 against 50.00 costs 5.00. With no arrival mid, the fee
    alone. *)

val sessions_of :
  dates:Date.t list ->
  close:(Date.t -> Ohcamel.Types.Symbol.t -> float option) ->
  allocated:(Date.t -> float) ->
  dividends:dividend list ->
  fill list ->
  session list
(** The strategy's sessions from its first fill, on the recorded [dates]. A dividend is
    credited in the first recorded session on or after its [ex_date] (after the session
    before), for the strategy's own shares from fills dated before [ex_date]; a dividend
    on a date with no session lands in the next one, as a fill does. *)

val returns : session list -> float list
(** [pnl / allocated], skipping a session with nothing allocated. *)

val drawdown : returns:float list -> float

val psr : returns:float list -> benchmark:float -> float option
(** Per-period, as Bailey and Lopez de Prado state it. None under four returns, where the
    moments are undefined, or with no spread. *)

val summarise : session list -> summary

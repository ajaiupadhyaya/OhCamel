(** A strategy's live record as statistics, pure and with no verdict: record.ml's header
    says why. *)

open Core

type session = {
  date : Date.t;
  allocated : float;  (** Dollars of capital the strategy held that session. *)
  pnl : float;
  cost : float;
  turnover : float;  (** Dollars traded. *)
}

type summary = {
  sessions : int;  (** Those with capital allocated; the others are skipped. *)
  compounded : float;
  sharpe : float option;  (** Annualised by the square root of 252. *)
  psr : float option;  (** Against a Sharpe of zero. *)
  max_drawdown : float;  (** A positive fraction of the compounded path's peak. *)
  hit_rate : float option;
  turnover_mean : float option;  (** Of allocated capital, per session. *)
  cost_bps : float option;  (** Of dollars traded. *)
}

val returns : session list -> float list
(** [pnl / allocated], skipping a session with nothing allocated. *)

val drawdown : returns:float list -> float

val psr : returns:float list -> benchmark:float -> float option
(** Per-period, as Bailey and Lopez de Prado state it. None under four returns, where the
    moments are undefined, or with no spread. *)

val summarise : session list -> summary

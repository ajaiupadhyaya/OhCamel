(* Options: contract types, Black-Scholes prices, and the Greeks.

   Everything above this module measures risk in one dimension. An equity
   position's exposure is price times quantity, its risk moves linearly with the
   price, and a covariance matrix over returns says everything there is to say
   about how the book behaves. None of that survives contact with an option. A
   position can be flat in the underlying and still lose money on a move in
   either direction (gamma), or lose money on no move at all (theta), or lose
   money because the market changed its mind about how much the underlying will
   move without the underlying moving (vega).

   So the engine needs a second and a third number per position, and it needs
   them to be distinct KINDS of number rather than three more entries in the
   exposure table. That is what this module and its graph nodes provide.

   WHAT IS DELIBERATELY NOT HERE

   No implied-volatility solve. Inverting Black-Scholes for sigma given a market
   price is a Newton iteration that needs care at the wings, and this engine has
   no options-chain data source to invert prices FROM -- so writing it would be
   building the second half of a bridge to nowhere. Implied vol is an INPUT
   here, supplied per position, and live mode declines to guess one rather than
   inventing a surface. See graph.ml.

   No American exercise, no dividends, no term structure of rates. Black-Scholes
   with a single flat rate and a single vol per contract. Each of those is a real
   simplification and each is named in the README rather than left for a reader
   to discover; the alternative -- a binomial tree and a dividend schedule -- is a
   derivatives library, which is a different project.

   UNITS, WHICH ARE THE EASIEST THING TO GET WRONG HERE

   Three conventions, fixed once:

     time      YEARS. A 30-day option is T = 30/365, not 30.
     vol       ANNUALISED, as a fraction. 0.20 is "20% a year", not 20.0 and
               not a daily number. The return windows elsewhere in this engine
               are daily, and multiplying a daily sigma into a Black-Scholes
               formula expecting an annual one understates every Greek by a
               factor of about sixteen while producing entirely plausible
               numbers.
     rate      CONTINUOUSLY COMPOUNDED and annualised. 0.05 is 5%.

   [vega] and [theta] are returned in their MATHEMATICAL units -- per 1.00 of
   volatility and per year -- not in the desk conventions of "per vol point" and
   "per day". Scaling by 1/100 and 1/365 is a display decision and it belongs
   where the display is, not baked into a number other code does arithmetic on.
   Getting this backwards is how a vega limit ends up a hundred times too loose.

   Nothing here references Incremental, and nothing here is stateful -- the same
   contract risk_metrics.ml and vol_estimators.ml keep, so every function below
   is testable against a value from a textbook. *)

open Core

(* ------------------------------------------------------------------------ *)
(* Types                                                                     *)
(* ------------------------------------------------------------------------ *)

(* A strike is a price, and it is NOT [Types.Price.t].

   The temptation is obvious -- both are dollars per share -- and taking it
   would be wrong for the reason the whole types.ml discipline exists: a strike
   is a term of a contract and a price is a fact about the market, they are
   never interchangeable, and the compiler is the only thing that will reliably
   notice when one is passed where the other belongs. The single bridge between
   the two worlds is [Black_scholes.compute], which takes both as plain floats
   at exactly one call site. *)
module Strike : sig
  type t [@@deriving sexp_of, compare, equal]

  val of_float : float -> t
  val to_float : t -> float
end = struct
  type t = float [@@deriving sexp_of, compare, equal]

  let of_float = Fn.id
  let to_float = Fn.id
end

(* Annualised implied volatility as a fraction: 0.20 is 20% a year.

   Distinct from a bare float because the daily-versus-annual mistake is silent
   and large -- a factor of sqrt(252), about sixteen -- and produces Greeks that
   are the right shape and the wrong size. A named type does not prevent
   somebody constructing one from a daily number, but it does mean the
   conversion has to be written down somewhere a reader can find it. *)
module Implied_vol : sig
  type t [@@deriving sexp_of, compare, equal]

  val of_float : float -> t
  val to_float : t -> float
end = struct
  type t = float [@@deriving sexp_of, compare, equal]

  let of_float = Fn.id
  let to_float = Fn.id
end

(* A number of CONTRACTS, which is not a number of shares.

   This is the invariant-3 case that actually bites. One contract is
   conventionally a hundred shares, so a book holding "60" of something is
   holding either 60 shares or 6,000 shares' worth of delta depending on which
   type that 60 is, and both readings produce a plausible exposure. Making
   [Contracts.t] and [Types.Qty.t] incompatible means the multiplier has to be
   applied explicitly, at one place, in [Position.delta_equivalent] below. *)
module Contracts : sig
  type t [@@deriving sexp_of, compare, equal]

  val of_float : float -> t
  val to_float : t -> float
  val is_zero : t -> bool
end = struct
  type t = float [@@deriving sexp_of, compare, equal]

  let of_float = Fn.id
  let to_float = Fn.id
  let is_zero t = Float.equal t 0.0
end

(* Standard tenor buckets, and the reason this type exists at all.

   A single portfolio vega adds sensitivities to DIFFERENT volatilities. The
   30-day implied and the 180-day implied are distinct random variables that
   move together but not identically, so summing across expiries treats the
   whole term structure as one number shifting in parallel. Every desk does this
   and calls it parallel-shift vega. It is an approximation, and the case it
   gets badly wrong is the calendar spread: long one expiry against short
   another nets to nearly zero parallel-shift vega while carrying real exposure
   to the term structure TWISTING.

   Bucketing does not fix the approximation -- vega within a bucket is still
   summed across the expiries inside it -- but it makes the thing being
   approximated visible. A book whose buckets are large and opposite while its
   total is zero is a book whose total is not telling you anything.

   The boundaries are the conventional desk tenors rather than anything derived.
   They are cut on CALENDAR days to expiry, consistent with
   [years_to_expiry] and with Black-Scholes discounting, so a contract moves
   between buckets as the valuation date advances -- which is correct and is why
   graph.ml recomputes the bucketing off the valuation clock rather than
   assigning a bucket once at construction. *)
module Tenor_bucket = struct
  type t =
    | Under_week
    | Week_to_month
    | One_to_three
    | Three_to_six
    | Six_to_twelve
    | Over_year
  [@@deriving sexp_of, compare, equal, enumerate]

  let of_days days =
    if Float.( <= ) days 7.0 then Under_week
    else if Float.( <= ) days 30.0 then Week_to_month
    else if Float.( <= ) days 90.0 then One_to_three
    else if Float.( <= ) days 180.0 then Three_to_six
    else if Float.( <= ) days 365.0 then Six_to_twelve
    else Over_year

  let to_string = function
    | Under_week -> "<=1w"
    | Week_to_month -> "1w-1m"
    | One_to_three -> "1-3m"
    | Three_to_six -> "3-6m"
    | Six_to_twelve -> "6-12m"
    | Over_year -> ">1y"

  (* Ordered near to far, which is the order a term structure is read in. The
     derived [all] follows the constructor order, which is already this, but
     relying on that implicitly would make a reordering of the type silently
     reorder every display. *)
  let ordered =
    [ Under_week; Week_to_month; One_to_three; Three_to_six; Six_to_twelve; Over_year ]

  include Comparable.Make_plain (struct
    type nonrec t = t [@@deriving sexp_of, compare]
  end)
end

module Right = struct
  type t = Call | Put [@@deriving sexp_of, compare, equal]

  let to_string = function Call -> "C" | Put -> "P"
end

(* ------------------------------------------------------------------------ *)
(* Black-Scholes                                                             *)
(* ------------------------------------------------------------------------ *)

module Black_scholes = struct
  (* Price and the four first- and second-order sensitivities the engine uses.

     Rho is absent. It is the sensitivity to the risk-free rate, it is the
     smallest of the five for any equity option under a year, and this engine
     has exactly one flat rate with no term structure to shock -- so a rho it
     could not use in a scenario would be a number on a dashboard that nothing
     downstream reads. Adding it is easy if a rates book ever appears. *)
  type t = {
    price : float;
    (* d(price)/d(spot). In (0, 1) for a call, (-1, 0) for a put. *)
    delta : float;
    (* d2(price)/d(spot)2. Positive for BOTH rights -- an option is convex in
       the underlying whichever way it points, which is the entire reason a
       delta-hedged book still has risk. *)
    gamma : float;
    (* d(price)/d(sigma), per 1.00 of annualised vol. Positive for both rights.
       Divide by 100 to get the desk's "per vol point", at the display and not
       here. *)
    vega : float;
    (* d(price)/d(t), per YEAR, and normally negative: an option decays. Divide
       by 365 for the desk's "per day", again at the display. *)
    theta : float;
  }
  [@@deriving sexp_of, fields ~getters]

  let normal_cdf x = Owl.Stats.gaussian_cdf x ~mu:0.0 ~sigma:1.0
  let normal_pdf x = Owl.Stats.gaussian_pdf x ~mu:0.0 ~sigma:1.0

  (* The degenerate boundary, handled first and deliberately rather than fallen
     into.

     When sigma * sqrt(T) reaches zero -- an expiring contract, or a vol of zero
     -- d1 and d2 are a division by zero and every formula below produces a nan
     or an infinity. There is a correct answer and it is not nan: the option is
     worth its intrinsic value, its delta is 0 or 1 (or 0 or -1), and gamma,
     vega and theta are all zero, because a contract with no remaining
     uncertainty has no sensitivity to uncertainty.

     The one genuinely undefined case is a contract expiring exactly at the
     money, where delta is a step and gamma is unbounded. Reported as delta 0
     and gamma 0 rather than as a coin flip, on the grounds that a limit read
     off an infinity is worse than a limit read off a slightly wrong zero, and
     the position is about to cease existing either way. Named here rather than
     left as an emergent property of the arithmetic. *)
  let intrinsic ~spot ~strike ~(right : Right.t) =
    let in_the_money =
      match right with
      | Right.Call -> Float.( > ) spot strike
      | Right.Put -> Float.( < ) spot strike
    in
    let price =
      match right with
      | Right.Call -> Float.max 0.0 (spot -. strike)
      | Right.Put -> Float.max 0.0 (strike -. spot)
    in
    let delta =
      if not in_the_money then 0.0
      else match right with Right.Call -> 1.0 | Right.Put -> -1.0
    in
    { price; delta; gamma = 0.0; vega = 0.0; theta = 0.0 }

  (* Structurally invalid input raises, matching risk_metrics.ml's convention.
     A negative time to expiry or a negative volatility is a bug in the caller,
     and the alternative -- clamping to zero -- turns it into a plausible number
     that nobody investigates. graph.ml guards before calling, so these should
     never reach a node body. *)
  let validate ~spot ~strike ~time_to_expiry ~implied_vol =
    if Float.( <= ) spot 0.0 then
      invalid_argf "options: spot must be positive, got %f" spot ();
    if Float.( <= ) strike 0.0 then
      invalid_argf "options: strike must be positive, got %f" strike ();
    if Float.is_negative time_to_expiry then
      invalid_argf
        "options: time_to_expiry must be non-negative years, got %f (an expired contract \
         is time 0, not negative time)"
        time_to_expiry ();
    if Float.is_negative implied_vol then
      invalid_argf "options: implied_vol must be non-negative, got %f" implied_vol ()

  (* Black-Scholes-Merton, European, no dividends, one flat continuously
     compounded rate.

       d1 = [ln(S/K) + (r + sigma^2 / 2) T] / (sigma sqrt(T))
       d2 = d1 - sigma sqrt(T)

       call = S N(d1) - K e^(-rT) N(d2)
       put  = K e^(-rT) N(-d2) - S N(-d1)

     [spot], [strike], [time_to_expiry] (years), [rate] and [implied_vol] are
     plain floats rather than the abstract types above. This is the one function
     where the two worlds meet, and it is deliberately a single narrow crossing
     -- the same shape as [Types.notional], which is the only place Price, Qty
     and Notional are allowed to touch. *)
  let compute ~(spot : float) ~(strike : float) ~(time_to_expiry : float) ~(rate : float)
      ~(implied_vol : float) ~(right : Right.t) : t =
    validate ~spot ~strike ~time_to_expiry ~implied_vol;
    let sqrt_t = Float.sqrt time_to_expiry in
    let sigma_sqrt_t = implied_vol *. sqrt_t in
    (* Not [Float.equal sigma_sqrt_t 0.0]. A contract with an hour left and a 1%
       vol has a sigma*sqrt(T) around 1e-4, which is non-zero, and dividing by
       it yields a d1 of several thousand -- from which N(d1) is exactly 1 and
       gamma is exactly 0 anyway, but vega and theta come back as denormal
       garbage. The threshold puts the whole neighbourhood on the intrinsic
       branch, where the answers are right rather than merely finite. *)
    if Float.( < ) sigma_sqrt_t 1e-8 then intrinsic ~spot ~strike ~right
    else begin
      let discount = Float.exp (-.rate *. time_to_expiry) in
      let d1 =
        (Float.log (spot /. strike)
        +. ((rate +. (0.5 *. implied_vol *. implied_vol)) *. time_to_expiry))
        /. sigma_sqrt_t
      in
      let d2 = d1 -. sigma_sqrt_t in
      let nd1 = normal_cdf d1 and nd2 = normal_cdf d2 in
      let pdf_d1 = normal_pdf d1 in
      (* Gamma and vega are identical for a call and a put at the same strike
         and expiry, and that is not a coincidence to be checked -- it follows
         from put-call parity, since the two prices differ by S - K e^(-rT),
         which is linear in S and free of sigma. Computed once, shared, so the
         property cannot be broken in one branch and not the other. *)
      let gamma = pdf_d1 /. (spot *. sigma_sqrt_t) in
      let vega = spot *. pdf_d1 *. sqrt_t in
      let decay = -.(spot *. pdf_d1 *. implied_vol) /. (2.0 *. sqrt_t) in
      match right with
      | Right.Call ->
          {
            price = (spot *. nd1) -. (strike *. discount *. nd2);
            delta = nd1;
            gamma;
            vega;
            theta = decay -. (rate *. strike *. discount *. nd2);
          }
      | Right.Put ->
          {
            price =
              (strike *. discount *. normal_cdf (-.d2)) -. (spot *. normal_cdf (-.d1));
            (* N(d1) - 1, not -N(-d1), though they are equal. Written as the
               call's delta minus one because that is what put-call parity says
               it is, and it makes the relationship checkable by eye. *)
            delta = nd1 -. 1.0;
            gamma;
            vega;
            theta = decay +. (rate *. strike *. discount *. normal_cdf (-.d2));
          }
    end
end

(* ------------------------------------------------------------------------ *)
(* A position                                                                *)
(* ------------------------------------------------------------------------ *)

(* The conventional shares-per-contract multiplier for a US listed equity
   option. Carried on the position rather than hard-coded, because it is not
   universal -- index options, adjusted contracts after a corporate action, and
   non-US listings all differ -- and a constant 100 buried in an exposure
   formula is a bug that only appears on the one position where it is wrong. *)
let default_multiplier = 100.0

module Position = struct
  (* The contract's terms, which do not change, separated from what the market
     and the book do change.

     [implied_vol] is NOT here. It moves -- it is the most volatile input this
     engine has -- so it belongs in a graph input cell alongside price, not in
     an immutable description of the contract. Putting it here would have been
     the invisible-dependency mistake in a new place. *)
  type t = {
    (* The equity this option is written on. It must be an instrument the graph
       already knows, so an option's delta lands in the same exposure bucket
       the underlying's shares do. *)
    underlying : Types.Symbol.t;
    (* A name for the contract, used as a map key and in displays. Contracts are
       not identified by their underlying -- a book routinely holds several
       strikes and expiries on one name -- so this is what distinguishes them. *)
    id : string;
    strike : Strike.t;
    right : Right.t;
    (* Days from the valuation date to expiry, at construction. The graph turns
       this into a live time-to-expiry against a valuation-date input cell, so
       the number here is a term of the contract and not a running clock. *)
    expiry_in_days : float;
    multiplier : float;
  }
  [@@deriving sexp_of, fields ~getters]

  let create ?(multiplier = default_multiplier) ~underlying ~id ~strike ~right
      ~expiry_in_days () =
    { underlying; id; strike; right; expiry_in_days; multiplier }

  let describe t =
    Printf.sprintf "%s %g%s %gd"
      (Types.Symbol.to_string t.underlying)
      (Strike.to_float t.strike) (Right.to_string t.right) t.expiry_in_days

  (* DELTA-EQUIVALENT EXPOSURE: the number of dollars of underlying this
     position currently behaves like.

       delta * multiplier * contracts * spot

     This is the quantity that folds into the engine's existing per-instrument
     exposure, and folding rather than paralleling is the whole design decision
     of this phase. Gross, net, weights, equity, drawdown and every notional
     limit are already functions of exposure[S]; adding options here means all
     of them account for options with no further work and, more importantly,
     with no second definition of what "exposure" means.

     It is an approximation and it is a first-order one: it says what the
     position does for a SMALL move, and gamma is precisely the statement that
     the number is wrong for a large one. That is why gamma and vega are
     reported separately rather than folded in anywhere -- there is no
     defensible way to express convexity as a quantity of underlying, and a
     number that tried would be a linear summary of the thing that is not
     linear. *)
  let delta_equivalent t ~(greeks : Black_scholes.t) ~(contracts : Contracts.t)
      ~(spot : float) : Types.Notional.t =
    Types.Notional.of_float
      (greeks.Black_scholes.delta *. t.multiplier *. Contracts.to_float contracts *. spot)

  (* Position gamma, per 1.00 move in the underlying, in dollars-of-delta.

     Scaled by multiplier and contracts but NOT by spot, unlike delta above.
     Raw gamma is d2(price)/d(spot)2 per share; multiplying by multiplier and
     contracts gives the book's dollar delta change per 1.00 move in the
     underlying, which is the quantity a trader hedges against and the quantity
     that is additive across positions. Multiplying by spot again would give
     "dollar gamma per 1% move", which is also a real convention and a different
     number -- the two differ by a factor of the spot price, so mixing them
     across a book with names at $30 and $900 is a thirty-fold error in one
     row. *)
  let gamma_exposure t ~(greeks : Black_scholes.t) ~(contracts : Contracts.t) : float =
    greeks.Black_scholes.gamma *. t.multiplier *. Contracts.to_float contracts

  (* Position vega, in dollars per 1.00 of annualised volatility.

     Divide by 100 at the display for the desk's "dollars per vol point". *)
  let vega_exposure t ~(greeks : Black_scholes.t) ~(contracts : Contracts.t) : float =
    greeks.Black_scholes.vega *. t.multiplier *. Contracts.to_float contracts

  (* Position theta, in dollars per year. Divide by 365 at the display. *)
  let theta_exposure t ~(greeks : Black_scholes.t) ~(contracts : Contracts.t) : float =
    greeks.Black_scholes.theta *. t.multiplier *. Contracts.to_float contracts
end

(* Years to expiry, given how far the valuation date has advanced.

   365 rather than 252. Black-Scholes discounts and decays in CALENDAR time --
   an option held over a weekend loses two days of theta and earns two days of
   carry, and the market prices it that way. Trading-day counts belong to
   volatility estimation, which is a different quantity in a different module,
   and using 252 here would misprice every contract by about 4% of its time
   value while looking like a reasonable choice.

   Clamped at zero: a valuation date past expiry is an expired contract, worth
   its intrinsic value, not one with negative time. *)
let years_to_expiry ~(expiry_in_days : float) ~(days_elapsed : float) : float =
  Float.max 0.0 ((expiry_in_days -. days_elapsed) /. 365.0)

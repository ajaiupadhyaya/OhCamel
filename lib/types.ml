(* Core domain types.

   Two conventions from the README bind hardest here, because every other module
   inherits whatever this one decides.

   1. No bare floats for money or quantity. Price, Qty and Notional are abstract
      and mutually incompatible, so `price + qty` and `notional * price` are type
      errors rather than plausible-looking nonsense. The single sanctioned bridge
      between them is [notional], below: price x quantity -> notional. That is
      the only place the three meet, which makes it the only place a units bug
      can be introduced.

      Deliberately absent: Price has no [add]. Adding two prices is meaningless
      (the average of two prices is a weighted question, not a sum), so the
      operation simply does not exist rather than existing and being misused.

   2. No 'a soup. Symbol and Sector are distinct types even though both are
      strings underneath, so a sector can never be passed where a symbol is
      expected -- a mistake that is otherwise invisible to the compiler and
      silently produces an empty lookup at run time.

   Everything here is plain data. No module in this file knows that Incremental
   exists. *)

open Core

(* Symbol and Sector are string-backed but abstract, and both carry a comparator
   so they can key a Map -- exposure is aggregated per-instrument and per-sector,
   so both are used as map keys in graph.ml. *)

module Symbol : sig
  type t [@@deriving sexp_of, compare, equal, hash]

  include Comparable.S_plain with type t := t

  (* Hashable as well as comparable: the ordered Map is what the graph uses (its
     key order is what keeps weights aligned with covariance rows), but a feed
     looking up thousands of incoming symbols a second wants the hash table. *)
  include Hashable.S_plain with type t := t

  val of_string : string -> t
  val to_string : t -> string
end = struct
  module T = struct
    type t = string [@@deriving sexp_of, compare, equal, hash]
  end

  include T
  include Comparable.Make_plain (T)
  include Hashable.Make_plain (T)

  let of_string = Fn.id
  let to_string = Fn.id
end

module Sector : sig
  type t [@@deriving sexp_of, compare, equal, hash]

  include Comparable.S_plain with type t := t

  val of_string : string -> t
  val to_string : t -> string
end = struct
  module T = struct
    type t = string [@@deriving sexp_of, compare, equal, hash]
  end

  include T
  include Comparable.Make_plain (T)

  let of_string = Fn.id
  let to_string = Fn.id
end

(* A quantity of an instrument, signed: positive is long, negative is short.

   Floats rather than integers because this engine is not the system of record
   for share counts -- it consumes them. Fractional quantities are also real
   (fractional shares, FX, crypto), so an integer type would be wrong as often
   as it was right. *)
module Qty : sig
  type t [@@deriving sexp_of, compare, equal]

  val of_float : float -> t
  val to_float : t -> float
  val zero : t
  val add : t -> t -> t
  val neg : t -> t
  val abs : t -> t
  val is_zero : t -> bool
  val is_negative : t -> bool
end = struct
  type t = float [@@deriving sexp_of, compare, equal]

  let of_float = Fn.id
  let to_float = Fn.id
  let zero = 0.0
  let add = ( +. )
  let neg t = -.t
  let abs = Float.abs
  let is_zero t = Float.equal t 0.0
  let is_negative t = Float.is_negative t
end

(* A price per unit. Always positive in practice, but not enforced: a validating
   constructor here would either raise deep inside a graph node (where there is
   no sensible recovery) or silently clamp bad market data into plausible data,
   which is worse. Feed-level validation is the right place for that, and it is
   a Phase 2 concern. *)
module Price : sig
  type t [@@deriving sexp_of, compare, equal]

  val of_float : float -> t
  val to_float : t -> float
end = struct
  type t = float [@@deriving sexp_of, compare, equal]

  let of_float = Fn.id
  let to_float = Fn.id
end

(* Signed money: a position's market value, an exposure, a limit threshold.
   Negative means short. *)
module Notional : sig
  type t [@@deriving sexp_of, compare, equal]

  val of_float : float -> t
  val to_float : t -> float
  val zero : t
  val add : t -> t -> t
  val sub : t -> t -> t
  val neg : t -> t
  val abs : t -> t
  val sum : t list -> t
  val ( > ) : t -> t -> bool
  val ( >= ) : t -> t -> bool
end = struct
  type t = float [@@deriving sexp_of, compare, equal]

  let of_float = Fn.id
  let to_float = Fn.id
  let zero = 0.0
  let add = ( +. )
  let sub = ( -. )
  let neg t = -.t
  let abs = Float.abs
  let sum ts = List.fold ts ~init:0.0 ~f:( +. )
  let ( > ) = Float.( > )
  let ( >= ) = Float.( >= )
end

(* The one sanctioned crossing between the three numeric types. Every notional in
   the system is ultimately produced here, so if the units are right here they
   are right everywhere. *)
let notional ~(price : Price.t) ~(qty : Qty.t) : Notional.t =
  Notional.of_float (Price.to_float price *. Qty.to_float qty)

module Instrument = struct
  (* Sector is carried on the instrument rather than looked up in a side table,
     so that sector exposure in graph.ml is a pure function of the positions it
     already depends on. A side table would be a dependency that Incremental
     cannot see, and an invisible dependency is a stale number waiting to
     happen. *)
  type t = { symbol : Symbol.t; sector : Sector.t }
  [@@deriving sexp_of, compare, equal, fields ~getters]
end

(* Time_ns.Alternate_sexp rather than plain Time_ns: Core deprecated
   Time_ns.sexp_of_t in favour of either Time_ns_unix (which drags in a Unix
   dependency) or Alternate_sexp (which does not). Same type, different sexp
   representation. *)
module Time = struct
  include Time_ns.Alternate_sexp

  (* Re-exported so no other module has to reach for Time_ns directly and
     accidentally reintroduce the deprecated sexp representation. *)
  let epoch = Time_ns.epoch
  let now = Time_ns.now
  let diff = Time_ns.diff
  let add = Time_ns.add

  module Span = Time_ns.Span
end

module Tick = struct
  type t = { symbol : Symbol.t; price : Price.t; time : Time.t }
  [@@deriving sexp_of, fields ~getters]
end

module Fill = struct
  (* [qty] is signed: positive is a buy, negative is a sell. One signed field
     rather than a separate side enum, so that applying a fill to a position is
     addition and cannot get the sign convention wrong in only one branch. *)
  type t = { symbol : Symbol.t; qty : Qty.t; price : Price.t; time : Time.t }
  [@@deriving sexp_of, fields ~getters]
end

module Position = struct
  type t = { instrument : Instrument.t; qty : Qty.t }
  [@@deriving sexp_of, fields ~getters]

  let create instrument qty = { instrument; qty }
  let symbol t = t.instrument.Instrument.symbol
  let sector t = t.instrument.Instrument.sector

  (* Applying a fill is addition because [Fill.qty] is signed. *)
  let apply_fill t (fill : Fill.t) = { t with qty = Qty.add t.qty fill.Fill.qty }
end

module Limit = struct
  (* What a limit is measured over. *)
  type scope = Instrument of Symbol.t | Sector of Sector.t | Portfolio
  [@@deriving sexp_of, compare, equal]

  (* What is measured, and the threshold.

     Kinds are separated rather than collapsed into one float because they are
     not in the same units -- notional dollars versus a fraction -- and a single
     [threshold : float] field is exactly how a drawdown limit ends up compared
     against a dollar exposure. *)
  type kind =
    | Gross_notional of Notional.t (* |exposure| may not exceed this *)
    | Value_at_risk of Notional.t (* portfolio VaR may not exceed this *)
    | Max_drawdown of float (* fraction in [0,1], e.g. 0.1 = 10% *)
    | Component_var of Notional.t
      (* This scope's SHARE of portfolio VaR may not exceed this.

           Separate from [Value_at_risk] rather than a scope of it, because it
           is a different measurement and not merely the same one narrowed.
           [Value_at_risk] is a quantile of the book's own return distribution
           and exists only at portfolio level. A component is the Euler share
           of that quantile attributable to one instrument or sector -- see
           attribution.ml -- and it is defined at every scope precisely because
           the shares add up to the total.

           The consequence worth understanding before writing one: this limit
           is correlation-aware, so a position's number moves when OTHER
           positions move. Adding a hedge can bring a name back inside its
           component limit without trading that name at all, which is correct
           and is the entire point, but it does mean the limit is not a
           property of the position in isolation the way a notional cap is. *)
  [@@deriving sexp_of, compare, equal]

  type t = { name : string; scope : scope; kind : kind }
  [@@deriving sexp_of, compare, equal, fields ~getters]

  let scope_to_string = function
    | Instrument s -> "instrument:" ^ Symbol.to_string s
    | Sector s -> "sector:" ^ Sector.to_string s
    | Portfolio -> "portfolio"
end

module Breach = struct
  (* The result of evaluating one limit.

     [observed], [threshold] and [excess] are plain floats whose unit is
     determined by [limit.kind] -- dollars for Gross_notional and Value_at_risk,
     a fraction for Max_drawdown. They are not Notional.t precisely because they
     are not always notional. Read the kind before formatting the number.

     [excess] is observed - threshold, so it is positive exactly when [breached]
     is true, and its magnitude says how far over the line the book is. A bare
     bool would tell you a limit broke without telling you whether to shrug or
     to halt. *)
  type t = {
    limit : Limit.t;
    observed : float;
    threshold : float;
    excess : float;
    breached : bool;
  }
  [@@deriving sexp_of, fields ~getters]

  let create ~(limit : Limit.t) ~observed ~threshold =
    let excess = observed -. threshold in
    { limit; observed; threshold; excess; breached = Float.( > ) excess 0.0 }
end

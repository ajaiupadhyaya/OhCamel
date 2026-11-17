(** The strategies' positions against the account's, as one sum per name (design: "one
    sub-ledger check for the desk").

    Each strategy's own position in a name is the net of the desk's fills of its orders
    (sources [signal:<slug>:<sequence>]), never the account's. Several strategies may hold
    one name, on the same side or on opposite sides, and the account holds their NET: a
    strategy long 100 SPY beside one short 50 is an account long 50. So no strategy's
    position can be compared with the account's on its own -- that comparison refused the
    short one forever, since the account (+50) never "holds" its -50. What can be compared
    is the sum: per name, the strategies' own positions together, with the desk's hand
    tickets beside them ({!hand}), must be the account's position. A name where they are
    not is a BREAK: a sale or a purchase made outside the desk, a split, or a fill the
    desk never heard of.

    Pure: the order manager gathers the figures (Oms.subledger) -- which names to judge,
    and that none of them has an order that may still fill -- and this decides. *)

open Ohcamel.Types

type break = { symbol : Symbol.t; strategies : float; account : float }
[@@deriving sexp_of, compare, equal]
(** A name whose sub-ledger does not sum: [strategies] is every owner's own position
    passed to {!check} summed -- the strategies' and the hand's -- and [account] the
    account's. *)

val hand : string
(** The owner of every fill whose order no strategy proposed: a ticket, a manual order.
    Hand tickets belong to no strategy, and they are one more sub-ledger beside the
    strategies', so a name a strategy trades and the owner also traded by hand still sums.
*)

val owner : source:string -> string
(** Whose sub-ledger an order's journal source names: [signal:<slug>:<sequence>] is the
    strategy <slug>'s, anything else {!hand}'s. *)

val tolerance : float
(** 0.5 of a share: the default [tolerance]. No position on this desk is a fraction of a
    share -- every order is whole shares, and each fill is a whole number of them -- so a
    real break is at least one share, and anything under half of one is the noise of
    summing floats. A break seen while an order in the name may still fill is not a
    tolerance question: the caller does not judge such a name at all (Oms.subledger). *)

val check :
  own:(string * Symbol.t * float) list ->
  account:(Symbol.t * float) list ->
  tolerance:float ->
  break list
(** Every name in [own] or in [account], each judged once: the sum of [own]'s entries for
    it (owner, symbol, position) against [account]'s figure for it, a name missing from
    either side counting zero there. A break when the two differ by more than [tolerance],
    or when either is not a finite number -- an unknown is never a sum that holds. By
    symbol. *)

val refuses : break list -> symbol:Symbol.t -> increases:bool -> string option
(** Why an order in [symbol] may not go, if [symbol] breaks and the order [increases]
    exposure, naming both figures; None otherwise -- a name with no break, or an order
    that does not increase.

    [increases] is the caller's, judged against THE PROPOSING STRATEGY'S OWN position
    (Rebalance.grows: it opens a position, takes one further from zero, or takes it
    through zero), never against the account's. Every desk fill moves the account and the
    sub-ledger by the same quantity, so no desk order widens or narrows a break: what an
    order risks is the size of the strategy it belongs to. A reduction of its own goes, so
    every strategy in a broken name can still be cut and stopped -- the long sells, the
    short covers -- and what it may not do is grow, however the account's net moves. *)

val describe : break -> string
(** The break as a sentence's clause: the name, the sub-ledger's sum and the account's
    figure. *)

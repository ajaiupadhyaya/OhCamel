(** An intraday rebalance's orders, in the waves they are sent in (design §4.3).

    A rebalance is gated on every combination of its orders that could be its fills, so it
    may have at most {!Rebalance.max_legs} of them. An intraday one with more is sent in
    waves, each gated on the book as it is when its turn comes: first every order that
    shrinks a position, so risk comes off before any goes on; then the orders that grow
    one, the largest first, [max_legs] at a time. *)

val split :
  legs:Rebalance.Leg.t list ->
  position:(Ohcamel.Types.Symbol.t -> float) ->
  max_legs:int ->
  Rebalance.Leg.t list list
(** Wave 1 is every leg {!Rebalance.grows} says shrinks the book's [position] in its name,
    omitted when there is none; then the growing legs by |qty x price| descending (ties in
    the order given), in chunks of [max_legs] (at least 1). No leg is in two waves, and no
    wave is empty. *)

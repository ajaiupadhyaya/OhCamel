(** An intraday rebalance's orders, in the waves they are sent in (design §4.3).

    A rebalance is gated on every combination of its orders that could be its fills, so it
    may have at most {!Rebalance.max_legs} of them. An intraday one with more is sent in
    waves, each gated on the book as it is when its turn comes: first the orders that
    shrink a position, so risk comes off before any goes on; then the orders that grow
    one; each group the largest first, [max_legs] at a time. *)

val split :
  legs:Rebalance.Leg.t list ->
  position:(Ohcamel.Types.Symbol.t -> float) ->
  max_legs:int ->
  Rebalance.Leg.t list list
(** First the legs {!Rebalance.grows} says shrink the book's [position] in their names,
    then the legs that grow one; each group by |qty x price| descending (ties in the order
    given), in chunks of [max_legs] (at least 1) -- so a rebalance with more than
    [max_legs] shrinking legs, a long strategy's flat signal over seven names, still goes.
    No leg is in two waves, no wave is empty, and no wave mixes shrinking and growing
    legs. *)

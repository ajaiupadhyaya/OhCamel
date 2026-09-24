(* See wire.mli: the permit's one constructor, and the one call that uses it. *)

type permit = Permit

let submit (trade : permit Venue.Trade.t) (request : Order.Request.t) =
  trade.Venue.Trade.submit Permit request

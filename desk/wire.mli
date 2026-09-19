(** The one door to the venue's submit (invariant 10's one submit site).

    [Venue.Trade.t]'s submit takes a [permit]. Its type is abstract here and nothing here
    returns one, so no module outside wire.ml can make a value of it. Both adapters,
    [Sim_venue.trade] and [Alpaca_trade.trade], build their trading half at this permit,
    so their submit is reached only through [submit] below: a call written against the
    record's field has no permit to pass and does not compile, and neither does typing an
    adapter at another permit to get one.

    That much the compiler holds. Which modules call [submit], it does not:
    test/test_rebalance.ml holds that only the order manager does, once, by failing when
    any other file in desk/ or bin/ names this module for anything but its permit type, or
    names the order manager's one submit function. *)

type permit

val submit :
  permit Venue.Trade.t -> Order.Request.t -> Venue.Submission.t Async.Deferred.t

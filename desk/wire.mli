(** The one door to the venue's submit (invariant 10's one submit site).

    [Venue.Trade.t]'s submit takes a [permit], and this is the only module that can make
    one: its type is abstract here, and nothing here returns one. So no module can call a
    venue's submit except through [submit] below -- a call written straight against the
    record's field, under any alias, has no permit to pass and does not compile. Only
    [Oms.submit_journaled] calls [submit], after the order is journaled and the switch,
    the book and the session are asked again; test/test_rebalance.ml fails if any other
    file in desk/ or bin/ so much as names this module. *)

type permit

val submit :
  permit Venue.Trade.t -> Order.Request.t -> Venue.Submission.t Async.Deferred.t

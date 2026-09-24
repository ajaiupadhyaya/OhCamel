(* Times as text, once.

   The journal stores times as RFC 3339 and Alpaca speaks it. Core prints UTC
   with a space where RFC 3339 wants a T, so the one conversion lives here and
   nowhere else.

   [local_date] is the reason this module exists at all. A session's date is
   the EXCHANGE'S date, and the exchange is in New York. Computing it from a
   UTC instant would need a timezone database, which the runtime image does
   not carry and which would be one more thing to be wrong about twice a year.
   Alpaca already writes its clock in exchange time with an offset
   (2026-09-11T16:00:00-04:00), so the date it means is the first ten
   characters of what it said.

   [new_york] is the one exception, and it is not a date the venue could have
   said. Alpaca refuses a market-on-open order sent between 09:28 and 19:00
   ET and queues one sent after 19:00 (Task 15): a rule of the exchange's
   wall clock, which moves an hour against UTC twice a year, and which no
   venue answer states. So it is read from the system's tz database, the real
   America/New_York zone and never a fixed offset -- which would put 19:00
   an hour wrong for half the year. deploy/Dockerfile installs tzdata in the
   runtime image for it. Where the zone cannot be loaded this is None, and
   the rules refuse every opening-auction order, saying why: an hour that
   cannot be told is not an hour to trade in. *)

open Core

let rfc3339 (t : Time_ns.t) : string =
  String.tr ~target:' ' ~replacement:'T' (Time_ns.to_string_utc t)

let parse (s : string) : Time_ns.t option =
  Option.try_with (fun () -> Time_ns.of_string_with_utc_offset s)

let local_date (s : string) : Date.t option =
  if String.length s < 10 then None
  else Option.try_with (fun () -> Date.of_string (String.prefix s 10))

let new_york : Timezone.t option Lazy.t =
  lazy (Option.try_with (fun () -> Timezone.find "America/New_York") |> Option.join)

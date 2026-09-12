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
   characters of what it said. *)

open Core

let rfc3339 (t : Time_ns.t) : string =
  String.tr ~target:' ' ~replacement:'T' (Time_ns.to_string_utc t)

let parse (s : string) : Time_ns.t option =
  Option.try_with (fun () -> Time_ns.of_string_with_utc_offset s)

let local_date (s : string) : Date.t option =
  if String.length s < 10 then None
  else Option.try_with (fun () -> Date.of_string (String.prefix s 10))

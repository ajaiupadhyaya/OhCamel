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

(* THE SESSION'S HOURS: how much of [from, until] fell inside a regular
   session, 09:30 to 16:00 on a weekday in [zone] (America/New_York, from
   [new_york]). The overlay's age is counted in these (Overlay.factor): an
   overlay written after Friday's close has not aged by Monday's open.

   There is no holiday table here, as there is none in sim_venue.ml: a
   holiday and an early close count as full sessions, so an age is never
   UNDER-counted -- an overlay can only be judged older than it is, and
   size down sooner, never later. Zero when [until] is not after [from].
   [stop_after] ends the count once it is passed, for a caller that only
   compares the age with a bound: past the bound, by how much is not asked. *)
let session_opens = Time_ns.Ofday.create ~hr:9 ~min:30 ()
let session_closes = Time_ns.Ofday.create ~hr:16 ()

let session_span ?stop_after ~(zone : Timezone.t) ~(from : Time_ns.t) ~(until : Time_ns.t)
    () : Time_ns.Span.t =
  if Time_ns.(until <= from) then Time_ns.Span.zero
  else
    let last = Time_ns.to_date until ~zone in
    let passed acc =
      match stop_after with Some bound -> Time_ns.Span.(acc > bound) | None -> false
    in
    let rec count day acc =
      if Date.( > ) day last || passed acc then acc
      else
        let acc =
          if not (Date.is_weekday day) then acc
          else
            let lo = Time_ns.max from (Time_ns.of_date_ofday ~zone day session_opens) in
            let hi = Time_ns.min until (Time_ns.of_date_ofday ~zone day session_closes) in
            if Time_ns.(hi > lo) then Time_ns.Span.(acc + Time_ns.diff hi lo) else acc
        in
        count (Date.add_days day 1) acc
    in
    count (Time_ns.to_date from ~zone) Time_ns.Span.zero

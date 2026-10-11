(* The overlay, enforced. See overlay.mli.

   Modelled on contract.ml: rules O1..O4 are functions of their own, applied in
   order, the first failure reported; comparisons are written positively
   ("not (x >= 0. && x <= 1.)") because every ordering against NaN is false,
   so "x < 0. || x > 1." would accept it. Unknown is None -- a missing or
   mistyped field is a parse error, never a default. *)

open Core

type t = {
  schema_version : int;
  kind : string;
  as_of : Time_ns.t;
  computed_at : string;
  sequence : int;
  exposure_scale : float;
  inputs : Yojson.Safe.t;
  reported : Yojson.Safe.t;
  raw : string;
}

type clock = { now : Time_ns.t }
type verdict = Accepted of t | Rejected of [ `O1 | `O2 | `O3 | `O4 ] * string

let parse_string raw : (t, string) Result.t =
  let open Yojson.Safe.Util in
  let number j =
    match j with
    | `Int i -> Float.of_int i
    | `Float f -> f
    | _ -> raise (Type_error ("expected a number", j))
  in
  match Yojson.Safe.from_string raw with
  | exception Yojson.Json_error msg -> Error ("not JSON: " ^ msg)
  | json -> (
      try
        let kind = member "kind" json |> to_string in
        if not (String.equal kind "overlay") then
          Error (sprintf "kind is %S, not \"overlay\"" kind)
        else
          let as_of_text = member "as_of" json |> to_string in
          match Desk_time.parse as_of_text with
          | None -> Error (sprintf "as_of %S is not an RFC 3339 instant" as_of_text)
          | Some as_of ->
              Ok
                {
                  schema_version = member "schema_version" json |> to_int;
                  kind;
                  as_of;
                  computed_at = member "computed_at" json |> to_string;
                  sequence = member "sequence" json |> to_int;
                  exposure_scale = member "exposure_scale" json |> number;
                  inputs = member "inputs" json;
                  reported = member "reported" json;
                  raw;
                }
      with
      | Type_error (msg, j) -> Error (sprintf "%s at %s" msg (Yojson.Safe.to_string j))
      | Failure msg | Invalid_argument msg -> Error msg)

(* ------------------------------------------------------------------------ *)
(* The rules, in order. *)

let o1 s =
  if s.schema_version = 1 && String.equal s.kind "overlay" then Ok ()
  else Error (`O1, sprintf "schema_version %d, kind %S" s.schema_version s.kind)

let o2 (clock : clock) ~max_age_minutes s =
  let age = Time_ns.diff clock.now s.as_of in
  if not Time_ns.Span.(age >= zero) then
    Error
      ( `O2,
        sprintf "as_of %s is after now %s" (Desk_time.rfc3339 s.as_of)
          (Desk_time.rfc3339 clock.now) )
  else if not Time_ns.Span.(age <= of_min (Float.of_int max_age_minutes)) then
    Error
      ( `O2,
        sprintf "as_of is %d minutes old; max age is %d minutes"
          (Float.to_int (Float.round_down (Time_ns.Span.to_min age)))
          max_age_minutes )
  else Ok ()

let o3 ~last_sequence s =
  if s.sequence > last_sequence then Ok ()
  else
    Error
      ( `O3,
        sprintf "sequence %d is not after the last accepted %d" s.sequence last_sequence
      )

(* Written as "not (x >= 0. && x <= 1.)": NaN fails both comparisons and is
   refused, and so is infinity (1e999 in JSON reads as one). *)
let o4 s =
  if not Float.(s.exposure_scale >= 0. && s.exposure_scale <= 1.) then
    Error (`O4, sprintf "exposure_scale %g is not in [0, 1]" s.exposure_scale)
  else Ok ()

let validate ~clock ~max_age_minutes ~last_sequence (s : t) : verdict =
  let ( >>= ) r f = match r with Ok () -> f () | Error e -> Error e in
  match
    o1 s >>= fun () ->
    o2 clock ~max_age_minutes s >>= fun () ->
    o3 ~last_sequence s >>= fun () -> o4 s
  with
  | Ok () -> Accepted s
  | Error (rule, why) -> Rejected (rule, why)

(* ------------------------------------------------------------------------ *)
(* The two numbers. *)

let finite x = Float.is_finite x

let governor ~drawdown ~dd_cap =
  match (drawdown, dd_cap) with
  | None, _ | _, None -> 1.0
  | Some dd, Some cap ->
      if not (finite dd && finite cap && Float.(cap > 0.)) then 0.0
      else if Float.(dd <= cap /. 2.) then 1.0
      else if Float.(dd < cap) then (cap -. dd) /. (cap /. 2.)
      else 0.0

let effective ~overlay ~fallback ~governor =
  let base = Option.value overlay ~default:fallback in
  let x = base *. governor in
  if not (finite base && finite governor && finite x) then 0.0
  else Float.clamp_exn x ~min:0. ~max:1.

type state = Fresh | Held of Time_ns.Span.t | Stale | Absent

let state ~clock ~received_at ~max_age_minutes =
  match received_at with
  | None -> Absent
  | Some at ->
      let age = Time_ns.diff clock.now at in
      if not Time_ns.Span.(age >= zero) then Stale
      else if Time_ns.Span.(age < of_min 1.) then Fresh
      else if Time_ns.Span.(age <= of_min (Float.of_int max_age_minutes)) then Held age
      else Stale

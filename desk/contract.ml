(* The signal contract, enforced. (Named Contract rather than Signal because
   Core.Signal -- Unix signals -- would shadow it under [open Core].)

   A signal is a claim made by the Python research layer: "at the close of
   [as_of], strategy [strategy] wanted these weights, and here is how it was
   validated". Everything temporal in it is a claim about time, and the only
   clock this module believes is the [Clock.t] the caller hands to [validate]
   -- built from bars the desk has itself recorded, never from the signal's
   own [computed_at] or from wall-clock "today". That is the whole content of
   "OCaml never trusts Python's timing, only its output" -- Python's wall
   clock, Python's idea of today, and Python's ordering of files are all
   checked against bar dates, never taken as given.

   Rules are numbered R1..R7 here (R8, the data-hash check, is deliberately
   absent -- see contract.mli). They are applied in order and the FIRST
   failure is reported, so a document that fails several is described by the
   earliest rule, which is also the most fundamental one. Tests pin that
   order.

   This module is pure: it parses JSON and evaluates rules against a clock it
   is given. It does no IO and holds no bar store of its own, so every rule
   can be tested with a hand-built document and a hand-built clock on either
   side of its boundary. *)

open Core

module Rule = struct
  type t = R1 | R2 | R3 | R4 | R5 | R6 | R7 [@@deriving sexp, compare, equal]

  let to_string = function
    | R1 -> "R1"
    | R2 -> "R2"
    | R3 -> "R3"
    | R4 -> "R4"
    | R5 -> "R5"
    | R6 -> "R6"
    | R7 -> "R7"

  let describe = function
    | R1 -> "schema_version must be 1"
    | R2 -> "strategy must be registered with the core"
    | R3 -> "as_of must not be after the core's latest bar"
    | R4 -> "as_of must be within max_age trading days of the latest bar"
    | R5 -> "sequence must exceed the last accepted sequence for the strategy"
    | R6 -> "validation.status must be pass; anything else is advisory only"
    | R7 -> "targets must be in the universe, each |w| <= 1, sum |w| <= 1"
end

type target = { symbol : Ohcamel.Types.Symbol.t; weight : float }

type validation = {
  status : string;
  gates_version : string;
  dsr : float option;
  psr : float option;
  pbo : float option;
  manifest : string option;
}

type t = {
  schema_version : int;
  strategy : string;
  params_hash : string;
  as_of : Date.t;
  computed_at : string;
  data_hash : string;
  sequence : int;
  validation : validation;
  targets : target list;
}

(* ------------------------------------------------------------------------ *)
(* The clock this module believes, as a parameter rather than something it
   builds.

   Alpha's version of this module built its own [Clock.t] from the full list
   of bar dates it had parsed (see [bars.ml] there): [of_dates] deduplicated
   and sorted them, and [age]/[latest]/[length] were queries over that list.
   That made the module correct only alongside the one process that read the
   replay file and called [of_dates] -- fine for Alpha's single core binary,
   wrong for a module that must stay pure here. So [Clock.t] here holds
   exactly the three facts R3 and R4 read -- the earliest and latest bar
   dates, and a function counting how many recorded bars fall after a given
   date -- and nothing else. Task 14 builds it from the journal's [sessions] table, whose
   rows are the closes the desk has recorded; this module never needs to know
   that table, or a raw date list, exists.

   [earliest_bar] is the third fact this module needs: without it, an [as_of]
   before every session the desk has ever recorded reads as "0 bars old" --
   [bars_after] counts recorded bars after a date it never saw either, so a
   desk three sessions into its life would call a year-old signal fresh.
   [create] rejects the two shapes that cannot come from a real intake: an
   [earliest_bar] after [latest_bar], and a [latest_bar] that is not itself
   the newest date [bars_after] counts. *)
module Clock = struct
  type t = { earliest_bar : Date.t; latest_bar : Date.t; bars_after : Date.t -> int }

  let create ~earliest_bar ~latest_bar ~bars_after =
    if Date.( > ) earliest_bar latest_bar then
      failwith
        (sprintf "Clock.create: earliest_bar %s is after latest_bar %s"
           (Date.to_string earliest_bar) (Date.to_string latest_bar))
    else if bars_after latest_bar <> 0 then
      failwith
        (sprintf
           "Clock.create: bars_after latest_bar = %d, not 0 -- %s is not the newest date \
            bars_after counts"
           (bars_after latest_bar) (Date.to_string latest_bar))
    else { earliest_bar; latest_bar; bars_after }
end

(* What the core has been configured to accept: which strategies exist, and
   the last sequence number it accepted from each. A3 keeps this in memory
   for the duration of one command; a later phase persists it. *)
type registry = { strategies : string list; last_sequence : (string * int) list }
type universe = Ohcamel.Types.Symbol.t list
type verdict = Accepted of t | Rejected of Rule.t * string

(* ------------------------------------------------------------------------ *)
(* Parsing. Lenient about nothing: a missing or mistyped required field is a
   parse error, not a default. Numbers that JSON writes as integers (a weight
   of 1) are accepted where a float is expected, because that is what any
   JSON serialiser emits for 1.0. *)

let parse (json : Yojson.Safe.t) : (t, string) Result.t =
  let open Yojson.Safe.Util in
  let number j =
    match j with
    | `Int i -> Float.of_int i
    | `Float f -> f
    | _ -> raise (Type_error ("expected a number", j))
  in
  let opt_number j = match j with `Null -> None | j -> Some (number j) in
  let opt_string j = match j with `Null -> None | j -> Some (to_string j) in
  try
    let validation =
      let v = member "validation" json in
      {
        status = member "status" v |> to_string;
        gates_version = member "gates_version" v |> to_string;
        dsr = member "dsr" v |> opt_number;
        psr = member "psr" v |> opt_number;
        pbo = member "pbo" v |> opt_number;
        manifest = member "manifest" v |> opt_string;
      }
    in
    let targets =
      member "targets" json |> to_list
      |> List.map ~f:(fun t ->
          {
            symbol = member "symbol" t |> to_string |> Ohcamel.Types.Symbol.of_string;
            weight = member "weight" t |> number;
          })
    in
    Ok
      {
        schema_version = member "schema_version" json |> to_int;
        strategy = member "strategy" json |> to_string;
        params_hash = member "params_hash" json |> to_string;
        as_of = member "as_of" json |> to_string |> Date.of_string;
        computed_at = member "computed_at" json |> to_string;
        data_hash = member "data_hash" json |> to_string;
        sequence = member "sequence" json |> to_int;
        validation;
        targets;
      }
  with
  | Type_error (msg, j) -> Error (sprintf "%s at %s" msg (Yojson.Safe.to_string j))
  | Failure msg | Invalid_argument msg -> Error msg

let parse_string s =
  match Yojson.Safe.from_string s with
  | json -> parse json
  | exception Yojson.Json_error msg -> Error ("not JSON: " ^ msg)

(* ------------------------------------------------------------------------ *)
(* The rules, in order. Each is its own function so a test can name it, and
   [validate] threads them so the first failure wins. *)

let r1 s =
  if s.schema_version = 1 then Ok ()
  else Error (Rule.R1, sprintf "got %d" s.schema_version)

let r2 registry s =
  if List.mem registry.strategies s.strategy ~equal:String.equal then Ok ()
  else Error (Rule.R2, sprintf "%S is not registered" s.strategy)

let r3 (clock : Clock.t) s =
  if Date.( > ) s.as_of clock.latest_bar then
    Error
      ( Rule.R3,
        sprintf "as_of %s is after the latest bar %s" (Date.to_string s.as_of)
          (Date.to_string clock.latest_bar) )
  else Ok ()

let r4 (clock : Clock.t) ~max_age s =
  if Date.( < ) s.as_of clock.earliest_bar then
    Error
      ( Rule.R4,
        sprintf "as_of %s is older than the clock's history (earliest recorded bar is %s)"
          (Date.to_string s.as_of)
          (Date.to_string clock.earliest_bar) )
  else
    let age = clock.bars_after s.as_of in
    if age > max_age then
      Error
        ( Rule.R4,
          sprintf "as_of %s is %d bars old; max_age is %d" (Date.to_string s.as_of) age
            max_age )
    else Ok ()

let r5 registry s =
  let last =
    List.Assoc.find registry.last_sequence ~equal:String.equal s.strategy
    |> Option.value ~default:0
  in
  if s.sequence > last then Ok ()
  else
    Error
      (Rule.R5, sprintf "sequence %d is not after the last accepted %d" s.sequence last)

let r6 s =
  if String.equal s.validation.status "pass" then Ok ()
  else
    Error
      ( Rule.R6,
        sprintf "validation.status is %S -- advisory, not traded" s.validation.status )

let symbol_equal = Ohcamel.Types.Symbol.equal

let r7 (universe : universe) s =
  let sum_abs = List.sum (module Float) s.targets ~f:(fun t -> Float.abs t.weight) in
  let outside =
    List.find s.targets ~f:(fun t -> not (List.mem universe t.symbol ~equal:symbol_equal))
  in
  (* Both bounds are written as "not (x <= bound)" rather than "x > bound",
     because every ordering comparison against NaN is false: "NaN > 1" is
     false and would accept it, while "not (NaN <= 1)" is true and rejects
     it. Either check alone rejects a NaN weight (the per-weight one first);
     an infinite weight fails the per-weight check too. |w| <= 1 is R7's
     rule as interface/README.md states it, and NaN is not <= anything. *)
  let over = List.find s.targets ~f:(fun t -> not Float.(abs t.weight <= 1.0)) in
  match (outside, over) with
  | Some t, _ ->
      Error
        ( Rule.R7,
          sprintf "%s is not in the universe" (Ohcamel.Types.Symbol.to_string t.symbol) )
  | None, Some t ->
      Error
        ( Rule.R7,
          sprintf "%s has |weight| %g > 1"
            (Ohcamel.Types.Symbol.to_string t.symbol)
            (Float.abs t.weight) )
  | None, None ->
      if not Float.(sum_abs <= 1.0 +. 1e-9) then
        Error (Rule.R7, sprintf "sum |weight| = %g > 1" sum_abs)
      else Ok ()

let validate ~clock ~registry ~universe ~max_age (s : t) : verdict =
  let ( >>= ) r f = match r with Ok () -> f () | Error e -> Error e in
  match
    r1 s >>= fun () ->
    r2 registry s >>= fun () ->
    r3 clock s >>= fun () ->
    r4 clock ~max_age s >>= fun () ->
    r5 registry s >>= fun () ->
    r6 s >>= fun () -> r7 universe s
  with
  | Ok () -> Accepted s
  | Error (rule, why) -> Rejected (rule, why)

let verdict_to_string = function
  | Accepted s ->
      sprintf "ACCEPT %s seq=%d as_of=%s targets=%d" s.strategy s.sequence
        (Date.to_string s.as_of) (List.length s.targets)
  | Rejected (rule, why) ->
      sprintf "REJECT %s: %s (%s)" (Rule.to_string rule) why (Rule.describe rule)

(* The nine-ETF universe from the spec, as the default. A caller with a
   different book passes its own. *)
let default_universe : universe =
  List.map ~f:Ohcamel.Types.Symbol.of_string
    [ "SPY"; "QQQ"; "IWM"; "TLT"; "IEF"; "GLD"; "XLE"; "XLF"; "XLK" ]

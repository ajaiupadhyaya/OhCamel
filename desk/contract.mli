(** The signal contract, enforced. (Named [Contract] rather than [Signal] because
    [Core.Signal] -- Unix signals -- would shadow it under [open Core].)

    A signal is a claim made by the Python research layer: "at the close of [as_of],
    strategy [strategy] wanted these weights, and here is how it was validated".
    Everything temporal in it is a claim about time, and the only clock this module
    believes is {!Clock.t}, built by the caller from bars the desk has itself recorded --
    never from the signal's own [computed_at] or from wall-clock "today". That is the
    whole content of "OCaml never trusts Python's timing, only its output".

    This module is pure: it parses JSON and evaluates rules against a [Clock.t] handed to
    it. It does no IO and reads no bar store itself, so every rule can be tested with a
    hand-built document and a hand-built clock. Task 13 builds the real [Clock.t] from the
    journal's [sessions] table, whose rows are the closes the desk has recorded; this
    module never knows that table exists. *)

open Core

(** The only clock this module believes: not a wall clock and not a calendar, but two
    facts the caller derives from the bars it holds. [t] is opaque so that neither fact
    can be faked separately from the other, and so a future caller cannot reach past the
    interface for a raw date list this module no longer keeps (see the note on Alpha's
    [Clock.of_dates] below). *)
module Clock : sig
  type t

  val create : latest_bar:Date.t -> bars_after:(Date.t -> int) -> t
  (** [latest_bar]: the newest trading date the desk holds a close for -- what R3 compares
      [as_of] against. [bars_after d]: how many of the desk's trading dates fall strictly
      after [d] -- what R4 calls a signal's age, so age is counted in bars actually seen,
      not calendar days. A signal dated on [latest_bar] has age 0.

      Unlike Alpha's [core/lib/contract.ml], which built its [Clock.t] from the full list
      of bar dates it had parsed itself (an [of_dates], exposing [age] and [length] as
      derived queries over that list), this module takes only the two projections R3 and
      R4 actually read. The full list belonged to Alpha's [bars.ml], one process reading
      one replay file; the desk's bars live in a SQLite table another module owns, and
      handing this pure module the whole table so it could recompute what the caller
      already knows would be the module reaching for state, not being given data. *)
end

type target = { symbol : Ohcamel.Types.Symbol.t; weight : float }
(** A single portfolio-weight target inside a signal's [targets] array. *)

type validation = {
  status : string;
  gates_version : string;
  dsr : float option;
  psr : float option;
  pbo : float option;
  manifest : string option;
}
(** The [validation] block: written by the code that ran the gate battery, from its
    manifest, never by hand (see [interface/README.md]). *)

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
(** A parsed signal document, one JSON object as [interface/signal.schema.json] shapes it.
*)

type registry = { strategies : string list; last_sequence : (string * int) list }
(** What the desk has been configured to accept: which strategies exist, and the last
    sequence number it accepted from each (R2, R5). *)

type universe = Ohcamel.Types.Symbol.t list
(** The symbols a signal's targets are allowed to name (R7). *)

(** The rules a signal is judged against, numbered as [interface/README.md] numbers them
    so a rejection and a test can both name one. *)
module Rule : sig
  type t = R1 | R2 | R3 | R4 | R5 | R6 | R7 [@@deriving sexp, compare, equal]

  val to_string : t -> string
  val describe : t -> string
end

(** The result of judging one signal: accepted whole, or rejected at the first rule it
    failed. Rules are applied in order R1 through R7, and only the first failure is
    reported -- so a document that fails several rules is described by the earliest, which
    is also the most fundamental one. R8, the [data_hash] check, is deliberately absent:
    [interface/README.md] §3.12 (and this project's ruling 3) hold it back until its hash
    recipe stops depending on how two languages print a float. Nothing here reads
    [data_hash]; R7 is the last rule applied. Task 14 depends on this order: a first
    failure of R6 means R1 through R5 passed and R7 was never reached, which is exactly
    what makes an R6 rejection "advisory, and everything else about the signal is
    otherwise sound". *)
type verdict = Accepted of t | Rejected of Rule.t * string

val parse : Yojson.Safe.t -> (t, string) Result.t
(** Lenient about nothing: a missing or mistyped required field is a parse error, not a
    default. A weight or hash JSON writes as an integer is accepted where the schema wants
    a number, because that is what any JSON serialiser emits for a whole value. *)

val parse_string : string -> (t, string) Result.t

val validate :
  clock:Clock.t -> registry:registry -> universe:universe -> max_age:int -> t -> verdict
(** Applies R1 through R7 in order against [clock], [registry], [universe] and [max_age]
    (R4's default is 3 trading days; the caller states it explicitly here rather than this
    module assuming a default it cannot justify on its own). *)

val verdict_to_string : verdict -> string

val default_universe : universe
(** The nine-ETF universe from the spec. A caller with a different book passes its own to
    {!validate}. *)

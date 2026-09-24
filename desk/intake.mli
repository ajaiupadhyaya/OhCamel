(** The signal intake: signal files in, judgements out, and an accepted signal on a live
    strategy handed to the order manager as one rebalance (desk/intake.ml's header says
    how, and why every doubt refuses).

    The interface exists for one promise above all: {!Accepted.t} is private. Only this
    module can build one, and it builds one only for a document it judged accepted in the
    pass that is running -- so nothing outside it can hand {!size} a row read back from
    the journal, and nothing inside it does. *)

open Core
open Async
open Ohcamel.Types

val env_var : string
(** OHCAMEL_SIGNALS_DIR. *)

val r8_sentence : string
val max_file_bytes : int

val weekdays_after : as_of:Date.t -> latest:Date.t -> int
(** Weekdays d with [as_of] < d <= [latest]. *)

val directory : string option -> (string option, string) Result.t
(** The environment variable's value, checked: unset is no intake; set is a readable
    directory or a refusal naming the variable. *)

type ident

(** What lstat says of a name, before anything is opened. *)
type look =
  | Regular of ident
  | Refused of string  (** recorded once in signal_files *)
  | Vanished  (** listed, then removed before it was looked at *)
  | Io_error of string  (** tried again next pass, never recorded *)

val look : string -> look

type read =
  | Text of string
  | Changed  (** not the file lstat saw, or grown past the bound *)
  | Gone
  | Unreadable of string

val read : ?between:(unit -> unit) -> string -> ident -> read
(** One file, no further than a signal can be long, and only if it is still the file
    [look] saw. [between] runs between the two, for the test that swaps a symlink in. *)

type t

val create :
  journal:Journal.t ->
  dir:string ->
  strategies:Ohcamel.Config.Book.Signals_spec.Strategy.t list ->
  universe:Symbol.t list ->
  on_event:(string -> unit) ->
  now:(unit -> Time_ns.t) ->
  t

val deferred : t -> int
val last_pass : t -> Time_ns.t option

type status = Running of t | Off of string

val setup :
  journal:Journal.t ->
  universe:Symbol.t list ->
  signals:Ohcamel.Config.Book.Signals_spec.t option ->
  dir:string option ->
  on_event:(string -> unit) ->
  now:(unit -> Time_ns.t) ->
  status

val demo_status : status

val pending : strategy:string -> sequence:int -> string
(** The sentence an accepted judgement's rebalance column holds until its outcome replaces
    it. *)

(** A document the running pass judged accepted, with its strategy: readable anywhere,
    built only here. *)
module Accepted : sig
  type t = private {
    strategy : Ohcamel.Config.Book.Signals_spec.Strategy.t;
    doc : Contract.t;
  }
end

val judge_pass : t -> Accepted.t list
(** One pass: every judgement recorded, and the documents judged accepted returned. *)

val pass : t -> unit
(** [judge_pass], its accepted documents dropped: a pass that sizes nothing. *)

val size : t -> oms:Oms.t option -> Accepted.t list -> unit Deferred.t
(** Each accepted document planned, proposed as one rebalance, and its outcome written
    over the pending sentence. Only the highest sequence of a strategy is sized. Never
    raises. *)

val run : ?time_source:Time_source.t -> ?oms:Oms.t -> t -> unit Deferred.t
(** A pass at once and then once a minute, each pass's accepted documents sized before the
    minute's wait. *)

val research_json :
  journal:Journal.t ->
  strategies:Ohcamel.Config.Book.Signals_spec.Strategy.t list ->
  status ->
  Yojson.Safe.t

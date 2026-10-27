(** The signal intake: signal files in, judgements out, and an accepted signal on a live
    strategy -- or on an incubation strategy, on the paper venue only -- handed to the
    order manager as one rebalance (desk/intake.ml's header says how, and why every doubt
    refuses).

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

(** What the effective scale is read from beyond the journal (design §4.3): the book's
    overlay numbers ([overlay_max_age_minutes], [overlay_fallback], [overlay_band]), the
    venue's own clock as the desk last read it advanced by the host's time since
    (Desk.venue_now; None only before any reading has ever arrived), the graph's drawdown
    as a fraction, and the book's drawdown cap. It also keeps the last governor computed
    from a drawdown that could be read: one the graph cannot read (the thunk raises, or
    answers a non-finite number) holds that value, 1 before any, and never governs to 0.
    One gauge is shared by the intake and the desk. *)
module Gauge : sig
  type t

  val create :
    spec:Ohcamel.Config.Book.Desk_spec.t ->
    venue_now:(unit -> Time_ns.t option) ->
    drawdown:(unit -> float option) ->
    dd_cap:float option ->
    t

  val none : unit -> t
  (** The default book's numbers, no clock, no drawdown, no cap. *)

  val spec : t -> Ohcamel.Config.Book.Desk_spec.t
  val dd_cap : t -> float option

  val dd_cap_of_limits : Limit.t list -> float option
  (** The book's Portfolio-scoped [Max_drawdown] threshold, the tightest of several; None
      when it has none, and the governor is then 1 ([setup] says so once). *)
end

(** The effective scale and what it was computed from, as [/api/desk] carries it. *)
module Reading : sig
  type t = {
    exposure_scale : float;
        (** What every sizing multiplies by: the overlay's scale when fresh or held, the
            book's fallback when stale, 1 when absent; times the governor. *)
    factor : float;
        (** What the governor multiplies: the overlay's scale, the fallback, or 1, by the
            state. A sizing records it beside the governor. *)
    governor : float;
    governor_cap : float option;  (** The book's drawdown cap; None governs at 1. *)
    governor_held : bool;  (** The drawdown could not be read: the last governor holds. *)
    overlay : float option;  (** The latest accepted overlay's scale, whatever its age. *)
    as_of : string option;
    age_s : int option;  (** Seconds since it was received, on the venue clock. *)
    state : Overlay.state;
        (** Absent only when no overlay was ever accepted; Stale too before any venue
            reading has arrived, with [reason]. *)
    reason : string option;  (** "no venue clock yet", or None. *)
  }
end

val state_word : Overlay.state -> string
(** "fresh", "held", "stale" or "absent": the wire's word. *)

val reading : latest:(int * string * float * string) option -> Gauge.t -> Reading.t
(** [latest] as Journal.latest_overlay answers it. Updates the gauge's held governor when
    the drawdown is read. *)

val reading_fields : Reading.t -> (string * Yojson.Safe.t) list
(** ["exposure_scale"], ["governor"] and ["overlay"] (value, as_of, age_s, state --
    "fresh", "held", "stale" or "absent" -- reason, governor_cap), in that order. *)

val overlay_zero_line : sequence:int -> string
(** The alert line written once when an overlay of zero is accepted: "overlay <n> says
    zero exposure: every strategy that adjusts intraday is flattened". *)

type t

val create :
  ?gauge:Gauge.t ->
  journal:Journal.t ->
  dir:string ->
  strategies:Ohcamel.Config.Book.Signals_spec.Strategy.t list ->
  universe:Symbol.t list ->
  on_event:(string -> unit) ->
  now:(unit -> Time_ns.t) ->
  unit ->
  t

val deferred : t -> int
val last_pass : t -> Time_ns.t option

type status = Running of t | Off of string

val setup :
  gauge:Gauge.t ->
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
    it. While it stands the judgement is tried again each pass ({!collect_pending}). *)

(** A document the running pass judged accepted, with its strategy: readable anywhere,
    built only here. *)
module Accepted : sig
  type t = private {
    strategy : Ohcamel.Config.Book.Signals_spec.Strategy.t;
    doc : Contract.t;
  }
end

val judge_pass : t -> Accepted.t list
(** One pass: every judgement recorded, and the documents judged accepted returned. Files
    named [overlay-*.json] are judged first, by Overlay (O1-O4) against the gauge's venue
    clock, each sequence once, and recorded in [overlays]; never by the contract. *)

val effective_scale : t -> float
(** [Overlay.effective ~overlay ~fallback ~governor]: the latest accepted overlay's scale
    when it is fresh or held, the book's fallback when stale, 1 when no overlay was ever
    accepted; times the drawdown governor. *)

val current_reading : t -> Reading.t

val pass : t -> unit
(** [judge_pass], its accepted documents dropped: a pass that sizes nothing. *)

val size :
  ?reading:(unit -> Reading.t) ->
  t ->
  oms:Oms.t option ->
  scale:(unit -> float) ->
  Accepted.t list ->
  unit Deferred.t
(** Each accepted document planned at the effective [scale] (asked once per rebalance), or
    at [reading]'s when it is given ([run] passes {!current_reading}, and the sizing row
    then records its factor and governor), proposed as one rebalance, and its outcome
    written over the pending sentence -- unless the rebalance was refused for timing alone
    (Oms.Rebalance_outcome.Not_now), when the sentence stays pending and the refusal is
    said once. Its sizing is recorded only when every order went (A-I5). Only the highest
    sequence of a strategy in the list is sized; an incubation strategy only on a paper
    venue. Never raises. *)

val collect_pending : t -> Accepted.t list
(** The retry set: every accepted judgement whose rebalance still holds its pending
    sentence, read back from the journal -- each closed instead, its reason written over
    the pending sentence, when its strategy is gone or no longer at the tier it was judged
    at, a later document of the strategy was accepted, it aged past max_age sessions, or
    orders were journaled under its source (a crash while proposing it). *)

val adjust : t -> oms:Oms.t option -> unit Deferred.t
(** For each registered strategy that adjusts intraday, is not stopped, and has a latest
    accepted document, that document is re-sized in the intraday mode, its sizing
    journaled with mode "adjust", when: it has no sizing for that document and holds a
    position of its own; or the reading is fresh, held or absent and the effective scale
    is more than [overlay_band] from the one it was last sized at (the sizing's scale over
    the incubation ratio recorded with it), or that ratio changed, or the accepted overlay
    says exactly 0 and the last scale was not; or the reading is stale and the governor
    moved more than the band from the last sizing's -- then at that sizing's overlay
    factor times the governor now, noted "governor". A stale reading otherwise holds every
    last sizing, said once a spell.

    Before any of that, THE COMPLETION: when orders of that document ended with shares
    unfilled after the strategy's latest sizing ({!Journal.source_orders_unfilled}) --
    expired at the opening auction, cancelled at a wave's timeout, refused -- the strategy
    is sized again from the document whatever the band, its row "complete" and noted
    "completion": at the reading, or at the last sizing's factor times the governor when
    the reading is stale. At most three times a session for one strategy, counted when
    orders reached the venue, and said once a session when the count is spent.

    At most eight strategies at once. A pass that finds the last one still running skips,
    said once for the whole overlap. Never raises. *)

val run : ?time_source:Time_source.t -> ?oms:Oms.t -> t -> unit Deferred.t
(** A pass at once and then once a minute; after each, the retry set ({!collect_pending})
    sized at the current reading before the minute's wait -- only once the venue's clock
    has answered: before it nothing is sized and every judgement waits, pending -- then
    the adjust pass started, detached. *)

val absent_reading_fields : (string * Yojson.Safe.t) list
(** [reading_fields]' shape for a desk with no gauge: nulls and "absent". *)

val research_json :
  ?scale:(string * Yojson.Safe.t) list ->
  journal:Journal.t ->
  strategies:Ohcamel.Config.Book.Signals_spec.Strategy.t list ->
  status ->
  Yojson.Safe.t

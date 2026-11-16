(** The overlay: the research layer's exposure scale, enforced.

    An overlay is a second document kind beside the signal ({!Contract}): one number in
    [0, 1] that multiplies every incubation and live target, with the inputs it came from
    and figures the page shows and the desk does not act on. It is told apart from a
    signal by ["kind": "overlay"], and carries no targets and no strategy, so the signal's
    R2, R6 and R7 do not apply. The document and its rules O1 to O4 are specified in
    [interface/README.md] and [interface/overlay.schema.json].

    This module is pure: it parses one document, judges it against a clock it is handed,
    and computes two numbers. It does no IO, reads no journal and no graph. As in
    {!Contract}, the only clock believed is the one the caller passes -- never the
    document's own [computed_at]. *)

open Core

type t = {
  schema_version : int;
  kind : string;
  as_of : Time_ns.t;
  computed_at : string;
      (** Informational and kept as written: a [computed_at] that is not an instant does
          not refuse the document, because no rule reads it. *)
  sequence : int;
  exposure_scale : float;
      (** Not yet judged: a parsed document may carry a scale O4 will refuse. *)
  inputs : Yojson.Safe.t;
  reported : Yojson.Safe.t;
  raw : string;  (** The document exactly as received, for the journal. *)
}

val parse_string : string -> (t, string) Result.t
(** Lenient about nothing: a missing or mistyped required field is a parse error, never a
    default. A whole-number [exposure_scale] JSON writes as an integer is accepted. A
    [kind] other than ["overlay"] (a signal, say) is refused, as is an [as_of] that is not
    an RFC 3339 instant. A non-finite [exposure_scale] (JSON [1e999] reads as infinity)
    parses and is left for O4 to refuse, so the rejection is named by rule. *)

type clock = { now : Time_ns.t }
(** The venue clock's instant, handed in by the caller. *)

type verdict =
  | Accepted of t
  | Rejected of [ `O1 | `O2 | `O3 | `O4 ] * string
      (** Accepted whole, or rejected at the first rule failed. Rules apply in order O1 to
          O4 and only the first failure is reported. *)

val validate : clock:clock -> max_age_minutes:int -> last_sequence:int -> t -> verdict
(** O1: [schema_version] is 1 and [kind] is ["overlay"]. O2: [as_of] is not after
    [clock.now] and [clock.now - as_of] is at most [max_age_minutes]. O3: [sequence]
    exceeds [last_sequence]. O4: [exposure_scale] is finite and in [0, 1]. *)

val governor : drawdown:float option -> dd_cap:float option -> float
(** The drawdown governor: a size multiplier in [0, 1]. [drawdown] is the graph's drawdown
    as a fraction (0.015 is 1.5 %); [dd_cap] the book's [Max_drawdown] limit as a
    fraction. No cap -> [1.0] (the book names nothing to govern by). Otherwise [1.0] while
    [drawdown <= dd_cap / 2] (including a negative drawdown, equity above its peak),
    falling linearly as [(dd_cap - drawdown) / (dd_cap / 2)] between half the cap and the
    cap, and [0.0] at and beyond the cap. A figure that exists but cannot be used -- a
    non-finite drawdown or cap, or a cap that is not positive -- gives [0.0]. A [None]
    drawdown beside a cap answers [1.0] here, but the desk never asks it: a drawdown it
    cannot read is governed by {!unread_governor} instead (Intake.reading). *)

val unread_governor : fallback:float -> last:float option -> float
(** The governor while the drawdown cannot be read -- the thunk raises, answers a
    non-finite number or nothing -- including the first read after a restart: the book's
    [overlay_fallback], or the [last] governor read when that is smaller. Unknown is not
    permission: it never sizes above the fallback, nor above what the last readable
    drawdown allowed (a book at its cap stays at 0). A non-finite result gives [0.0]. *)

val within : age:Time_ns.Span.t option -> max_age:Time_ns.Span.t -> bool
(** [age] is known, not negative, and at most [max_age]. *)

val factor :
  overlay:float option ->
  age:Time_ns.Span.t option ->
  max_age:Time_ns.Span.t ->
  fallback:float ->
  float
(** What the governor multiplies. [overlay] is the factor the caller would size at -- the
    latest accepted overlay's scale, or the factor a held sizing used -- and [age] how
    long the overlay behind it has stood, counted in session hours
    ({!Desk_time.session_span}). While [age] is known, not negative and at most [max_age]
    (the book's [overlay_max_age]), [overlay] itself; otherwise -- older, or of unknown
    age -- the smaller of [overlay] and [fallback]: the fallback is a ceiling on what an
    overlay that no longer stands may size at, never a floor that buys. No overlay:
    [fallback]. *)

val effective :
  overlay:float option ->
  age:Time_ns.Span.t option ->
  max_age:Time_ns.Span.t ->
  fallback:float ->
  governor:float ->
  float
(** [factor ~overlay ~age ~max_age ~fallback * governor], clamped to [0, 1]: older than
    [max_age] session hours, or of unknown age, [fallback x governor] (or less, as
    {!factor} says). When no overlay was ever accepted ([Absent]) the desk passes
    [Some 1.0] at age zero: the book sizes as it did before overlays existed, times the
    governor (Intake.reading). A non-finite input or product gives [0.0]. *)

(** How the last overlay received stands. *)
type state =
  | Fresh  (** Received less than one minute ago: the intake's pass interval. *)
  | Held of Time_ns.Span.t
      (** At least one minute old and no older than [max_age_minutes]: its last value
          still stands, and the span is how long it has been held. *)
  | Stale  (** Older than [max_age_minutes]: the book's fallback applies. *)
  | Absent  (** Nothing received. *)

val state : clock:clock -> received_at:Time_ns.t option -> max_age_minutes:int -> state
(** [received_at] is when the desk received the overlay, not its [as_of]. A receipt dated
    after [clock.now] cannot be aged and is [Stale] (fail closed). *)

(** The switch the desk obeys (design §3.8); halt.ml's header says why each state is what
    it is.

    [t] is abstract so that what lifts each state is a fact of this interface, not a
    convention: a hand's halt and a limit's trip are lifted only by [reset] (and, on the
    demo only, a limit's trip by [tick]); the engine's stop by nothing here at all -- only
    a new [t], which is a restart. *)

open Core

(** Where a limit's trip comes from: the kernel's alerts, or closures a test holds. *)
module Source : sig
  type t = {
    tripped : unit -> (string * Time_ns.t) option;
    firing : unit -> string list;
    reset : unit -> unit;
  }

  val none : t
  (** Never trips; a hand can still halt and the engine can still stop. *)

  val of_alerts : Ohcamel.Alerts.t -> t
end

module State : sig
  type t =
    | Clear
    | Tripped of { limit : string; at : Time_ns.t }
    | Halted of { why : string; at : Time_ns.t }
    | Stopped of { why : string; at : Time_ns.t }

  val name : t -> string
  (** "clear", "tripped", "halted" or "stopped": the frame's [kill_switch]. *)
end

type t

val create : ?auto_reset_after:Time_ns.Span.t -> Source.t -> t
(** [auto_reset_after] is passed only by the demo. *)

val state : t -> State.t
(** Stopped outranks halted, which outranks tripped. *)

val reason : t -> string option
(** Why new orders are refused, or [None] when they are not. *)

val halt : t -> why:string -> at:Time_ns.t -> unit
(** A halt by hand. A second press does not overwrite the first. *)

val stop : t -> why:string -> at:Time_ns.t -> unit
(** The engine's stop. A second stop does not overwrite the first, and nothing in this
    interface clears it. *)

val reset : t -> unit
(** Lifts a hand's halt and a limit's trip; changes nothing while stopped. *)

val tick : t -> now:Time_ns.t -> bool
(** The demo's clock: resets a limit's trip once the limit has been clear for
    [auto_reset_after], and says whether it did. Never a hand's halt or the engine's stop.
*)

val to_json : t -> now:Time_ns.t -> Yojson.Safe.t
(** The switch whole, for /api/desk and the kill routes. *)

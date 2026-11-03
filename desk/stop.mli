(** INC-01's stop rules, decided at the close: stop.ml's header says what fires and why
    nothing un-stops on its own. *)

open Core
module Spec := Ohcamel.Config.Book.Signals_spec

val check :
  spec:Spec.Stop.t ->
  record:Record.summary ->
  sessions:int ->
  [ `Continue | `Stop of string * string ]
(** [`Stop (rule, sentence)], the rule being ["drawdown"] or ["sessions"]. *)

val sessions :
  journal:Journal.t ->
  zone:Time_float.Zone.t ->
  strategy:string ->
  since:Date.t option ->
  Record.session list
(** The strategy's record from the journal, from its first fill, or from [since]. *)

val evaluate :
  journal:Journal.t ->
  strategies:Spec.Strategy.t list ->
  zone:Time_float.Zone.t ->
  at:Time_ns.t ->
  on_event:(string -> unit) ->
  string list
(** Stops every strategy past a rule that is not stopped already, and names them. *)

val reinstate :
  journal:Journal.t ->
  zone:Time_float.Zone.t ->
  strategies:Spec.Strategy.t list ->
  on_event:(string -> unit) ->
  unit
(** At startup: a [(reinstated DATE)] later than a strategy's stop re-activates it. *)

(* The journal's interface: what the rest of the desk may do to the record.

   Every write goes through a function here, and so through one transaction
   and one step of the version counter. Invariant 10 -- the journal before the
   wire -- is a promise about order writes, and it holds only if no caller can
   reach the database around them. Before this file a caller could, through
   the record's [db] field (A1's final review, M8). The two tests that need
   the handle itself reach it through [For_testing], whose name says what it
   is for. *)

open Core
open Ohcamel.Types

type t

val schema_version : int

val open_ : path:string -> t Or_error.t
(** Opens, or creates, the journal at [path] (":memory:" for one that ends with the
    process). An error names the file and what failed, and leaves no handle open behind
    it. A file journal that SQLite would not put in WAL mode is refused. *)

val close : t -> unit
val location : t -> string

val version : t -> int
(** Moves once per committed transaction: the page's signal that the record changed. *)

module Session : sig
  type t = {
    date : Date.t;
    equity_close : float;
    cash_close : float;
    gross_close : float;
    net_close : float;
    recorded_at : Time_ns.t;
  }
  [@@deriving sexp_of, compare, equal]
end

val record_session : t -> Session.t -> unit
val sessions : t -> Session.t list
val session_count : t -> int

val recent_sessions : t -> limit:int -> Session.t list
(** The newest [limit], oldest first. *)

val session : t -> Date.t -> Session.t option

module Mark : sig
  type t = { date : Date.t; symbol : Symbol.t; close : float; qty : float }
  [@@deriving sexp_of, compare, equal]
end

val record_marks : t -> Mark.t list -> unit
val marks : t -> Date.t -> Mark.t list

module Forecast : sig
  type t = {
    date : Date.t;
    estimator : string;
    confidence : float;
    var_fraction : float option;
    var_notional : float option;
    es_notional : float option;
  }
  [@@deriving sexp_of, compare, equal]
end

val record_forecasts : t -> Forecast.t list -> unit
val forecasts : t -> Forecast.t list

val latest_forecasts : t -> Forecast.t list
(** The latest date's forecasts, by estimator. *)

module Alert : sig
  type t = { at : Time_ns.t; kind : string; limit_name : string; line : string }
  [@@deriving sexp_of, compare, equal]
end

val record_alert : t -> Alert.t -> unit
val recent_alerts : t -> limit:int -> Alert.t list

(** A journaled order, rebuilt: the request and state from its row, the filled quantity,
    notional and executions from its fills, the reason from its latest event that carries
    one. *)
module Order_row : sig
  type t = {
    order : Order.t;
    source : string;
    decision_price : Price.t;
    arrival : (Price.t * Price.t) option;
    verdict : Yojson.Safe.t;
    created_at : Time_ns.t;
    updated_at : Time_ns.t;
  }
end

(** A fill, with its order's decision price and arrival quote joined in. *)
module Fill_row : sig
  type t = {
    client_order_id : Ids.Client_order_id.t;
    symbol : Symbol.t;
    side : Order.Side.t;
    fill : Order.Fill.t;
    decision_price : Price.t;
    arrival : (Price.t * Price.t) option;
  }
end

val insert_order :
  t ->
  Order.t ->
  source:string ->
  decision_price:Price.t ->
  arrival:(Price.t * Price.t) option ->
  verdict:Yojson.Safe.t ->
  at:Time_ns.t ->
  unit
(** One transaction: the orders row and its [created] event. The order manager calls this
    before the request that submits the order is sent. *)

val update_order :
  t ->
  Order.t ->
  event:Order.Event.t ->
  anomaly:Order.Anomaly.t option ->
  at:Time_ns.t ->
  unit
(** One transaction: the row's state, venue id, filled quantity and average price, and one
    order_events row. *)

val record_fill : t -> Order.t -> Order.Fill.t -> bool
(** [INSERT OR IGNORE]: true when the execution id is new. *)

val load_order : t -> Ids.Client_order_id.t -> Order_row.t option

val open_orders : t -> Order_row.t list
(** Non-terminal states, oldest first. *)

val recent_orders : t -> limit:int -> Order_row.t list
(** Newest first. *)

val recent_fills : t -> limit:int -> Fill_row.t list
(** Newest first. *)

module For_testing : sig
  val db : t -> Sqlite3.db
  (** The raw handle, for a test that must make SQLite fail on purpose -- a trigger, a
      deferred foreign key. Nothing in desk/ or bin/ calls it. *)

  val write : t -> what:string -> (unit -> unit) -> unit
  val run : t -> what:string -> string -> Sqlite3.Data.t list -> unit

  val journal_mode : t -> string
  (** SQLite's answer to PRAGMA journal_mode: "wal" for a file journal, "memory" for
      ":memory:". *)

  val wal_check : path:string -> string list -> (unit, string) Result.t
  (** The refusal's decision alone, with no SQLite and no IO: [Ok ()] only for a single
      answer equal to "wal", ignoring case, else an [Error] naming [path] and the modes
      answered. Exported so the refusal can be tested against an answer of our own
      choosing -- this build's own test filesystem always enters WAL for real, so a case
      that only opens a real file can never tell a working refusal from a deleted one. *)
end

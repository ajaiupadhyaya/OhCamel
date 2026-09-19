(* The switch the desk obeys (design §3.8).

   Three things stop new orders: a limit the kill switch trips on, a person,
   and the engine itself. The first lives in the kernel's alerts, which only
   ever set a flag; this module is where the flag gains a consequence. The
   second is a halt by hand, the POST /api/desk/kill someone sends when
   something is wrong that no limit measures. The third is the engine's stop,
   which bin/main.ml sets when the venue's order updates end for good: after
   that no fill is heard, so no order may go out whose fill nothing would
   apply.

   THE ENGINE'S STOP OUTRANKS EVERYTHING, AND NO RESET LIFTS IT. What stopped
   has not recovered -- the update stream is gone until the engine restarts,
   reconnects and reconciles -- so a reset while stopped changes nothing at
   all, not the hand's halt or the limit's trip beneath it, and the reset
   route answers 409 before it would be called. It is not a halt by hand and
   never says it is. Only a restart, which builds a new switch, clears it;
   halt.mli keeps [t] abstract so nothing else can. Oms.stop sets it and asks
   the venue to cancel the open orders, as a trip and a kill do; this module
   is only the switch, and calls nothing back.

   A HAND OUTRANKS A LIMIT. A halt by hand is reported ahead of a trip, and
   only a deliberate reset lifts it: whoever pressed it knew something the
   limits did not.

   THE DEMO RESETS ITSELF, AND ONLY THE DEMO. The public demo trips its switch
   on purpose -- nvda-cap sits 200 dollars above NVDA's exposure -- and a
   switch that stayed tripped for ever would turn its blotter into a list of
   refusals. So [auto_reset_after], passed only by the demo, resets a limit's
   trip once that limit has been clear for that long; a halt by hand and the
   engine's stop are never reset by time. The live host passes nothing, and
   its switch stays where it was put until someone resets it.

   The source is a record of closures, so the logic is tested without a graph
   and a desk with no alerts configured still has a switch: [Source.none]
   never trips, and a hand can still halt. *)

open Core
module Alerts = Ohcamel.Alerts

module Source = struct
  type t = {
    tripped : unit -> (string * Time_ns.t) option;
    firing : unit -> string list;
    reset : unit -> unit;
  }

  let none =
    { tripped = (fun () -> None); firing = (fun () -> []); reset = (fun () -> ()) }

  let of_alerts (a : Alerts.t) =
    {
      tripped =
        (fun () ->
          match Alerts.Kill_switch.state (Alerts.kill_switch a) with
          | Alerts.Kill_switch.Tripped { by; at } -> Some (by, at)
          | Alerts.Kill_switch.Armed | Alerts.Kill_switch.Disarmed -> None);
      firing = (fun () -> Alerts.firing_limits a);
      reset = (fun () -> Alerts.Kill_switch.reset (Alerts.kill_switch a));
    }
end

module State = struct
  type t =
    | Clear
    | Tripped of { limit : string; at : Time_ns.t }
    | Halted of { why : string; at : Time_ns.t }
    | Stopped of { why : string; at : Time_ns.t }

  let name = function
    | Clear -> "clear"
    | Tripped _ -> "tripped"
    | Halted _ -> "halted"
    | Stopped _ -> "stopped"
end

type t = {
  source : Source.t;
  auto_reset_after : Time_ns.Span.t option;
  mutable by_hand : (string * Time_ns.t) option;
  (* The engine's stop. Nothing clears it but a new [t]. *)
  mutable stopped : (string * Time_ns.t) option;
  (* When the limit that tripped the switch was first seen clear, since it
     last fired. *)
  mutable clear_since : Time_ns.t option;
}

let create ?auto_reset_after source =
  { source; auto_reset_after; by_hand = None; stopped = None; clear_since = None }

let state t : State.t =
  match (t.stopped, t.by_hand, t.source.Source.tripped ()) with
  | Some (why, at), _, _ -> State.Stopped { why; at }
  | None, Some (why, at), _ -> State.Halted { why; at }
  | None, None, Some (limit, at) -> State.Tripped { limit; at }
  | None, None, None -> State.Clear

let reason t =
  match state t with
  | State.Clear -> None
  | State.Tripped { limit; at } ->
      Some
        (sprintf "the kill switch was tripped by %s at %s" limit (Desk_time.rfc3339 at))
  | State.Halted { why; at } ->
      Some (sprintf "the desk was halted by hand at %s: %s" (Desk_time.rfc3339 at) why)
  | State.Stopped { why; at } ->
      Some
        (sprintf "the desk was stopped by the engine: %s (at %s)" why
           (Desk_time.rfc3339 at))

(* A second press does not overwrite the first: the first press's reason is
   the one the reset has to answer. *)
let halt t ~why ~at = if Option.is_none t.by_hand then t.by_hand <- Some (why, at)

(* The engine's own halt. Likewise, a second stop does not overwrite the
   first. *)
let stop t ~why ~at = if Option.is_none t.stopped then t.stopped <- Some (why, at)

(* Lifts a hand's halt and a limit's trip -- but nothing while the engine has
   stopped the desk, because what stopped has not recovered. The reset route
   answers 409 then and never calls this, and the demo's [tick] does not call
   it while stopped. *)
let reset t =
  match t.stopped with
  | Some _ -> ()
  | None ->
      t.by_hand <- None;
      t.clear_since <- None;
      t.source.Source.reset ()

let tick t ~now =
  match (t.auto_reset_after, t.stopped, t.by_hand, t.source.Source.tripped ()) with
  | Some after, None, None, Some (limit, _) ->
      if List.mem (t.source.Source.firing ()) limit ~equal:String.equal then (
        t.clear_since <- None;
        false)
      else
        let since =
          match t.clear_since with
          | Some since -> since
          | None ->
              t.clear_since <- Some now;
              now
        in
        if Time_ns.Span.( >= ) (Time_ns.diff now since) after then (
          reset t;
          true)
        else false
  | _ ->
      t.clear_since <- None;
      false

let to_json t ~now : Yojson.Safe.t =
  let time at = `String (Desk_time.rfc3339 at) in
  let current = state t in
  let limit, why, at =
    match current with
    | State.Clear -> (`Null, `Null, `Null)
    | State.Tripped { limit; at } -> (`String limit, `Null, time at)
    | State.Halted { why; at } | State.Stopped { why; at } -> (`Null, `String why, time at)
  in
  `Assoc
    [
      ("state", `String (State.name current));
      ("limit", limit);
      ("why", why);
      ("at", at);
      ( "auto_reset_s",
        match t.auto_reset_after with
        | Some s -> `Float (Time_ns.Span.to_sec s)
        | None -> `Null );
      ( "resets_in_s",
        match (t.auto_reset_after, t.clear_since, current) with
        | Some after, Some since, State.Tripped _ ->
            `Float
              (Float.max 0.0
                 (Time_ns.Span.to_sec after
                 -. Time_ns.Span.to_sec (Time_ns.diff now since)))
        | _ -> `Null );
    ]

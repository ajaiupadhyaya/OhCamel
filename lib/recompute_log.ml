(* Which named node bodies ran, and how often.

   bin/main.ml has counted this since Phase 1 in order to print one column. The
   page needs the same fact for a different reason -- it lights the nodes that
   ran on a drawing of the graph -- and needs it per FRAME rather than per run,
   so the counting moves here and gains a second table.

   WHY TWO TABLES. [frame] is drained by whoever emits a frame and is empty
   immediately afterwards; [lifetime] is never cleared and answers "how many
   distinct nodes has this graph ever run, and which are the hot ones". A single
   table cannot do both: draining it to report a frame would destroy the
   lifetime tally, and not draining it would make every frame report every node
   that has ever run.

   WHY IT CANNOT RAISE. [note] runs INSIDE node bodies, alongside the
   computation, under the same rule graph.ml's on_change listeners are under:
   record the fact and return. An exception thrown from inside a stabilize
   leaves the graph half-computed with no honest way to report what state it is
   in. Hashtbl.incr is total -- it inserts 1 where there was no key -- so there
   is no lookup here that could fail and no allocation that could be refused
   more than any other.

   WHY IT IS NOT INCREMENTAL'S COUNTER. Inc.State.num_nodes_recomputed is
   process-wide: it counts input cells, the unnamed plumbing graph.ml never
   named, every scenario fork, and the startup scaling probe, all against one
   shared state. That number is worth printing and is labelled as what it is.
   This one is the named node bodies of ONE graph, because Graph.fork does not
   inherit the on_compute hook -- which is what makes "26 of 53 ran this frame"
   a statement about the book rather than about the process. *)

open Core

type t = {
  (* Cleared by [drain]. What ran since the last frame was emitted. *)
  frame : int String.Table.t;
  (* Never cleared. What has ever run, for [distinct], [total] and [hottest]. *)
  lifetime : int String.Table.t;
}

let create () = { frame = String.Table.create (); lifetime = String.Table.create () }

(* Called from inside a node body, once per recomputation. Two hashtable
   increments and nothing else: no formatting, no allocation of a list, no
   comparison against a previous value. Anything more expensive here is paid on
   every node of every tick. *)
let note (t : t) (name : string) : unit =
  Hashtbl.incr t.frame name;
  Hashtbl.incr t.lifetime name

(* Sorted by name rather than returned in hashtable order, because this list
   goes onto the wire and out to a browser: an unsorted order would make two
   frames with the same content compare unequal for no reason, and would make a
   test of the drained set depend on a hash seed. *)
let drain (t : t) : (string * int) list =
  let entries =
    Hashtbl.to_alist t.frame
    |> List.sort ~compare:(fun (a, _) (b, _) -> String.compare a b)
  in
  Hashtbl.clear t.frame;
  entries

let distinct (t : t) : int = Hashtbl.length t.lifetime

let total (t : t) : int =
  Hashtbl.fold t.lifetime ~init:0 ~f:(fun ~key:_ ~data acc -> acc + data)

(* Count descending, then name ascending. The second key is not decoration: two
   nodes on the same edge run the same number of times, so ties are the common
   case here and an unstable order would make the hottest list flicker between
   runs of a program whose whole claim is that it reproduces. *)
let hottest (t : t) ~(n : int) : (string * int) list =
  Hashtbl.to_alist t.lifetime
  |> List.sort ~compare:(fun (name_a, a) (name_b, b) ->
      match Int.descending a b with 0 -> String.compare name_a name_b | c -> c)
  |> fun sorted -> List.take sorted n

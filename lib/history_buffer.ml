(* A bounded, in-memory history of published risk numbers.

   The dashboard shows one number per metric, and one number is a state rather
   than a story. Gross exposure of $316,000 says nothing about whether it has
   been flat all session or doubled in the last ten minutes, and drawdown is the
   clearest case: 3% is unremarkable if it has been 3% since the open and is the
   only thing worth looking at if it was 0% four minutes ago. So this keeps a
   short trail.

   THIS IS NOT PERSISTENCE, AND MUST NOT BECOME IT.

   The README states that this engine has no persistence: state lives in the
   running process and a restart rebuilds the book from the position file and
   the feed. That is a design choice, not a missing feature -- there is no
   schema to migrate, no store to reconcile against, and no possibility of the
   engine starting up and confidently serving yesterday's risk.

   This buffer is a fixed-capacity ring in memory. It writes nothing to disk, it
   is not restored on startup, and it drops its oldest entry rather than growing.
   A restart empties it, which is correct: the numbers in it are about a process
   that is no longer running. If a future change makes it durable, that change
   is adding persistence to this project and should be argued as such rather
   than arriving as a side effect of wanting a longer chart.

   IT IS AN OBSERVER, NOT A NODE.

   Same reasoning as alerts.ml, and the same seam. A graph node may be
   recomputed whenever the runtime likes, so a node that appended to a buffer
   would append an unpredictable number of times -- Incremental makes no promise
   about how often a body runs, only about what its value is. Appending is an
   effect, effects go on observers, and [attach] below hangs off
   [Graph.on_change] exactly as the alerting consumer does.

   It also means the chart is driven by the same signal the SSE stream is: a
   quiet market appends nothing, so a flat line here is a real absence of change
   rather than a sampler that happened to be asleep. *)

open Core

module Point = struct
  (* One published state, flattened.

     Deliberately a small fixed record rather than a whole [Graph.Snapshot.t].
     Five hundred snapshots would retain five hundred exposure maps, breach
     lists and feed-health records -- most of a megabyte of live data to draw a
     line six pixels tall. These are the quantities a chart can actually show.

     The three risk numbers are options for the same reason they are options on
     the snapshot: during warm-up there is no return distribution to take a
     quantile of, and a zero would render as "no risk", which risk_metrics.ml
     calls the most dangerous wrong answer available. A gap in a line is the
     honest rendering of a period when the number did not exist. *)
  type t = {
    (* Milliseconds since the epoch. A number rather than a formatted string
       because the consumer is a chart, and a chart needs to do arithmetic on
       the axis. *)
    time_ms : float;
    gross : float;
    net : float;
    equity : float;
    drawdown : float;
    var_notional : float option;
    es_notional : float option;
  }
  [@@deriving sexp_of, fields ~getters]
end

type t = {
  (* The ring. [capacity] entries, [next] is where the following write lands,
     and [filled] counts how many are real -- which matters only until the first
     wrap and is what stops a fresh buffer from reporting [capacity] points of
     zero. *)
  points : Point.t option array;
  capacity : int;
  mutable next : int;
  mutable filled : int;
  (* Total appends ever, including evicted ones. Exposed because "500 points"
     and "500 points out of 40,000 changes" are different statements about how
     much of the session a reader is looking at, and only the second is
     honest. *)
  mutable appended : int;
}

(* Five hundred points.

   At the demo's tick rate that is a couple of minutes, and at a real book's it
   is however long it is -- the buffer is bounded by COUNT, not by time, because
   it is fed by changes rather than by a clock and the two are not
   proportional. Sized to be a useful trail without being a data structure
   anybody has to think about: 500 points is roughly 30KB of floats. *)
let default_capacity = 500

let create ?(capacity = default_capacity) () =
  if capacity < 1 then
    invalid_argf "history_buffer: capacity must be positive, got %d" capacity ();
  {
    points = Array.create ~len:capacity None;
    capacity;
    next = 0;
    filled = 0;
    appended = 0;
  }

let length t = t.filled
let capacity t = t.capacity
let appended t = t.appended

let append (t : t) (point : Point.t) : unit =
  t.points.(t.next) <- Some point;
  t.next <- (t.next + 1) % t.capacity;
  if t.filled < t.capacity then t.filled <- t.filled + 1;
  t.appended <- t.appended + 1

(* Oldest first, which is the order a chart draws in.

   The ring's physical order is not the logical one once it has wrapped, so this
   walks from the oldest slot forward. Before the first wrap the oldest slot is
   index 0; after it, it is wherever [next] points, because that is the entry
   about to be overwritten and therefore the one that has survived longest. *)
let to_list (t : t) : Point.t list =
  let start = if t.filled < t.capacity then 0 else t.next in
  List.filter_map
    (List.init t.filled ~f:(fun i -> t.points.((start + i) % t.capacity)))
    ~f:Fn.id

let clear (t : t) : unit =
  Array.fill t.points ~pos:0 ~len:t.capacity None;
  t.next <- 0;
  t.filled <- 0;
  t.appended <- 0

(* Read the same quantities straight off the graph's observers.

   Distinct from [point_of_snapshot] below, and the difference is not stylistic.
   [Graph.snapshot] STABILIZES before it reads, so that a caller who has just
   written some inputs gets a settled answer without having to know to ask. That
   is the right default everywhere except here: [Graph.on_change] fires from
   inside an Incremental update handler, and Incremental refuses to stabilize
   re-entrantly --

     ("cannot stabilize during on-update handlers")

   -- so calling [snapshot] from the handler raises and takes the process with
   it. The observers are already settled at that point, which is exactly why the
   handler is allowed to run there, so reading them directly is both safe and
   the cheaper of the two.

   This was not obvious and was not caught by reasoning; it was caught by
   test_history_buffer.ml, which is the argument for driving an observer from a
   test at all. *)
let point_of_graph ~(time_ms : float) (graph : Graph.t) : Point.t =
  {
    Point.time_ms;
    gross = Types.Notional.to_float (Graph.gross_exposure graph);
    net = Types.Notional.to_float (Graph.net_exposure graph);
    equity = Types.Notional.to_float (Graph.equity graph);
    drawdown = Graph.current_drawdown graph;
    var_notional =
      Option.map (Graph.value_at_risk_notional graph) ~f:Types.Notional.to_float;
    es_notional =
      Option.map (Graph.expected_shortfall_notional graph) ~f:Types.Notional.to_float;
  }

(* The same point from a snapshot a caller already holds. Kept because the
   snapshot is the published record and a consumer that has one should not have
   to reach past it into the graph -- but it is NOT what [attach] uses, for the
   reason above. *)
let point_of_snapshot ~(time_ms : float) (s : Graph.Snapshot.t) : Point.t =
  {
    Point.time_ms;
    gross = Types.Notional.to_float (Graph.Snapshot.gross_exposure s);
    net = Types.Notional.to_float (Graph.Snapshot.net_exposure s);
    equity = Types.Notional.to_float (Graph.Snapshot.equity s);
    drawdown = Graph.Snapshot.current_drawdown s;
    var_notional =
      Option.map (Graph.Snapshot.value_at_risk_notional s) ~f:Types.Notional.to_float;
    es_notional =
      Option.map (Graph.Snapshot.expected_shortfall_notional s) ~f:Types.Notional.to_float;
  }

(* Hang a buffer off a graph's change signal.

   [now] is a parameter rather than a call to [Types.Time.now] inside, so the
   tests can drive the clock and assert on the timestamps instead of tolerating
   whatever the wall clock said. The live caller passes [Types.Time.now].

   The handler reads the graph's observers directly rather than calling
   [Graph.snapshot], because [snapshot] stabilizes and Incremental will not
   stabilize from inside an update handler -- see [point_of_graph]. What it does
   do is bounded and small: six observer reads and an array write. Contrast
   server.ml's broadcaster, which fills an Ivar and defers everything, because
   serializing and writing to n sockets is neither bounded nor small and has no
   business happening on the graph's own thread of control. *)
let attach ?(capacity = default_capacity) ~(graph : Graph.t) ~(now : unit -> Types.Time.t)
    () : t =
  let t = create ~capacity () in
  Graph.on_change graph ~f:(fun () ->
      let time_ms = Types.Time.Span.to_ms (Types.Time.diff (now ()) Types.Time.epoch) in
      append t (point_of_graph ~time_ms graph));
  t

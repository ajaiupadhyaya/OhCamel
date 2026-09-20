(* The long panel: 250 daily returns per name, aligned to the factor ETFs' own
   calendar (rulings 1-3 of A4's risk depth).

   Pure data and pure functions only -- no Graph, no Incremental, no IO, and
   no clock beyond the [now] each caller passes in. lib/feed/long_window.ml (a
   later task) is what actually pages through SIP daily bars and FRED's
   DGS10, and calls [build] with what it fetched; this module is what makes
   that assembly testable without a network, and it is the only place the
   assembly happens.

   The discipline that makes the panel trustworthy is alignment, and it cuts
   in two different directions:

   - The panel has ONE calendar: the dates on which SPY, IWM, IWD, IWF and
     MTUM all have a final bar (ruling 2). Every factor is a return or a
     spread of returns computed on THAT calendar, so by construction only
     [rates] can ever be missing a value -- the five ETFs always have a bar
     on a panel date, because that is what makes it a panel date.
   - Every other instrument aligns to that same calendar SEPARATELY, and does
     not get to borrow it. A return on panel date i needs a bar on date i
     AND on date i-1; a gap on either side is [nan], not the nearest good
     value, not zero, not carried from three days back. A thin or brand-new
     name shortens only its OWN observation count, never the window's dates
     or anyone else's count.

   THE ONE RULE FOR CONSUMERS

   Because a gap is [nan] and not a number, this module is the first place in
   this tree that legitimately produces arrays containing [nan]: an
   instrument's [returns] carry one wherever it has no observation, and the
   [rates] factor carries one on its trailing unpublished run. So: a raw
   [returns] array, and the [rates] factor, must NEVER be handed to
   Risk_metrics. Arithmetic does not raise on [nan], it propagates it, so a
   window function given one would return [nan] -- and a [nan] that reaches a
   published figure is the failure this whole module's care about alignment
   exists to prevent. Read the panel through [present_returns] below, which
   removes the gaps; [Factor_model.fit] drops whole [nan] rows and
   [Liquidity.line] guards with [is_finite], each for its own reasons.
   Risk_metrics now refuses a non-finite observation with [Invalid_argument]
   rather than returning [nan], so breaking the rule is loud rather than
   silent -- but it is still the rule.

   [build] never raises. Every way a caller's own data can be malformed --
   a duplicated bar, a duplicated DGS10 print, a window that makes no sense --
   is an [Error] that names the problem, because this module's caller is a
   scheduled refresh with nobody watching a stack trace: an exception here
   would take the whole engine down with it rather than skip one refresh and
   log why. *)

open Core
open Types

module Bar = struct
  type t = { date : Date.t; close : float; volume : float } [@@deriving sexp, equal]
end

type t = {
  dates : Date.t array;
  (* T + 1 panel dates, oldest first: the dates on which every factor ETF has
     a final bar, the last [window + 1] of them kept. *)
  returns : float array Symbol.Map.t;
  (* one entry per symbol in [build]'s ~instruments, length [window]. Entry i
     is instrument's close on [dates.(i+1)] over its close on [dates.(i)],
     minus 1 -- [nan] unless the instrument has a bar on BOTH dates. *)
  factors : float array array;
  (* length 5, [Types.Factor.all] order (market, size, value, momentum,
     rates); each length [window]. Every entry is a real number except
     [rates], which is [nan] on the trailing run of panel dates newer than
     the last DGS10 level FRED has published -- see [build]. *)
  last_close : float option Symbol.Map.t;
  (* the close of the instrument's own most recent final bar ON OR BEFORE
     [as_of] -- not necessarily [as_of] itself, so a thin name still freezes
     at something, however old. [None] only when the instrument has no final
     bar at or before [as_of]. *)
  adv20 : float option Symbol.Map.t;
  (* mean volume over the instrument's own last 20 final bars ON OR BEFORE
     [as_of] (not the last 20 PANEL dates -- its own calendar, bounded by
     [as_of] the same way [last_close] is). [None] below 20 such bars. *)
  observations : int Symbol.Map.t;
  (* count of non-[nan] entries in [returns], per instrument. *)
  as_of : Date.t; (* the last panel date, [dates.(window)]. *)
}
[@@deriving sexp_of]

(* 00:16 UTC on the day after [d] -- the instant ruling 1 makes a bar dated
   [d] final (the research service's own rule: the day's bar is complete and
   SIP's restricted final fifteen minutes have passed).

   Built from calendar arithmetic against the Unix epoch rather than a zone
   lookup, because UTC has no daylight-saving rule to look up: a fixed-offset
   span from the epoch is exact on every date, needs no timezone database
   (unlike desk/desk_time.ml's America/New_York), and cannot be wrong about a
   spring-forward this file never has to think about. *)
let final_at (d : Date.t) : Time_ns.t =
  let next_day = Date.add_days d 1 in
  let days_since_epoch = Date.diff next_day Date.unix_epoch in
  Time_ns.of_span_since_epoch
    Time_ns.Span.(of_day (Float.of_int days_since_epoch) + of_min 16.0)

let final_bars ~(now : Time_ns.t) (bars : Bar.t list) : Bar.t list =
  List.filter bars ~f:(fun (bar : Bar.t) -> Time_ns.( >= ) now (final_at bar.date))

(* The five factor ETFs, bound locally by name because the formulas below
   (ruling 3) name each one directly. Pattern-matching [Factor.etfs] into five
   variables would depend on that list's order just as much as this does, and
   would be harder to read at the point of use. *)
let spy = Symbol.of_string "SPY"
let iwm = Symbol.of_string "IWM"
let iwd = Symbol.of_string "IWD"
let iwf = Symbol.of_string "IWF"
let mtum = Symbol.of_string "MTUM"

(* The ADV window: how many of an instrument's own final bars [adv20] averages,
   and therefore also the floor below which there is no ADV to report. Named
   once because it is used as both -- the floor and the divisor have to be the
   same number, and writing the literal twice is how they come to differ: a
   mean over the last 20 bars divided by 19 is a number that looks right and
   is not. *)
let adv_bars = 20

(* A symbol's bars, keyed by date -- or an [Error] naming [who] (an ETF
   ticker or a book instrument's symbol, whichever the caller is building)
   and the date, when two final bars share a day. That is a data bug the feed
   should never produce, and [Map.of_alist] (not [_exn]) is what turns it
   into an ordinary refusal here rather than an exception this module's
   caller -- an unattended scheduled refresh -- has no way to recover from. *)
let date_map ~(who : string) (bars : Bar.t list) : (Bar.t Date.Map.t, string) Result.t =
  match Date.Map.of_alist (List.map bars ~f:(fun (bar : Bar.t) -> (bar.date, bar))) with
  | `Ok m -> Ok m
  | `Duplicate_key d ->
      Error
        (Printf.sprintf "long_panel: %s has two final bars dated %s" who
           (Date.to_string d))

let build ~(bars : Bar.t list Symbol.Map.t) ~(dgs10 : (Date.t * float) list)
    ~(instruments : Symbol.t list) ~(window : int) ~(now : Time_ns.t) :
    (t, string) Result.t =
  let open Result.Let_syntax in
  let%bind () =
    if window < 1 then
      Error (Printf.sprintf "long_panel: window must be at least 1, got %d" window)
    else Ok ()
  in
  (* A symbol listed twice would otherwise double an entry in [rows] below
     under one key, which is not a data problem worth refusing the whole
     panel over -- it is resolved here, silently, the same way asking for the
     same figure twice is. *)
  let instruments = List.stable_dedup instruments ~compare:Symbol.compare in
  (* Ruling: [build] applies [final_bars] first, to every series alike. *)
  let final_of symbol =
    Map.find bars symbol |> Option.value ~default:[] |> final_bars ~now
  in
  let%bind etf_maps =
    List.fold_result Factor.etfs ~init:Symbol.Map.empty ~f:(fun acc etf ->
        match final_of etf with
        | [] ->
            Error
              (Printf.sprintf "long_panel: %s has no final bars" (Symbol.to_string etf))
        | some_bars ->
            let%bind m = date_map ~who:(Symbol.to_string etf) some_bars in
            Ok (Map.set acc ~key:etf ~data:m))
  in
  (* Panel dates: the intersection of the five ETFs' own date sets, which is
     exactly "every ETF has a final bar here" (ruling 2). [Set.inter] rather
     than counting occurrences, because five maps agreeing on a date is a set
     operation, not a tally. *)
  let common_dates =
    match Factor.etfs with
    | [] -> Date.Set.empty (* unreachable: Factor.etfs always has five entries *)
    | first :: rest ->
        let keys_of etf = Date.Set.of_list (Map.keys (Map.find_exn etf_maps etf)) in
        List.fold rest ~init:(keys_of first) ~f:(fun acc etf ->
            Set.inter acc (keys_of etf))
  in
  let sorted_dates = Set.to_array common_dates in
  (* ascending, per [Set.to_array]'s contract, so the LAST [window + 1] of
     them (never the first) are the newest. *)
  let n = Array.length sorted_dates in
  let%bind () =
    if n < window + 1 then
      Error
        (Printf.sprintf "long_panel: %d common ETF dates, fewer than window + 1 (%d)" n
           (window + 1))
    else Ok ()
  in
  let dates = Array.sub sorted_dates ~pos:(n - (window + 1)) ~len:(window + 1) in
  let%bind dgs10_map =
    match Date.Map.of_alist dgs10 with
    | `Ok m -> Ok m
    | `Duplicate_key d ->
        Error
          (Printf.sprintf "long_panel: DGS10 has two levels dated %s" (Date.to_string d))
  in
  let%bind max_dgs10_date =
    match Map.max_elt dgs10_map with
    | Some (d, _) -> Ok d
    | None -> Error "long_panel: no DGS10 level on or before the first panel date"
  in
  (* Stale history: every DGS10 print predates the panel entirely. Without
     this check the code below would still run -- [closest_key] finds the
     same last print [dates.(0)] would carry forward for a holiday -- and
     would quietly hand back a panel whose entire [rates] array is [nan],
     which is not what an [Error] is for here. This is different from "DGS10
     starts partway through the panel" (checked next): that leaves later
     dates with real coverage, and only a genuine gap on one end is nan. *)
  let%bind () =
    if Date.( < ) max_dgs10_date dates.(0) then
      Error
        (Printf.sprintf
           "long_panel: DGS10 has published nothing since %s, before the panel's first \
            date"
           (Date.to_string max_dgs10_date))
    else Ok ()
  in
  let%bind () =
    match Map.closest_key dgs10_map `Less_or_equal_to dates.(0) with
    | Some _ -> Ok ()
    | None -> Error "long_panel: no DGS10 level on or before the first panel date"
  in
  (* L(d): the last DGS10 level published on or before [d] -- but only while
     [d] is within what FRED has actually published. Past the last published
     date, [closest_key] would still find SOME earlier value (the same
     carry-forward a mid-panel holiday uses), and that is exactly the reading
     ruling 2 forbids: a holiday is confirmed flat by the publication that
     resumes after it, but the newest panel dates have no such confirmation,
     only a stale print. Those get [None], not a borrowed number. *)
  let level d =
    if Date.( > ) d max_dgs10_date then None
    else Map.closest_key dgs10_map `Less_or_equal_to d |> Option.map ~f:snd
  in
  let close etf d : float = (Map.find_exn (Map.find_exn etf_maps etf) d : Bar.t).close in
  (* r(etf, i): the ETF's return from panel date i-1 to panel date i. Always
     defined -- both dates are panel dates, and every ETF has a bar on every
     panel date by construction. *)
  let r etf i = (close etf dates.(i) /. close etf dates.(i - 1)) -. 1.0 in
  let market = Array.init window ~f:(fun idx -> r spy (idx + 1)) in
  let size = Array.init window ~f:(fun idx -> r iwm (idx + 1) -. r spy (idx + 1)) in
  let value = Array.init window ~f:(fun idx -> r iwd (idx + 1) -. r iwf (idx + 1)) in
  let momentum = Array.init window ~f:(fun idx -> r mtum (idx + 1) -. r spy (idx + 1)) in
  let rates =
    Array.init window ~f:(fun idx ->
        let i = idx + 1 in
        match (level dates.(i), level dates.(i - 1)) with
        | Some hi, Some lo -> hi -. lo
        | _ -> Float.nan)
  in
  (* Built from [Factor.all], with the match's exhaustiveness check tying
     this array's order to [Factor.t]'s own declaration order -- a fifth,
     independent factor added to the type would fail to compile here rather
     than silently leaving this array one short, or in the wrong order. *)
  let factors =
    Array.of_list_map Factor.all ~f:(function
      | Factor.Market -> market
      | Factor.Size -> size
      | Factor.Value -> value
      | Factor.Momentum -> momentum
      | Factor.Rates -> rates)
  in
  let as_of = dates.(window) in
  (* Every other instrument aligns to [dates] on its own: a return needs a
     bar on both endpoints, and nothing here ever looks past an immediate gap
     for a substitute. [last_close] and [adv20] read only the instrument's
     bars dated on or before [as_of] -- a feed can hand back a final bar
     dated after the panel's own newest date (an instrument that trades on a
     day the five ETFs, as a group, do not all agree on), and letting that
     leak into either figure would be a look-ahead past what this panel
     itself claims to know as of [as_of]. *)
  let per_instrument symbol :
      (Symbol.t * float array * int * float option * float option, string) Result.t =
    let bs = final_of symbol in
    let%bind m = date_map ~who:(Symbol.to_string symbol) bs in
    let returns =
      Array.init window ~f:(fun idx ->
          let i = idx + 1 in
          match (Map.find m dates.(i), Map.find m dates.(i - 1)) with
          | Some (hi : Bar.t), Some (lo : Bar.t) -> (hi.close /. lo.close) -. 1.0
          | _ -> Float.nan)
    in
    let observations = Array.count returns ~f:(fun x -> not (Float.is_nan x)) in
    let bars_up_to_as_of =
      List.filter bs ~f:(fun (b : Bar.t) -> Date.( <= ) b.date as_of)
      |> List.sort ~compare:(fun (a : Bar.t) b -> Date.compare a.date b.date)
    in
    let last_close =
      List.last bars_up_to_as_of |> Option.map ~f:(fun (b : Bar.t) -> b.close)
    in
    let adv20 =
      let n_bs = List.length bars_up_to_as_of in
      if n_bs < adv_bars then None
      else
        let window_bars = List.drop bars_up_to_as_of (n_bs - adv_bars) in
        Some
          (List.sum (module Float) window_bars ~f:(fun (b : Bar.t) -> b.volume)
          /. Float.of_int adv_bars)
    in
    Ok (symbol, returns, observations, last_close, adv20)
  in
  let%bind rows =
    List.fold_result instruments ~init:[] ~f:(fun acc symbol ->
        let%bind row = per_instrument symbol in
        Ok (row :: acc))
    |> Result.map ~f:List.rev
  in
  (* [instruments] was deduplicated above, so [rows] carries at most one row
     per symbol and [_exn] cannot raise. *)
  let map_of f = Symbol.Map.of_alist_exn (List.map rows ~f) in
  return
    {
      dates;
      returns = map_of (fun (s, r, _, _, _) -> (s, r));
      factors;
      last_close = map_of (fun (s, _, _, lc, _) -> (s, lc));
      adv20 = map_of (fun (s, _, _, _, a) -> (s, a));
      observations = map_of (fun (s, _, o, _, _) -> (s, o));
      as_of;
    }

(* val present_returns : t -> Symbol.t -> float array

   That instrument's observed returns, in order, with the [nan] gaps removed
   -- the only array from this module that may be handed to Risk_metrics.

   A raw [returns] array must never go there, and neither must the [rates]
   factor: both carry [nan] by design (a date the instrument has no bar on, a
   panel date newer than FRED's last DGS10 print), and Risk_metrics' window
   functions compute over every element they are given. [stddev],
   [covariance_matrix], [historical_var] and their siblings now raise
   [Invalid_argument] on a non-finite observation rather than returning [nan],
   so the mistake surfaces -- but surfacing it in a scheduled refresh is not
   the same as not making it, and this is the function that does not make it.

   Removes [nan] only, never [infinity]. The two mean different things here:
   [nan] is this module's own marker for "no observation", so dropping it is
   reading the panel as it was written, while an infinity can only come from a
   zero close upstream -- a data error, not a gap. Dropping that one would
   hide it; leaving it in hands it to Risk_metrics, which refuses it by name.
   That is the same split [Factor_model] makes (nan rows dropped as missing,
   inf an [Error]), and it is why the predicate below is [is_nan] and not
   [is_finite].

   The result's length is exactly [observations] for that symbol, which is
   counted with the same predicate; test_long_panel.ml pins the two together.

   Raises [Invalid_argument] on a symbol the panel does not carry. That is a
   key the CALLER chose -- an instrument it never passed in [~instruments] --
   not data a feed sent, so it is a programming error and not the kind of
   malformation [build] turns into an [Error]: returning [||] instead would
   read as "this name has no observations", which is a claim about the market
   rather than about the caller's spelling. *)
let present_returns (t : t) (symbol : Symbol.t) : float array =
  match Map.find t.returns symbol with
  | Some returns -> Array.filter returns ~f:(fun x -> not (Float.is_nan x))
  | None ->
      invalid_argf "long_panel: present_returns: %s is not an instrument in this panel"
        (Symbol.to_string symbol) ()

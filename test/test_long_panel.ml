(* Unit tests for long_panel.ml: the 250-observation window's calendar and
   alignment, ahead of any regression or covariance built on top of it.

   Every numeric assertion is hand-derived in the comment above it, per the
   project convention (test_vol_estimators.ml). Two disciplines get their own
   tests because they are the two easiest ways to get this module quietly
   wrong:

     ALIGNMENT   an instrument's return needs a bar on the panel date AND on
                 the panel's PREVIOUS date -- not the previous calendar day,
                 which may not be a panel date at all. Getting this wrong
                 either invents a return across a gap or drops a real one.

     RATES       DGS10 publishes a business day late, so its "last level on
                 or before d" carries forward a genuine flat day (a bond
                 holiday) but must NOT carry forward past its own newest
                 print into dates ruling 2 calls unpublished. The two cases
                 compute identically from the raw levels; only the trailing
                 check tells them apart, so both get a test, side by side. *)

open Core
open Ohcamel.Types
module LP = Ohcamel.Long_panel

let feq = Alcotest.float 1e-12
let sym = Symbol.of_string
let date = Date.of_string

(* A UTC instant, built the same way long_panel.ml's own [final_at] is
   verified against: Core reads a trailing "Z" on the time-of-day token as a
   zero UTC offset, so this never touches a timezone database. *)
let utc day time = Time_ns.of_string_with_utc_offset (day ^ " " ^ time ^ "Z")
let bar d close volume : LP.Bar.t = { LP.Bar.date = d; close; volume }

let etf_bars_5 dates_closes =
  (* [dates_closes] is one (date, close) list per ticker, in SPY, IWM, IWD,
     IWF, MTUM order, sharing one volume (unused by any assertion here). *)
  List.zip_exn [ "SPY"; "IWM"; "IWD"; "IWF"; "MTUM" ] dates_closes
  |> List.map ~f:(fun (ticker, dcs) ->
      (sym ticker, List.map dcs ~f:(fun (d, c) -> bar d c 0.0)))

let get_ok = function
  | Ok panel -> panel
  | Error message -> Alcotest.failf "long_panel: expected Ok, got Error %S" message

let get_error = function
  | Ok (_ : LP.t) -> Alcotest.fail "long_panel: expected Error, got Ok"
  | Error message -> message

(* ------------------------------------------------------------------------ *)
(* final_bars (ruling 1)                                                     *)
(* ------------------------------------------------------------------------ *)

(* A bar dated 2026-09-18 is final at 2026-09-19 00:16 UTC, not a minute
   before. A bar dated 2026-09-17 is final a full day earlier, so it is
   already final at both instants under test. *)
let test_final_bars () =
  let bars =
    [ bar (date "2026-09-18") 100.0 1_000.0; bar (date "2026-09-17") 99.0 900.0 ]
  in
  let at_22h = LP.final_bars ~now:(utc "2026-09-18" "22:00:00") bars in
  Alcotest.(check int)
    "only the 17th is final two hours before midnight" 1 (List.length at_22h);
  Alcotest.(check bool)
    "the 17th, not the 18th" true
    (Date.equal (List.hd_exn at_22h).LP.Bar.date (date "2026-09-17"));
  (* One second before the cutoff: still not final. This is what tells the
     cutoff apart from a mutant fixed at 00:00 or at 00:15 -- both of those
     would already have made the 18th final by 00:15:59. *)
  let at_00_15_59 = LP.final_bars ~now:(utc "2026-09-19" "00:15:59") bars in
  Alcotest.(check int)
    "one second before the cutoff, the 18th is still not final" 1
    (List.length at_00_15_59);
  let at_cutoff = LP.final_bars ~now:(utc "2026-09-19" "00:16:00") bars in
  Alcotest.(check int) "both are final at 00:16 UTC on the 19th" 2 (List.length at_cutoff)

(* ------------------------------------------------------------------------ *)
(* A window of 2: panel dates from the ETF intersection, and alignment      *)
(* ------------------------------------------------------------------------ *)

(* Four candidate dates; IWD has no bar on the second, so the panel dates are
   the three the brief names: 1, 3 and 4 below (d1, d3, d4). Closes are chosen
   so every return is an exact multiple of 0.05, needing no tolerance beyond
   ordinary float rounding.

     r(SPY,d3)  = 110/100  - 1 =  0.10      r(SPY,d4)  = 99/110    - 1 = -0.10
     r(IWM,d3)  = 52.5/50  - 1 =  0.05      r(IWM,d4)  = 47.25/52.5- 1 = -0.10
     r(IWD,d3)  = 44/40    - 1 =  0.10      r(IWD,d4)  = 41.8/44   - 1 = -0.05
     r(IWF,d3)  = 63/60    - 1 =  0.05      r(IWF,d4)  = 69.3/63   - 1 =  0.10
     r(MTUM,d3) = 88/80    - 1 =  0.10      r(MTUM,d4) = 92.4/88   - 1 =  0.05

     market   = r(SPY)                     ->  0.10 ; -0.10
     size     = r(IWM)  - r(SPY)           -> -0.05 ;  0.00
     value    = r(IWD)  - r(IWF)           ->  0.05 ; -0.15
     momentum = r(MTUM) - r(SPY)           ->  0.00 ;  0.15

   DGS10 is given at d1, d3 and d4 (4.00, 4.10, 4.05), so rates is a plain two
   entries: 4.10 - 4.00 = 0.10, then 4.05 - 4.10 = -0.05.

   Instrument "GAP" has bars on d1, d2 and d4 (not d3): its return at d3 is
   nan (no bar there), and its return at d4 is ALSO nan, because the previous
   PANEL date, d3, has no bar -- d2's bar is not a substitute, panel-adjacent
   or not. Instrument "NOBAR" is absent from the bars map entirely, which the
   brief is explicit is not an error: it reads back as two nans and a zero
   count, same as any other instrument the feed has nothing on yet. *)
let test_window_of_two () =
  let d1 = date "2026-01-05" and d2 = date "2026-01-06" in
  let d3 = date "2026-01-07" and d4 = date "2026-01-08" in
  let etfs =
    etf_bars_5
      [
        [ (d1, 100.0); (d2, 105.0); (d3, 110.0); (d4, 99.0) ];
        (* SPY *)
        [ (d1, 50.0); (d2, 52.0); (d3, 52.5); (d4, 47.25) ];
        (* IWM *)
        [ (d1, 40.0); (d3, 44.0); (d4, 41.8) ];
        (* IWD -- no d2 *)
        [ (d1, 60.0); (d2, 61.0); (d3, 63.0); (d4, 69.3) ];
        (* IWF *)
        [ (d1, 80.0); (d2, 81.0); (d3, 88.0); (d4, 92.4) ];
        (* MTUM *)
      ]
  in
  let gap_bars = [ bar d1 10.0 5.0; bar d2 10.5 5.0; bar d4 11.0 5.0 ] in
  let bars =
    Symbol.Map.of_alist_exn ((sym "GAP", gap_bars) :: etfs)
    (* "NOBAR" is deliberately absent from this map. *)
  in
  let dgs10 = [ (d1, 4.00); (d3, 4.10); (d4, 4.05) ] in
  let panel =
    get_ok
      (LP.build ~bars ~dgs10
         ~instruments:[ sym "GAP"; sym "NOBAR" ]
         ~window:2 ~now:(utc "2026-01-09" "00:16:00"))
  in
  Alcotest.(check int) "3 panel dates (window + 1)" 3 (Array.length panel.LP.dates);
  Alcotest.(check bool)
    "dates are d1, d3, d4 in order" true
    (Array.equal Date.equal panel.LP.dates [| d1; d3; d4 |]);
  Alcotest.(check bool) "as_of is the last panel date" true (Date.equal panel.LP.as_of d4);
  let market = panel.LP.factors.(0)
  and size = panel.LP.factors.(1)
  and value = panel.LP.factors.(2)
  and momentum = panel.LP.factors.(3)
  and rates = panel.LP.factors.(4) in
  Alcotest.check feq "market[0] = r(SPY,d3) = 110/100 - 1"
    ((110.0 /. 100.0) -. 1.0)
    market.(0);
  Alcotest.check feq "market[1] = r(SPY,d4) = 99/110 - 1"
    ((99.0 /. 110.0) -. 1.0)
    market.(1);
  Alcotest.check feq "size[0] = r(IWM,d3) - r(SPY,d3) = 0.05 - 0.10"
    ((52.5 /. 50.0) -. 1.0 -. ((110.0 /. 100.0) -. 1.0))
    size.(0);
  Alcotest.check feq "size[1] = r(IWM,d4) - r(SPY,d4) = -0.10 - -0.10"
    ((47.25 /. 52.5) -. 1.0 -. ((99.0 /. 110.0) -. 1.0))
    size.(1);
  Alcotest.check feq "value[0] = r(IWD,d3) - r(IWF,d3) = 0.10 - 0.05"
    ((44.0 /. 40.0) -. 1.0 -. ((63.0 /. 60.0) -. 1.0))
    value.(0);
  Alcotest.check feq "value[1] = r(IWD,d4) - r(IWF,d4) = -0.05 - 0.10"
    ((41.8 /. 44.0) -. 1.0 -. ((69.3 /. 63.0) -. 1.0))
    value.(1);
  Alcotest.check feq "momentum[0] = r(MTUM,d3) - r(SPY,d3) = 0.10 - 0.10"
    ((88.0 /. 80.0) -. 1.0 -. ((110.0 /. 100.0) -. 1.0))
    momentum.(0);
  Alcotest.check feq "momentum[1] = r(MTUM,d4) - r(SPY,d4) = 0.05 - -0.10"
    ((92.4 /. 88.0) -. 1.0 -. ((99.0 /. 110.0) -. 1.0))
    momentum.(1);
  Alcotest.check feq "rates[0] = 4.10 - 4.00" (4.10 -. 4.00) rates.(0);
  Alcotest.check feq "rates[1] = 4.05 - 4.10" (4.05 -. 4.10) rates.(1);
  let gap_returns = Map.find_exn panel.LP.returns (sym "GAP") in
  Alcotest.check feq "GAP has no bar on d3: nan" Float.nan gap_returns.(0);
  Alcotest.check feq "GAP has a bar on d4, but not on d3, the PREVIOUS panel date: nan"
    Float.nan gap_returns.(1);
  Alcotest.(check int)
    "GAP's observation count is 0" 0
    (Map.find_exn panel.LP.observations (sym "GAP"));
  Alcotest.(check (option (float 1e-12)))
    "GAP's last close is its own most recent final bar (d4), not a panel-date close"
    (Some 11.0)
    (Map.find_exn panel.LP.last_close (sym "GAP"));
  let nobar_returns = Map.find_exn panel.LP.returns (sym "NOBAR") in
  Alcotest.check feq "an uncovered instrument: nan, not an error (return 0)" Float.nan
    nobar_returns.(0);
  Alcotest.check feq "an uncovered instrument: nan, not an error (return 1)" Float.nan
    nobar_returns.(1);
  Alcotest.(check int)
    "an uncovered instrument's count is 0" 0
    (Map.find_exn panel.LP.observations (sym "NOBAR"));
  Alcotest.(check (option (float 1e-12)))
    "an uncovered instrument has no last close" None
    (Map.find_exn panel.LP.last_close (sym "NOBAR"));
  Alcotest.(check (option (float 1e-12)))
    "an uncovered instrument has no ADV" None
    (Map.find_exn panel.LP.adv20 (sym "NOBAR"))

(* ------------------------------------------------------------------------ *)
(* A bond holiday mid-panel                                                  *)
(* ------------------------------------------------------------------------ *)

(* Four panel dates p0..p3 (window 3). DGS10 publishes at p0, p1 and p3 but
   not p2 -- a holiday, with real prints on both sides. L(p2) carries forward
   p1's level (4.00), so:

     rates[1] (p1 -> p2) = L(p2) - L(p1) = 4.00 - 4.00 = 0.0          (flat)
     rates[2] (p2 -> p3) = L(p3) - L(p2) = 4.20 - 4.00 = 0.20         (the
       whole p1 -> p3 move, carried by the date that follows the holiday)

   The ETFs' own closes are irrelevant here (held flat at 100 for all five),
   so market/size/value/momentum are all exactly 0 and are not asserted. *)
let test_bond_holiday () =
  let p0 = date "2026-02-02" and p1 = date "2026-02-03" in
  let p2 = date "2026-02-04" and p3 = date "2026-02-05" in
  let flat = [ (p0, 100.0); (p1, 100.0); (p2, 100.0); (p3, 100.0) ] in
  let bars = Symbol.Map.of_alist_exn (etf_bars_5 [ flat; flat; flat; flat; flat ]) in
  let dgs10 = [ (p0, 4.00); (p1, 4.00); (p3, 4.20) ] in
  let panel =
    get_ok
      (LP.build ~bars ~dgs10 ~instruments:[] ~window:3 ~now:(utc "2026-02-06" "00:16:00"))
  in
  let rates = panel.LP.factors.(4) in
  Alcotest.check feq "rates[0] = 4.00 - 4.00" (4.00 -. 4.00) rates.(0);
  Alcotest.check feq "the holiday itself reads flat: 4.00 - 4.00" (4.00 -. 4.00) rates.(1);
  Alcotest.check feq "the next date carries the whole two-day move: 4.20 - 4.00"
    (4.20 -. 4.00) rates.(2)

(* ------------------------------------------------------------------------ *)
(* Unpublished DGS10: the trailing dates are nan, not a holiday's 0.0        *)
(* ------------------------------------------------------------------------ *)

(* Same four-date shape, but DGS10 only reaches q1 -- q2 and q3 are the "last
   two ETF dates" the brief describes as beyond publication. L(q2) and L(q3)
   are therefore [None], not q1's carried-forward level, so:

     rates[0] (q0 -> q1) = 4.05 - 4.00 = 0.05        (a real, known change)
     rates[1] (q1 -> q2) = nan                        (q2 unpublished)
     rates[2] (q2 -> q3) = nan                        (q3 unpublished too)

   The contrast with the holiday test above is the point: the SAME
   carry-forward rule would read rates[1] as 4.00 - 4.00 = 0.0 if this file
   treated "last level on or before" as the whole story. It is not: past
   DGS10's own newest print, the level is unknown, and unknown is nan. *)
let test_unpublished_dgs10 () =
  let q0 = date "2026-03-02" and q1 = date "2026-03-03" in
  let q2 = date "2026-03-04" and q3 = date "2026-03-05" in
  let flat = [ (q0, 100.0); (q1, 100.0); (q2, 100.0); (q3, 100.0) ] in
  let bars = Symbol.Map.of_alist_exn (etf_bars_5 [ flat; flat; flat; flat; flat ]) in
  let dgs10 = [ (q0, 4.00); (q1, 4.05) ] in
  let panel =
    get_ok
      (LP.build ~bars ~dgs10 ~instruments:[] ~window:3 ~now:(utc "2026-03-06" "00:16:00"))
  in
  let rates = panel.LP.factors.(4) in
  Alcotest.check feq "rates[0] = 4.05 - 4.00, still known" (4.05 -. 4.00) rates.(0);
  Alcotest.check feq "rates[1]: q2 is past DGS10's last print -- nan, not 0.0" Float.nan
    rates.(1);
  Alcotest.check feq "rates[2]: q3 likewise -- nan" Float.nan rates.(2)

(* ------------------------------------------------------------------------ *)
(* ADV20: the instrument's own last 20 final bars, not the panel's dates     *)
(* ------------------------------------------------------------------------ *)

(* "ADV25" has 25 final bars dated day 1 through day 25, volumes 1.0 through
   25.0 in date order, PLUS one more bar dated day 26 -- after this panel's
   [as_of] -- with an outlier volume (9999) and close (500.0) that would be
   obvious in either figure if it leaked in. The ETF calendar is day 1 and
   day 25 (window 1), so [as_of] is day 25 and day 26 is one day past it.

   Its last 20 bars ON OR BEFORE as_of are days 6-25:

     (6 + 7 + ... + 25) / 20 = ((6 + 25) * 20 / 2) / 20 = 310 / 20 = 15.5

   and its last close on or before as_of is day 25's, 100.0 -- not day 26's
   500.0. "ADV19" has only 19 final bars (all before as_of), so its ADV is
   [None] -- one short of the floor, deliberately, rather than testing only a
   case far below it. *)
let test_adv () =
  let day k = Date.add_days (date "2026-04-01") (k - 1) in
  let adv25_bars =
    List.init 25 ~f:(fun i -> bar (day (i + 1)) 100.0 (Float.of_int (i + 1)))
    @ [ bar (day 26) 500.0 9999.0 ]
  in
  let adv19_bars =
    List.init 19 ~f:(fun i -> bar (day (i + 1)) 100.0 (Float.of_int (i + 1)))
  in
  let etfs = etf_bars_5 (List.init 5 ~f:(fun _ -> [ (day 1, 100.0); (day 25, 100.0) ])) in
  let bars =
    Symbol.Map.of_alist_exn
      ((sym "ADV25", adv25_bars) :: (sym "ADV19", adv19_bars) :: etfs)
  in
  let dgs10 = [ (day 1, 4.00) ] in
  let panel =
    get_ok
      (LP.build ~bars ~dgs10
         ~instruments:[ sym "ADV25"; sym "ADV19" ]
         ~window:1 ~now:(utc "2026-04-27" "00:16:00"))
  in
  Alcotest.(check bool) "as_of is day 25" true (Date.equal panel.LP.as_of (day 25));
  Alcotest.(check (option (float 1e-12)))
    "ADV25: mean of volumes 6..25 = 310 / 20 = 15.5, day 26's 9999 excluded" (Some 15.5)
    (Map.find_exn panel.LP.adv20 (sym "ADV25"));
  Alcotest.(check (option (float 1e-12)))
    "ADV25's last close is day 25's (100.0), not day 26's look-ahead 500.0" (Some 100.0)
    (Map.find_exn panel.LP.last_close (sym "ADV25"));
  Alcotest.(check (option (float 1e-12)))
    "ADV19: 19 bars is below the floor of 20" None
    (Map.find_exn panel.LP.adv20 (sym "ADV19"))

(* ------------------------------------------------------------------------ *)
(* Errors                                                                    *)
(* ------------------------------------------------------------------------ *)

let test_error_etf_missing () =
  let d1 = date "2026-05-01" and d2 = date "2026-05-02" in
  let some_etf_bars = [ bar d1 100.0 0.0; bar d2 101.0 0.0 ] in
  let bars =
    Symbol.Map.of_alist_exn
      [
        (sym "SPY", some_etf_bars);
        (sym "IWM", some_etf_bars);
        (* IWD is absent entirely. *)
        (sym "IWF", some_etf_bars);
        (sym "MTUM", some_etf_bars);
      ]
  in
  let message =
    get_error
      (LP.build ~bars
         ~dgs10:[ (d1, 4.0) ]
         ~instruments:[] ~window:1 ~now:(utc "2026-05-03" "00:16:00"))
  in
  Alcotest.(check bool)
    "names the ETF with no final bars" true
    (String.is_substring message ~substring:"IWD")

let test_error_too_few_common_dates () =
  let d1 = date "2026-06-01" and d2 = date "2026-06-02" in
  let bars =
    Symbol.Map.of_alist_exn
      (etf_bars_5 (List.init 5 ~f:(fun _ -> [ (d1, 100.0); (d2, 101.0) ])))
  in
  (* window 2 needs 3 common dates; only 2 exist. *)
  let message =
    get_error
      (LP.build ~bars
         ~dgs10:[ (d1, 4.0) ]
         ~instruments:[] ~window:2 ~now:(utc "2026-06-03" "00:16:00"))
  in
  Alcotest.(check bool)
    "names the shortfall" true
    (String.is_substring message ~substring:"2 common ETF dates")

let test_error_no_dgs10 () =
  let d1 = date "2026-07-01" and d2 = date "2026-07-02" in
  let bars =
    Symbol.Map.of_alist_exn
      (etf_bars_5 (List.init 5 ~f:(fun _ -> [ (d1, 100.0); (d2, 101.0) ])))
  in
  let message =
    get_error
      (LP.build ~bars ~dgs10:[] ~instruments:[] ~window:1
         ~now:(utc "2026-07-03" "00:16:00"))
  in
  Alcotest.(check string)
    "exact reason, empty DGS10 history"
    "long_panel: no DGS10 level on or before the first panel date" message

(* DGS10's first print is dated the SECOND panel date, f2 -- nothing at or
   before f1. This must refuse on the FIRST panel date, f1, not the last:
   a check mistakenly made against [dates.(window)] instead of [dates.(0)]
   would find DGS10's one print sitting right at or before the last date and
   wrongly return [Ok]. *)
let test_error_dgs10_starts_at_second_panel_date () =
  let f1 = date "2026-11-01" and f2 = date "2026-11-02" in
  let bars =
    Symbol.Map.of_alist_exn
      (etf_bars_5 (List.init 5 ~f:(fun _ -> [ (f1, 100.0); (f2, 101.0) ])))
  in
  let message =
    get_error
      (LP.build ~bars
         ~dgs10:[ (f2, 4.0) ]
         ~instruments:[] ~window:1 ~now:(utc "2026-11-03" "00:16:00"))
  in
  Alcotest.(check string)
    "refused on the first panel date, not the last"
    "long_panel: no DGS10 level on or before the first panel date" message

(* DGS10's last print, 2026-08-25, is entirely before the panel's first date,
   2026-09-01: not a trailing-run gap (ruling 2) but a stale history that
   never touches the panel at all. [closest_key] alone would still find that
   print (it IS <= the first panel date) and hand back a panel whose entire
   [rates] array is nan -- which the brief forbids outright, hence a hard
   Error naming the last print instead. *)
let test_error_stale_dgs10 () =
  let g1 = date "2026-09-01" and g2 = date "2026-09-02" in
  let bars =
    Symbol.Map.of_alist_exn
      (etf_bars_5 (List.init 5 ~f:(fun _ -> [ (g1, 100.0); (g2, 101.0) ])))
  in
  let dgs10 = [ (date "2026-08-20", 3.95); (date "2026-08-25", 4.00) ] in
  let message =
    get_error
      (LP.build ~bars ~dgs10 ~instruments:[] ~window:1 ~now:(utc "2026-09-03" "00:16:00"))
  in
  Alcotest.(check string)
    "names the last print and says it is stale"
    "long_panel: DGS10 has published nothing since 2026-08-25, before the panel's first \
     date"
    message

(* Five common ETF dates, window 2 (needs 3): at least window + 3. The panel
   must keep the newest three, n3-n5, not the oldest three -- a mutant that
   sliced from the front of [sorted_dates] instead of the back would still
   produce a 3-date panel, just the wrong one, and only checking WHICH three
   dates survived catches that. *)
let test_newest_dates_are_kept () =
  let n k = Date.add_days (date "2026-10-01") (k - 1) in
  let flat = [ (n 1, 100.0); (n 2, 100.0); (n 3, 100.0); (n 4, 100.0); (n 5, 100.0) ] in
  let bars = Symbol.Map.of_alist_exn (etf_bars_5 [ flat; flat; flat; flat; flat ]) in
  let dgs10 = List.map [ 1; 2; 3; 4; 5 ] ~f:(fun k -> (n k, 4.00)) in
  let panel =
    get_ok
      (LP.build ~bars ~dgs10 ~instruments:[] ~window:2 ~now:(utc "2026-10-06" "00:16:00"))
  in
  Alcotest.(check int)
    "3 panel dates kept out of 5 common ones" 3 (Array.length panel.LP.dates);
  Alcotest.(check bool)
    "the newest three, n3-n5, not n1-n3" true
    (Array.equal Date.equal panel.LP.dates [| n 3; n 4; n 5 |]);
  Alcotest.(check bool)
    "as_of is n5, the newest date" true
    (Date.equal panel.LP.as_of (n 5))

(* Three candidate dates: the day before yesterday, yesterday, and today.
   Every ETF has a bar dated today too, but [now] is mid-session today
   (14:00 UTC), so today's bar is not yet final and the panel must stop at
   yesterday. This is what catches a [build] that forgot to run
   [final_bars] at all: skipping it would let today's bar through, making
   THREE common dates instead of two, and (at window 1) the newest two kept
   would be yesterday and TODAY rather than the day before yesterday and
   yesterday. *)
let test_as_of_excludes_a_mid_session_bar () =
  let d0 =
    date "2026-08-10"
    (* the day before yesterday *)
  in
  let d1 =
    date "2026-08-11"
    (* yesterday *)
  in
  let d2 =
    date "2026-08-12"
    (* today *)
  in
  let flat = [ (d0, 100.0); (d1, 100.0); (d2, 100.0) ] in
  let bars = Symbol.Map.of_alist_exn (etf_bars_5 [ flat; flat; flat; flat; flat ]) in
  let dgs10 = [ (d0, 4.00); (d1, 4.00) ] in
  let panel =
    get_ok
      (LP.build ~bars ~dgs10 ~instruments:[] ~window:1 ~now:(utc "2026-08-12" "14:00:00"))
  in
  Alcotest.(check int)
    "only d0 and d1 are final; today's bar is excluded" 2 (Array.length panel.LP.dates);
  Alcotest.(check bool)
    "dates are d0, d1" true
    (Array.equal Date.equal panel.LP.dates [| d0; d1 |]);
  Alcotest.(check bool)
    "as_of is yesterday, not today" true
    (Date.equal panel.LP.as_of d1)

(* ------------------------------------------------------------------------ *)
(* build never raises -- every malformed input is an Error                  *)
(* ------------------------------------------------------------------------ *)

(* SPY has two final bars dated the same day. Processed first among the five
   ETFs (Factor.etfs' own order), so this is the error [build] returns. *)
let test_error_duplicate_etf_bar () =
  let d1 = date "2026-12-01" and d2 = date "2026-12-02" in
  let spy_bars = [ bar d1 100.0 0.0; bar d1 101.0 0.0; bar d2 102.0 0.0 ] in
  let clean = [ bar d1 100.0 0.0; bar d2 101.0 0.0 ] in
  let bars =
    Symbol.Map.of_alist_exn
      [
        (sym "SPY", spy_bars);
        (sym "IWM", clean);
        (sym "IWD", clean);
        (sym "IWF", clean);
        (sym "MTUM", clean);
      ]
  in
  let message =
    get_error
      (LP.build ~bars
         ~dgs10:[ (d1, 4.0) ]
         ~instruments:[] ~window:1 ~now:(utc "2026-12-03" "00:16:00"))
  in
  Alcotest.(check string)
    "names the ETF and the duplicated date"
    "long_panel: SPY has two final bars dated 2026-12-01" message

(* DGS10 prints two different levels for the same date. *)
let test_error_duplicate_dgs10 () =
  let d1 = date "2027-01-01" and d2 = date "2027-01-02" in
  let bars =
    Symbol.Map.of_alist_exn
      (etf_bars_5 (List.init 5 ~f:(fun _ -> [ (d1, 100.0); (d2, 101.0) ])))
  in
  let dgs10 = [ (d1, 4.00); (d1, 4.05) ] in
  let message =
    get_error
      (LP.build ~bars ~dgs10 ~instruments:[] ~window:1 ~now:(utc "2027-01-03" "00:16:00"))
  in
  Alcotest.(check string)
    "names DGS10 and the duplicated date"
    "long_panel: DGS10 has two levels dated 2027-01-01" message

(* An instrument named twice in ~instruments is deduplicated, not an error --
   [build] returns Ok with exactly one entry per map, not two, and not a
   crash from a doubled key. *)
let test_duplicate_instrument_is_deduplicated () =
  let d1 = date "2027-02-01" and d2 = date "2027-02-02" in
  let dupi_bars = [ bar d1 10.0 1.0; bar d2 11.0 1.0 ] in
  let bars =
    Symbol.Map.of_alist_exn
      ((sym "DUPI", dupi_bars)
      :: etf_bars_5 (List.init 5 ~f:(fun _ -> [ (d1, 100.0); (d2, 101.0) ])))
  in
  let panel =
    get_ok
      (LP.build ~bars
         ~dgs10:[ (d1, 4.0) ]
         ~instruments:[ sym "DUPI"; sym "DUPI" ]
         ~window:1 ~now:(utc "2027-02-03" "00:16:00"))
  in
  Alcotest.(check int)
    "one entry, not two, for a symbol listed twice" 1 (Map.length panel.LP.returns);
  Alcotest.(check bool)
    "the one entry is DUPI's" true
    (Map.mem panel.LP.returns (sym "DUPI"))

(* window must be at least 1: zero and negative windows are both refused
   rather than left to crash on an empty or negative-length array slice. *)
let test_error_bad_window () =
  let d1 = date "2027-03-01" and d2 = date "2027-03-02" in
  let bars =
    Symbol.Map.of_alist_exn
      (etf_bars_5 (List.init 5 ~f:(fun _ -> [ (d1, 100.0); (d2, 101.0) ])))
  in
  let build_with window =
    get_error
      (LP.build ~bars
         ~dgs10:[ (d1, 4.0) ]
         ~instruments:[] ~window ~now:(utc "2027-03-03" "00:16:00"))
  in
  Alcotest.(check string)
    "window 0 is refused" "long_panel: window must be at least 1, got 0" (build_with 0);
  Alcotest.(check string)
    "a negative window is refused" "long_panel: window must be at least 1, got -3"
    (build_with (-3))

let suite =
  ( "long_panel",
    [
      Alcotest.test_case "final_bars: drops a bar not yet final, keeps one that is" `Quick
        test_final_bars;
      Alcotest.test_case
        "window of 2: ETF-intersection panel dates; alignment across a gap; an uncovered \
         instrument is not an error"
        `Quick test_window_of_two;
      Alcotest.test_case "a bond holiday reads flat, and the next date carries the move"
        `Quick test_bond_holiday;
      Alcotest.test_case "unpublished DGS10 leaves the trailing dates nan, never 0.0"
        `Quick test_unpublished_dgs10;
      Alcotest.test_case "ADV20: the instrument's own last 20 final bars" `Quick test_adv;
      Alcotest.test_case "Error: an ETF with no final bars" `Quick test_error_etf_missing;
      Alcotest.test_case "Error: fewer than window + 1 common ETF dates" `Quick
        test_error_too_few_common_dates;
      Alcotest.test_case "Error: no DGS10 level on or before the first panel date" `Quick
        test_error_no_dgs10;
      Alcotest.test_case
        "Error: DGS10 starting at the second panel date is refused on the first, not the \
         last"
        `Quick test_error_dgs10_starts_at_second_panel_date;
      Alcotest.test_case "Error: stale DGS10 (last print before the panel's first date)"
        `Quick test_error_stale_dgs10;
      Alcotest.test_case "the newest window + 1 common dates are kept, not the oldest"
        `Quick test_newest_dates_are_kept;
      Alcotest.test_case
        "as_of excludes a mid-session bar every ETF has, even though final_bars would \
         otherwise make three common dates instead of two"
        `Quick test_as_of_excludes_a_mid_session_bar;
      Alcotest.test_case "Error: an ETF with two final bars dated the same day" `Quick
        test_error_duplicate_etf_bar;
      Alcotest.test_case "Error: DGS10 with two levels dated the same day" `Quick
        test_error_duplicate_dgs10;
      Alcotest.test_case "an instrument listed twice is deduplicated, not an error" `Quick
        test_duplicate_instrument_is_deduplicated;
      Alcotest.test_case "Error: window must be at least 1" `Quick test_error_bad_window;
    ] )

(* Unit tests for liquidity.ml.

   Every expected value is hand-derived, per the project convention, and the
   derivation sits in a comment above the assertion that checks it. Four
   groups:

     ONE POSITION    the worked example from the A4 brief: a single position
                     with everything known, checked field by field.

     A SHORT         the same position, sold instead of bought. Every figure
                     is scored on a magnitude (|qty|, |x|), never on the
                     signed exposure, so a short of the same size costs
                     exactly as much to unwind as the long did.

     IDENTITIES      zero half-spreads collapse LVaR to VaR exactly; LVaR is
                     never below VaR; and the book-level totals are the sums
                     (or, for days, the max) of the individual lines' own
                     figures -- checked against the lines' OWN returned
                     values, not against a second hand computation, because
                     that is the actual claim under test ("totals are built
                     from lines"), not "the arithmetic is right twice".

     UNKNOWN         a nonzero position with no ADV (or no sigma) reports
                     [None] on its own line and poisons the book totals; a
                     zero-quantity position with the same missing data
                     reports zeros throughout, because there is nothing to
                     unwind and "nothing" is a known quantity. *)

open Core
module L = Ohcamel.Liquidity
module Symbol = Ohcamel.Types.Symbol

let feq = Alcotest.float 1e-9
let sym s = Symbol.of_string s

let get_opt name = function
  | Some x -> x
  | None -> Alcotest.failf "%s: expected Some, got None" name

let check_none name = function
  | None -> ()
  | Some _ -> Alcotest.failf "%s: expected None, got Some" name

(* ------------------------------------------------------------------------ *)
(* ONE POSITION -- the brief's worked example.                              *)
(* ------------------------------------------------------------------------ *)

(* q = 10,000 shares at $100 -> x = $1,000,000 dollar exposure.
   ADV20 = 1,000,000 shares. participation = 0.10. sigma_daily = 0.02.
   Y (impact_coefficient) = 1.0. half-spread = 5 bps.

     days   = |q| / (p * ADV) = 10,000 / (0.10 * 1,000,000)
            = 10,000 / 100,000
            = 0.1

     impact fraction = Y * sigma * sqrt(|q| / ADV)
                      = 1.0 * 0.02 * sqrt(10,000 / 1,000,000)
                      = 0.02 * sqrt(0.01)
                      = 0.02 * 0.1
                      = 0.002

     impact cost = impact_fraction * |x| = 0.002 * 1,000,000 = 2,000

     spread cost = |x| * half_spread_bps * 1e-4
                 = 1,000,000 * 5 * 1e-4
                 = 1,000,000 * 0.0005
                 = 500

   With a book VaR of 50,000: LVaR = VaR + spread_cost = 50,000 + 500 = 50,500.

   Every one of these five numbers lands on an exact double (checked: 0.1 *
   1_000_000.0 = 100_000.0 exactly, and 10_000. /. 100_000. = 0.1 exactly in
   IEEE-754 double, likewise 0.02 *. sqrt 0.01 = 0.002), so 1e-9 is generous
   rather than load-bearing here; it is the identities below where the
   tolerance is doing real work. *)
let one_position =
  {
    L.symbol = sym "AAPL";
    qty = 10_000.0;
    price = 100.0;
    adv20 = Some 1_000_000.0;
    daily_stddev = Some 0.02;
    half_spread_bps = 5.0;
  }

let test_one_position () =
  let t = L.compute ~participation:0.10 ~impact_coefficient:1.0 [ one_position ] in
  let line = List.hd_exn t.lines in
  Alcotest.check feq "days to liquidate" 0.1
    (get_opt "days" line.L.Line.days_to_liquidate);
  Alcotest.check feq "impact fraction" 0.002
    (get_opt "impact fraction" line.L.Line.impact_fraction);
  Alcotest.check feq "impact cost" 2_000.0 (get_opt "impact cost" line.L.Line.impact_cost);
  Alcotest.check feq "spread cost" 500.0 line.L.Line.spread_cost;
  (* Totals of a single-line book equal that line's own figures. *)
  Alcotest.check feq "total spread cost" 500.0 t.L.spread_cost;
  Alcotest.check feq "total impact cost" 2_000.0
    (get_opt "total impact cost" t.L.impact_cost);
  Alcotest.check feq "max days to liquidate" 0.1
    (get_opt "max days" t.L.max_days_to_liquidate);
  Alcotest.check feq "LVaR = VaR + spread cost" 50_500.0 (L.lvar ~var:50_000.0 t)

(* ------------------------------------------------------------------------ *)
(* A SHORT POSITION -- same magnitudes.                                     *)
(* ------------------------------------------------------------------------ *)

(* q = -10,000 (sold, not bought). |q| = 10,000 exactly as before, and
   x = q * price = -1,000,000, so |x| = 1,000,000 exactly as before too. Every
   formula above reads only |q| and |x|, so days, impact fraction, impact cost
   and spread cost are bit-for-bit the same five numbers as the long case:
   days = 0.1, impact fraction = 0.002, impact cost = 2,000, spread cost = 500,
   LVaR at VaR 50,000 = 50,500. *)
let short_position = { one_position with L.qty = -10_000.0 }

let test_short_position () =
  let t = L.compute ~participation:0.10 ~impact_coefficient:1.0 [ short_position ] in
  let line = List.hd_exn t.lines in
  Alcotest.check feq "days to liquidate" 0.1
    (get_opt "days" line.L.Line.days_to_liquidate);
  Alcotest.check feq "impact fraction" 0.002
    (get_opt "impact fraction" line.L.Line.impact_fraction);
  Alcotest.check feq "impact cost" 2_000.0 (get_opt "impact cost" line.L.Line.impact_cost);
  Alcotest.check feq "spread cost" 500.0 line.L.Line.spread_cost;
  Alcotest.check feq "LVaR = VaR + spread cost" 50_500.0 (L.lvar ~var:50_000.0 t)

(* ------------------------------------------------------------------------ *)
(* IDENTITIES                                                                *)
(* ------------------------------------------------------------------------ *)

(* Zero half-spreads: spread_cost is a sum of |x| * 0 * 1e-4 terms, each
   exactly 0.0 in floating point (no cancellation, no rounding -- multiplying
   any finite double by 0.0 is exactly 0.0), so the total spread_cost is
   exactly 0.0 and LVaR = VaR + 0.0 is VaR bit-for-bit, not merely to a
   tolerance. *)
let test_zero_spread_gives_lvar_equal_var () =
  let p = { one_position with L.half_spread_bps = 0.0 } in
  let t = L.compute ~participation:0.10 ~impact_coefficient:1.0 [ p ] in
  Alcotest.(check bool)
    "spread cost is exactly zero" true
    (Float.equal t.L.spread_cost 0.0);
  Alcotest.(check bool)
    "LVaR equals VaR exactly" true
    (Float.equal (L.lvar ~var:50_000.0 t) 50_000.0)

(* LVaR >= VaR always, because spread_cost is a sum of |x| * bps * 1e-4 terms
   and every factor is nonnegative -- half_spread_bps is never negative in a
   valid book, |x| is a magnitude, and 1e-4 is a positive constant. Checked
   here on a two-line book with a mix of long and short, nonzero spreads. *)
let test_lvar_at_least_var () =
  let p1 = one_position in
  let p2 = { short_position with L.symbol = sym "MSFT"; half_spread_bps = 12.0 } in
  let t = L.compute ~participation:0.10 ~impact_coefficient:1.0 [ p1; p2 ] in
  Alcotest.(check bool)
    "LVaR is at least VaR" true
    (Float.( >= ) (L.lvar ~var:50_000.0 t) 50_000.0)

(* Totals are the sums of the lines (days: the max), on a three-line book
   mixing sizes, sides, ADVs, sigmas and spreads so the check is not
   accidentally passing on a degenerate one-line case. The expected totals
   below are built from the SAME lines' OWN returned fields, which is the
   actual property under test: the book-level numbers are not independently
   recomputed from the positions, they are the aggregate of what [compute]
   already put in [lines]. *)
let mixed_positions =
  [
    {
      L.symbol = sym "AAPL";
      qty = 10_000.0;
      price = 50.0;
      adv20 = Some 500_000.0;
      daily_stddev = Some 0.03;
      half_spread_bps = 8.0;
    };
    {
      L.symbol = sym "MSFT";
      qty = -5_000.0;
      price = 200.0;
      adv20 = Some 2_000_000.0;
      daily_stddev = Some 0.015;
      half_spread_bps = 3.0;
    };
    {
      L.symbol = sym "GOOG";
      qty = 0.0;
      price = 150.0;
      adv20 = Some 800_000.0;
      daily_stddev = Some 0.025;
      half_spread_bps = 4.0;
    };
  ]

let test_totals_are_sums_of_lines () =
  let t = L.compute ~participation:0.05 ~impact_coefficient:1.2 mixed_positions in
  Alcotest.(check int) "three lines" 3 (List.length t.L.lines);
  let expected_spread =
    List.fold t.L.lines ~init:0.0 ~f:(fun acc l -> acc +. l.L.Line.spread_cost)
  in
  let expected_impact =
    List.fold t.L.lines ~init:0.0 ~f:(fun acc l ->
        acc +. get_opt "line impact cost" l.L.Line.impact_cost)
  in
  let expected_max_days =
    List.fold t.L.lines ~init:0.0 ~f:(fun acc l ->
        Float.max acc (get_opt "line days" l.L.Line.days_to_liquidate))
  in
  Alcotest.check feq "spread cost is the sum of the lines'" expected_spread
    t.L.spread_cost;
  Alcotest.check feq "impact cost is the sum of the lines'" expected_impact
    (get_opt "total impact cost" t.L.impact_cost);
  Alcotest.check feq "max days is the max of the lines'" expected_max_days
    (get_opt "max days to liquidate" t.L.max_days_to_liquidate);
  (* And the sums are not trivially zero or trivially equal to one line, so the
     assertions above are actually exercising three distinct lines. *)
  Alcotest.(check bool) "spread cost is positive" true (Float.( > ) t.L.spread_cost 0.0);
  Alcotest.(check bool)
    "impact cost is positive" true
    (Float.( > ) (get_opt "total impact cost" t.L.impact_cost) 0.0)

(* ------------------------------------------------------------------------ *)
(* UNKNOWN IS NOT ZERO                                                       *)
(* ------------------------------------------------------------------------ *)

(* A nonzero position missing ADV: its own days, impact fraction and impact
   cost are all [None] -- not merely the ones that mention ADV syntactically,
   because "days" alone does not need sigma but the module treats a missing
   EITHER input as making the whole line's derived figures unknown, per the
   brief. That in turn poisons the book totals: impact_cost and
   max_days_to_liquidate are [None], even though spread_cost (which needs
   neither ADV nor sigma) is still a plain, known float. *)
let test_missing_adv_nulls_the_line_and_the_totals () =
  let p =
    {
      L.symbol = sym "NEWCO";
      qty = 1_000.0;
      price = 20.0;
      adv20 = None;
      daily_stddev = Some 0.03;
      half_spread_bps = 10.0;
    }
  in
  let t = L.compute ~participation:0.10 ~impact_coefficient:1.0 [ p ] in
  let line = List.hd_exn t.L.lines in
  check_none "days with no ADV" line.L.Line.days_to_liquidate;
  check_none "impact fraction with no ADV" line.L.Line.impact_fraction;
  check_none "impact cost with no ADV" line.L.Line.impact_cost;
  (* |x| = 1,000 * 20 = 20,000; spread cost = 20,000 * 10 * 1e-4 = 20,000 * 0.001 = 20. *)
  Alcotest.check feq "spread cost is still known" 20.0 line.L.Line.spread_cost;
  check_none "total impact cost" t.L.impact_cost;
  check_none "total max days" t.L.max_days_to_liquidate

(* The other missing input gives the same answer: no sigma nulls the line's
   three derived figures and the totals, exactly as no ADV did above. *)
let test_missing_sigma_nulls_the_line_and_the_totals () =
  let p =
    {
      L.symbol = sym "NEWCO";
      qty = 1_000.0;
      price = 20.0;
      adv20 = Some 400_000.0;
      daily_stddev = None;
      half_spread_bps = 10.0;
    }
  in
  let t = L.compute ~participation:0.10 ~impact_coefficient:1.0 [ p ] in
  let line = List.hd_exn t.L.lines in
  check_none "days with no sigma" line.L.Line.days_to_liquidate;
  check_none "impact fraction with no sigma" line.L.Line.impact_fraction;
  check_none "impact cost with no sigma" line.L.Line.impact_cost;
  check_none "total impact cost" t.L.impact_cost;
  check_none "total max days" t.L.max_days_to_liquidate

(* A ZERO position with the same missing ADV and sigma: there is nothing to
   unwind, so the line's three figures are known zeros rather than unknowns,
   and the totals -- unlike the nonzero case above -- are NOT [None]. *)
let test_zero_qty_with_missing_data_is_not_none () =
  let p =
    {
      L.symbol = sym "NEWCO";
      qty = 0.0;
      price = 20.0;
      adv20 = None;
      daily_stddev = None;
      half_spread_bps = 10.0;
    }
  in
  let t = L.compute ~participation:0.10 ~impact_coefficient:1.0 [ p ] in
  let line = List.hd_exn t.L.lines in
  Alcotest.check feq "days is a known zero" 0.0
    (get_opt "days" line.L.Line.days_to_liquidate);
  Alcotest.check feq "impact fraction is a known zero" 0.0
    (get_opt "impact fraction" line.L.Line.impact_fraction);
  Alcotest.check feq "impact cost is a known zero" 0.0
    (get_opt "impact cost" line.L.Line.impact_cost);
  Alcotest.check feq "spread cost is zero (x = 0)" 0.0 line.L.Line.spread_cost;
  Alcotest.check feq "total impact cost is a known zero" 0.0
    (get_opt "total impact cost" t.L.impact_cost);
  Alcotest.check feq "total max days is a known zero" 0.0
    (get_opt "total max days" t.L.max_days_to_liquidate)

(* ------------------------------------------------------------------------ *)
(* REFUSED ARGUMENTS                                                         *)
(* ------------------------------------------------------------------------ *)

let check_invalid_arg name f =
  match f () with
  | exception Invalid_argument _ -> ()
  | exception e ->
      Alcotest.failf "%s: expected Invalid_argument, got %s" name (Exn.to_string e)
  | _ -> Alcotest.failf "%s: expected Invalid_argument, got a value" name

let test_invalid_inputs () =
  check_invalid_arg "participation = 0" (fun () ->
      L.compute ~participation:0.0 ~impact_coefficient:1.0 [ one_position ]);
  check_invalid_arg "participation negative" (fun () ->
      L.compute ~participation:(-0.1) ~impact_coefficient:1.0 [ one_position ]);
  check_invalid_arg "participation > 1" (fun () ->
      L.compute ~participation:1.5 ~impact_coefficient:1.0 [ one_position ]);
  check_invalid_arg "impact_coefficient negative" (fun () ->
      L.compute ~participation:0.10 ~impact_coefficient:(-1.0) [ one_position ]);
  (* Boundaries are valid, not refused: participation = 1.0 (full ADV
     participation) and impact_coefficient = 0.0 (impact switched off) both
     compute without raising. *)
  ignore (L.compute ~participation:1.0 ~impact_coefficient:1.0 [ one_position ] : L.t);
  ignore (L.compute ~participation:0.10 ~impact_coefficient:0.0 [ one_position ] : L.t)

let suite =
  ( "liquidity",
    [
      Alcotest.test_case "one position, hand-derived" `Quick test_one_position;
      Alcotest.test_case "a short position gives the same magnitudes" `Quick
        test_short_position;
      Alcotest.test_case "zero spread gives LVaR = VaR exactly" `Quick
        test_zero_spread_gives_lvar_equal_var;
      Alcotest.test_case "LVaR is at least VaR" `Quick test_lvar_at_least_var;
      Alcotest.test_case "totals are the sums (max, for days) of the lines" `Quick
        test_totals_are_sums_of_lines;
      Alcotest.test_case "missing ADV nulls the line and the totals" `Quick
        test_missing_adv_nulls_the_line_and_the_totals;
      Alcotest.test_case "missing sigma nulls the line and the totals" `Quick
        test_missing_sigma_nulls_the_line_and_the_totals;
      Alcotest.test_case "a zero position with missing data is not None" `Quick
        test_zero_qty_with_missing_data_is_not_none;
      Alcotest.test_case "refused arguments raise Invalid_argument" `Quick
        test_invalid_inputs;
    ] )

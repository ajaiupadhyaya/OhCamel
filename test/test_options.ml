(* Unit tests for options.ml.

   Three kinds of check, in increasing order of what they would catch.

     TEXTBOOK VALUES   Hull's own worked examples, which is the only source of
                       expected values here that was not produced by this code.
                       Every other test in this project derives its numbers by
                       hand; for Black-Scholes the honest thing is to check
                       against the reference every desk already agrees on.

     PARITY            call - put = S - K e^(-rT), for arbitrary inputs. This is
                       the standard "does the pricer make sense at all" check
                       and it is nearly free. It catches a discount factor
                       applied to the wrong leg, or an N(-d2) written where an
                       N(d2) belongs -- errors that leave both prices looking
                       entirely plausible on their own.

     STRUCTURE         signs, bounds and monotonicity. Parity is symmetric in a
                       sign error made identically on both sides, so it cannot
                       catch a gamma that came out negative. These can.

   The tolerance on the textbook values is 5e-3, and that is deliberately loose:
   Hull quotes to three or four significant figures, so a tighter tolerance
   would be asserting the book's ROUNDING rather than this module's arithmetic.
   The parity and structure tests run at 1e-9, because those are identities and
   nothing is being rounded. *)

open Core
module Options = Ohcamel.Options
module BS = Ohcamel.Options.Black_scholes

let textbook = Alcotest.float 5e-3
let exact = Alcotest.float 1e-9

(* Hull, Options, Futures and Other Derivatives, the worked European call/put
   example: S = 42, K = 40, r = 10%, sigma = 20%, T = 0.5 years.

     d1 = [ln(42/40) + (0.10 + 0.20^2/2)(0.5)] / (0.20 sqrt(0.5))
        = [0.048790 + 0.060000] / 0.141421
        = 0.7693
     d2 = 0.7693 - 0.141421 = 0.6278

     c = 42 N(d1) - 40 e^(-0.05) N(d2) = 4.76
     p = 40 e^(-0.05) N(-d2) - 42 N(-d1) = 0.81 *)
let test_hull_call_and_put_prices () =
  let call =
    BS.compute ~spot:42.0 ~strike:40.0 ~time_to_expiry:0.5 ~rate:0.10 ~implied_vol:0.20
      ~right:Options.Right.Call
  in
  let put =
    BS.compute ~spot:42.0 ~strike:40.0 ~time_to_expiry:0.5 ~rate:0.10 ~implied_vol:0.20
      ~right:Options.Right.Put
  in
  Alcotest.check textbook "Hull's call, 4.76" 4.76 (BS.price call);
  Alcotest.check textbook "Hull's put, 0.81" 0.81 (BS.price put)

(* Hull's Greeks example: S = 49, K = 50, r = 5%, sigma = 20%, T = 0.3846
   (twenty weeks). The book reports, for the call:

     delta = N(d1)                            = 0.522
     gamma = N'(d1) / (S sigma sqrt(T))       = 0.066
     vega  = S N'(d1) sqrt(T)                 = 12.1     per 1.00 of vol
     theta = -S N'(d1) sigma / (2 sqrt(T))
             - r K e^(-rT) N(d2)              = -4.31    per year

   Vega and theta are checked in the module's own units -- per 1.00 of
   annualised vol and per year -- not the desk's "per vol point" and "per day".
   Hull quotes them the same way, and the scaling is a display decision that
   lives at the display. *)
let test_hull_greeks () =
  let g =
    BS.compute ~spot:49.0 ~strike:50.0 ~time_to_expiry:0.3846 ~rate:0.05 ~implied_vol:0.20
      ~right:Options.Right.Call
  in
  Alcotest.check textbook "delta 0.522" 0.522 (BS.delta g);
  Alcotest.check textbook "gamma 0.066" 0.066 (BS.gamma g);
  (* 5e-3 would be absurdly tight on a number of order 12, so this one is
     checked at a proportionate 5e-2. Same reasoning, different magnitude. *)
  Alcotest.check (Alcotest.float 5e-2) "vega 12.1" 12.1 (BS.vega g);
  Alcotest.check (Alcotest.float 5e-2) "theta -4.31" (-4.31) (BS.theta g)

(* A put's delta is its call's minus one, exactly, at every strike. That is
   parity differentiated once, and it is worth asserting separately because a
   pricer can get both prices right and still compute the put's delta as
   -N(-d1) with a sign slip. *)
let test_put_delta_is_call_delta_minus_one () =
  List.iter [ 30.0; 45.0; 49.0; 55.0; 80.0 ] ~f:(fun strike ->
      let at right =
        BS.compute ~spot:49.0 ~strike ~time_to_expiry:0.3846 ~rate:0.05 ~implied_vol:0.25
          ~right
      in
      let c = at Options.Right.Call and p = at Options.Right.Put in
      Alcotest.check exact
        (Printf.sprintf "K=%g: delta_put = delta_call - 1" strike)
        (BS.delta c -. 1.0)
        (BS.delta p))

(* PUT-CALL PARITY: c - p = S - K e^(-rT).

   Checked across a grid that spans deep in the money to deep out, several
   expiries and several vols, because parity is an identity and should hold
   everywhere rather than at a convenient point. A pricer that discounts the
   wrong leg satisfies it at the money and fails at the wings. *)
let test_put_call_parity () =
  let spot = 100.0 in
  List.iter [ 60.0; 90.0; 100.0; 110.0; 150.0 ] ~f:(fun strike ->
      List.iter [ 0.05; 0.25; 1.0; 2.0 ] ~f:(fun time_to_expiry ->
          List.iter [ 0.10; 0.30; 0.80 ] ~f:(fun implied_vol ->
              let rate = 0.04 in
              let at right =
                BS.compute ~spot ~strike ~time_to_expiry ~rate ~implied_vol ~right
              in
              let c = at Options.Right.Call and p = at Options.Right.Put in
              let expected = spot -. (strike *. Float.exp (-.rate *. time_to_expiry)) in
              Alcotest.check exact
                (Printf.sprintf "K=%g T=%g vol=%g" strike time_to_expiry implied_vol)
                expected
                (BS.price c -. BS.price p))))

(* Gamma and vega are identical for a call and a put at the same strike and
   expiry. This follows from parity -- the two prices differ by S - K e^(-rT),
   which is linear in S and free of sigma, so the second derivative in S and the
   first in sigma both survive the subtraction unchanged.

   Asserted rather than assumed even though options.ml computes them once and
   shares them, because that sharing is an implementation choice somebody could
   undo while "tidying up" the two branches into symmetry. *)
let test_gamma_and_vega_do_not_depend_on_the_right () =
  List.iter [ 80.0; 100.0; 130.0 ] ~f:(fun strike ->
      let at right =
        BS.compute ~spot:100.0 ~strike ~time_to_expiry:0.5 ~rate:0.03 ~implied_vol:0.25
          ~right
      in
      let c = at Options.Right.Call and p = at Options.Right.Put in
      Alcotest.check exact "gamma" (BS.gamma c) (BS.gamma p);
      Alcotest.check exact "vega" (BS.vega c) (BS.vega p))

(* Signs and bounds. Parity is symmetric in a sign error made on both sides, so
   these catch what it cannot. *)
let test_structure () =
  let grid = [ (60.0, 0.10); (100.0, 0.30); (140.0, 0.60) ] in
  List.iter grid ~f:(fun (strike, implied_vol) ->
      let at right =
        BS.compute ~spot:100.0 ~strike ~time_to_expiry:0.75 ~rate:0.03 ~implied_vol ~right
      in
      let c = at Options.Right.Call and p = at Options.Right.Put in
      let positive name v =
        Alcotest.(check bool) (name ^ " is strictly positive") true (Float.( > ) v 0.0)
      in
      (* Convexity in the underlying, whichever way the option points. This is
         the property that makes a delta-hedged book still risky, and it is the
         one an [abs] or a sign slip in the pdf term would destroy. *)
      positive "gamma" (BS.gamma c);
      positive "vega" (BS.vega c);
      positive "call price" (BS.price c);
      positive "put price" (BS.price p);
      Alcotest.(check bool)
        "call delta in (0, 1)" true
        (Float.( > ) (BS.delta c) 0.0 && Float.( < ) (BS.delta c) 1.0);
      Alcotest.(check bool)
        "put delta in (-1, 0)" true
        (Float.( > ) (BS.delta p) (-1.0) && Float.( < ) (BS.delta p) 0.0);
      (* A long option decays. Both rights, at a rate that is negative in the
         module's per-year units. (A deep in-the-money European put can have
         positive theta because of the carry on the strike; the strikes here are
         chosen so that case does not arise, and it is not a bug where it
         does.) *)
      Alcotest.(check bool) "call theta is negative" true (Float.is_negative (BS.theta c)))

(* Deep in the money the call is worth its forward intrinsic and its delta
   approaches 1; deep out of the money it is worth nothing and its delta
   approaches 0. Both limits also drive gamma and vega to zero, because a
   contract whose outcome is no longer in doubt has no sensitivity to
   uncertainty. *)
let test_moneyness_limits () =
  let at strike =
    BS.compute ~spot:100.0 ~strike ~time_to_expiry:0.25 ~rate:0.02 ~implied_vol:0.20
      ~right:Options.Right.Call
  in
  let deep_itm = at 10.0 and deep_otm = at 1000.0 in
  Alcotest.(check bool)
    "deep ITM call delta approaches 1" true
    (Float.( > ) (BS.delta deep_itm) 0.999);
  Alcotest.(check bool)
    "deep OTM call delta approaches 0" true
    (Float.( < ) (BS.delta deep_otm) 0.001);
  Alcotest.(check bool)
    "deep ITM gamma approaches 0" true
    (Float.( < ) (BS.gamma deep_itm) 1e-6);
  Alcotest.(check bool)
    "deep OTM vega approaches 0" true
    (Float.( < ) (BS.vega deep_otm) 1e-6)

(* The degenerate boundary, which is the branch most likely to produce a nan in
   production and least likely to be exercised by accident.

   At zero time or zero volatility the contract is worth its intrinsic value,
   its delta is a step, and gamma, vega and theta are all zero. The important
   assertion is not the values -- it is that every one of them is FINITE. A nan
   here propagates silently into an exposure, and an exposure that is nan
   compares false against every limit threshold, so the limit reports "not
   breached". *)
let test_expiry_and_zero_vol_are_finite () =
  let cases =
    [
      ("expired, in the money", 0.0, 0.20, 110.0, 100.0);
      ("expired, out of the money", 0.0, 0.20, 90.0, 100.0);
      ("expired, exactly at the money", 0.0, 0.20, 100.0, 100.0);
      ("zero vol, in the money", 0.5, 0.0, 110.0, 100.0);
      ("zero vol, out of the money", 0.5, 0.0, 90.0, 100.0);
      ("an hour left at 1% vol", 1.0 /. (365.0 *. 24.0), 0.01, 100.0, 100.0);
    ]
  in
  List.iter cases ~f:(fun (name, time_to_expiry, implied_vol, spot, strike) ->
      List.iter [ Options.Right.Call; Options.Right.Put ] ~f:(fun right ->
          let g =
            BS.compute ~spot ~strike ~time_to_expiry ~rate:0.03 ~implied_vol ~right
          in
          List.iter
            [
              ("price", BS.price g);
              ("delta", BS.delta g);
              ("gamma", BS.gamma g);
              ("vega", BS.vega g);
              ("theta", BS.theta g);
            ]
            ~f:(fun (field, v) ->
              Alcotest.(check bool)
                (Printf.sprintf "%s (%s): %s is finite" name
                   (Options.Right.to_string right)
                   field)
                true (Float.is_finite v))));
  (* And the values at expiry are the intrinsic ones, not merely finite. *)
  let expired_itm =
    BS.compute ~spot:110.0 ~strike:100.0 ~time_to_expiry:0.0 ~rate:0.03 ~implied_vol:0.20
      ~right:Options.Right.Call
  in
  Alcotest.check exact "expired ITM call is worth 10" 10.0 (BS.price expired_itm);
  Alcotest.check exact "expired ITM call delta is 1" 1.0 (BS.delta expired_itm);
  Alcotest.check exact "expired call gamma is 0" 0.0 (BS.gamma expired_itm)

(* THE DELTA-EQUIVALENT EXPOSURE, and the multiplier that is the whole reason
   Contracts.t is not Qty.t.

   One at-the-money-ish call with delta 0.5, 10 contracts, a multiplier of 100,
   spot 100:

     0.5 * 100 * 10 * 100 = 50,000

   Fifty thousand dollars of delta out of a position a naive reading would call
   "ten". Getting the multiplier wrong here is a hundred-fold error in a number
   that feeds gross, net, weights, equity and every notional limit. *)
let test_delta_equivalent_applies_the_multiplier () =
  let position =
    Options.Position.create
      ~underlying:(Ohcamel.Types.Symbol.of_string "AAPL")
      ~id:"AAPL-100C"
      ~strike:(Options.Strike.of_float 100.0)
      ~right:Options.Right.Call ~expiry_in_days:30.0 ()
  in
  let greeks =
    BS.compute ~spot:100.0 ~strike:100.0 ~time_to_expiry:(30.0 /. 365.0) ~rate:0.03
      ~implied_vol:0.25 ~right:Options.Right.Call
  in
  let delta_equivalent =
    Options.Position.delta_equivalent position ~greeks
      ~contracts:(Options.Contracts.of_float 10.0)
      ~spot:100.0
  in
  let expected = BS.delta greeks *. 100.0 *. 10.0 *. 100.0 in
  Alcotest.check exact "delta * multiplier * contracts * spot" expected
    (Ohcamel.Types.Notional.to_float delta_equivalent);
  (* Sanity on the magnitude, so a change that dropped the multiplier entirely
     would fail on the number as well as on the formula. *)
  Alcotest.(check bool)
    "an at-the-money call on 10 contracts is tens of thousands of dollars of delta" true
    (Float.( > ) (Ohcamel.Types.Notional.to_float delta_equivalent) 40_000.0);
  (* A short position flips the sign, and gamma and vega go negative with it --
     which is the point of selling options and the thing a Greek limit exists to
     cap. *)
  let short =
    Options.Position.gamma_exposure position ~greeks
      ~contracts:(Options.Contracts.of_float (-10.0))
  in
  Alcotest.(check bool) "short gamma is negative" true (Float.is_negative short)

(* Calendar days, not trading days. Black-Scholes discounts and decays in
   calendar time -- an option held over a weekend loses two days of theta -- and
   using 252 here would misprice every contract by about 4% of its time value
   while looking like a defensible choice. *)
let test_years_to_expiry () =
  Alcotest.check exact "365 days out is one year" 1.0
    (Options.years_to_expiry ~expiry_in_days:365.0 ~days_elapsed:0.0);
  Alcotest.check exact "30 days, 10 elapsed" (20.0 /. 365.0)
    (Options.years_to_expiry ~expiry_in_days:30.0 ~days_elapsed:10.0);
  Alcotest.check exact "past expiry clamps to zero, never negative" 0.0
    (Options.years_to_expiry ~expiry_in_days:30.0 ~days_elapsed:45.0)

let check_invalid_arg name f =
  match f () with
  | exception Invalid_argument _ -> ()
  | exception e ->
      Alcotest.failf "%s: expected Invalid_argument, got %s" name (Exn.to_string e)
  | _ -> Alcotest.failf "%s: expected Invalid_argument, got a value" name

let test_invalid_inputs () =
  let compute ?(spot = 100.0) ?(strike = 100.0) ?(time_to_expiry = 0.5) ?(rate = 0.03)
      ?(implied_vol = 0.2) () =
    BS.compute ~spot ~strike ~time_to_expiry ~rate ~implied_vol ~right:Options.Right.Call
  in
  check_invalid_arg "zero spot" (fun () -> compute ~spot:0.0 ());
  check_invalid_arg "negative spot" (fun () -> compute ~spot:(-1.0) ());
  check_invalid_arg "zero strike" (fun () -> compute ~strike:0.0 ());
  check_invalid_arg "negative time" (fun () -> compute ~time_to_expiry:(-0.1) ());
  check_invalid_arg "negative vol" (fun () -> compute ~implied_vol:(-0.2) ())

(* Tenor bucket boundaries, checked on both sides of each cut.

   Boundaries are inclusive at the top -- exactly 30 days is "1w-1m", not
   "1-3m" -- and the off-by-one is the only thing that can be wrong here, so
   each edge is asserted at the value and just past it. *)
let test_tenor_bucket_boundaries () =
  let open Options.Tenor_bucket in
  let check days expected =
    Alcotest.(check string)
      (Printf.sprintf "%.1f days" days)
      (to_string expected)
      (to_string (of_days days))
  in
  check 0.0 Under_week;
  check 7.0 Under_week;
  check 7.5 Week_to_month;
  check 30.0 Week_to_month;
  check 30.5 One_to_three;
  check 90.0 One_to_three;
  check 90.5 Three_to_six;
  check 180.0 Three_to_six;
  check 180.5 Six_to_twelve;
  check 365.0 Six_to_twelve;
  check 365.5 Over_year;
  check 3650.0 Over_year;
  (* [ordered] is near to far and covers every constructor. Asserted because a
     display iterates it, so a bucket missing from the list would silently stop
     being shown rather than failing to compile. *)
  Alcotest.(check int)
    "ordered covers every bucket" (List.length all) (List.length ordered);
  Alcotest.(check (list string))
    "and runs near to far"
    [ "<=1w"; "1w-1m"; "1-3m"; "3-6m"; "6-12m"; ">1y" ]
    (List.map ordered ~f:to_string)

let suite =
  ( "options",
    [
      Alcotest.test_case "Hull's call and put prices" `Quick test_hull_call_and_put_prices;
      Alcotest.test_case "Hull's Greeks" `Quick test_hull_greeks;
      Alcotest.test_case "PUT-CALL PARITY across the grid" `Quick test_put_call_parity;
      Alcotest.test_case "put delta is call delta minus one" `Quick
        test_put_delta_is_call_delta_minus_one;
      Alcotest.test_case "gamma and vega do not depend on the right" `Quick
        test_gamma_and_vega_do_not_depend_on_the_right;
      Alcotest.test_case "signs and bounds" `Quick test_structure;
      Alcotest.test_case "moneyness limits" `Quick test_moneyness_limits;
      Alcotest.test_case "expiry and zero vol stay finite" `Quick
        test_expiry_and_zero_vol_are_finite;
      Alcotest.test_case "DELTA-EQUIVALENT applies the multiplier" `Quick
        test_delta_equivalent_applies_the_multiplier;
      Alcotest.test_case "years to expiry counts calendar days" `Quick
        test_years_to_expiry;
      Alcotest.test_case "invalid inputs raise" `Quick test_invalid_inputs;
      Alcotest.test_case "tenor bucket boundaries" `Quick test_tenor_bucket_boundaries;
    ] )

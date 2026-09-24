(* Unit tests for var_backtest.ml.

   The likelihood-ratio statistics are derived by hand below -- they are sums of
   four or five logarithms, which is tedious but entirely checkable, and pinning
   them is the only way to know the degenerate branches are not quietly
   returning nan. Each derivation is written above its test.

   Two tests here are not about numbers at all and matter more than the ones
   that are:

     the lookahead test, which asserts that a forecast is built from a window
     that stops one observation short of the day it predicts. A backtest with
     lookahead reports a model that works. It is the failure mode that makes
     every other number on the page a lie, and it cannot be spotted by reading
     the output.

     the fat-tail test, which shows the battery actually rejecting something.
     A validation suite that has never failed anything is not evidence. *)

open Core
module VB = Ohcamel.Var_backtest
module Obs = Ohcamel.Var_backtest.Observation

let feq = Alcotest.float 1e-9
let bools = Alcotest.(array bool)

let check_invalid_arg name f =
  match f () with
  | exception Invalid_argument _ -> ()
  | exception e ->
      Alcotest.failf "%s: expected Invalid_argument, got %s" name (Exn.to_string e)
  | _ -> Alcotest.failf "%s: expected Invalid_argument, but it returned" name

(* ------------------------------------------------------------------------ *)
(* Exceedance                                                                *)
(* ------------------------------------------------------------------------ *)

(* The sign convention is the one place this module could be wrong in a way
   that still produces plausible output: [var] is a positive loss magnitude and
   [realised] is a signed return, so a breach is [realised < -var]. Getting it
   backwards counts the GAINS as exceptions, which on a rising book gives a
   healthy-looking exception rate and a completely inverted test. *)
let test_exceedance_sign_convention () =
  let breach = Obs.create ~var:0.02 ~realised:(-0.03) in
  let inside = Obs.create ~var:0.02 ~realised:(-0.01) in
  let big_gain = Obs.create ~var:0.02 ~realised:0.50 in
  Alcotest.(check bool) "a 3% loss beats a 2% VaR" true (Obs.exceeded breach);
  Alcotest.(check bool) "a 1% loss does not" false (Obs.exceeded inside);
  Alcotest.(check bool) "and a 50% GAIN is not an exception" false (Obs.exceeded big_gain)

(* ------------------------------------------------------------------------ *)
(* Kupiec: unconditional coverage                                            *)
(* ------------------------------------------------------------------------ *)

(* n = 250, x = 10, confidence = 99% so p = 0.01 and pi = 10/250 = 0.04.

     restricted   = 240*ln(0.99) + 10*ln(0.01)
                  = 240*(-0.0100503358535) + 10*(-4.6051701859881)
                  = -2.4120806048403 - 46.0517018598809 = -48.4637824647213
     unrestricted = 240*ln(0.96) + 10*ln(0.04)
                  = 240*(-0.0408219945203) + 10*(-3.2188758248682)
                  = -9.7972786848612 - 32.1887582486820 = -41.9860369335433
     LR_uc        = -2 * (restricted - unrestricted) = 12.9554910623560

   Ten exceptions where 2.5 were expected: the model understates the tail by a
   factor of four, and the p-value is 0.00032. *)
let test_kupiec_too_many_exceptions () =
  let lr, p = VB.kupiec_pof ~n:250 ~x:10 ~confidence:0.99 in
  Alcotest.check feq "LR" 12.955491062356 lr;
  Alcotest.(check bool) "rejected" true (Float.( < ) p 0.001)

(* n = 100, x = 0, confidence = 95%.

     restricted   = 100*ln(0.95) + 0 = -5.1293294387551
     unrestricted = 100*ln(1.00) + 0 =  0            (the 0*ln 0 term vanishes)
     LR_uc        = 10.2586588775101,  p = 0.00136

   ZERO exceptions in a hundred days at 95% is a rejection, and this is the
   test that says so. A model that is never breached is not conservative, it is
   miscalibrated in the direction that makes every limit written against it
   slack. It also exercises the 0*ln 0 convention -- without it this branch
   returns nan and the p-value is nan, which prints as a blank rather than as a
   failure. *)
let test_kupiec_too_few_exceptions () =
  let lr, p = VB.kupiec_pof ~n:100 ~x:0 ~confidence:0.95 in
  Alcotest.check feq "LR" 10.2586588775101 lr;
  Alcotest.(check bool) "rejected, and not nan" true (Float.( < ) p 0.005)

(* pi = p exactly: the restricted and unrestricted likelihoods coincide, so the
   statistic is zero (up to the float cancellation of two ~50-magnitude sums)
   and the p-value is one. *)
let test_kupiec_perfect_calibration () =
  let lr, p = VB.kupiec_pof ~n:100 ~x:5 ~confidence:0.95 in
  Alcotest.check (Alcotest.float 1e-10) "LR is zero" 0.0 lr;
  Alcotest.check (Alcotest.float 1e-10) "p is one" 1.0 p

let test_kupiec_rejects_bad_input () =
  check_invalid_arg "n = 0" (fun () -> VB.kupiec_pof ~n:0 ~x:0 ~confidence:0.95);
  check_invalid_arg "x > n" (fun () -> VB.kupiec_pof ~n:10 ~x:11 ~confidence:0.95);
  check_invalid_arg "confidence 1" (fun () -> VB.kupiec_pof ~n:10 ~x:1 ~confidence:1.0)

(* ------------------------------------------------------------------------ *)
(* Christoffersen: independence                                              *)
(* ------------------------------------------------------------------------ *)

(* Clustered: [F F T T F F F F F F], n = 10, so nine transitions.

     n00 = 6, n01 = 1, n10 = 1, n11 = 1
     pi01 = 1/7, pi11 = 1/2, pi = 2/9

     restricted   = 7*ln(7/9) + 2*ln(2/9)
                  = 7*(-0.2513144282809) + 2*(-1.5040773967763) = -4.7673557915189
     unrestricted = 6*ln(6/7) + 1*ln(1/7) + 1*ln(1/2) + 1*ln(1/2)
                  = -0.9249040789636 - 1.9459101090932 - 1.3862943611199
                  = -4.2571085491767
     LR_ind       = -2*(-4.7673557915189 + 4.2571085491767) = 1.0204944047603

   p = 0.312: two consecutive exceptions out of ten days is suggestive and not
   significant, which is the honest answer on a sample this small. The test
   pins the arithmetic, not the conclusion. *)
let test_christoffersen_clustered () =
  let e = [| false; false; true; true; false; false; false; false; false; false |] in
  let lr, p = VB.christoffersen_independence ~exceedances:e in
  Alcotest.check feq "LR" 1.0204944047602744 lr;
  Alcotest.(check bool) "not significant on ten observations" true (Float.( > ) p 0.3)

(* Perfectly alternating: [T F T F T F].

     n00 = 0, n01 = 2, n10 = 3, n11 = 0
     pi01 = 1.0, pi11 = 0.0, pi = 2/5

     restricted   = 3*ln(0.6) + 2*ln(0.4) = -1.5324768712980 - 1.8325814637483
                  = -3.3650583350463
     unrestricted = 0 + 2*ln(1) + 3*ln(1) + 0 = 0
     LR_ind       = 6.7301166700926,  p = 0.0095

   Rejected -- and note the direction. These exceptions are ANTI-clustered:
   every one is followed by a calm day, which is just as much a violation of
   independence as bunching. The test is two-sided because the null is
   independence, not "not clustered".

   This case also runs three of the four terms through the 0*ln 0 convention at
   once, including ln(1 - pi01) with pi01 = 1, which is ln 0 guarded only by
   its count being zero. *)
let test_christoffersen_alternating () =
  let e = [| true; false; true; false; true; false |] in
  let lr, p = VB.christoffersen_independence ~exceedances:e in
  Alcotest.check feq "LR" 6.730116670092564 lr;
  Alcotest.(check bool) "anti-clustering is also dependence" true (Float.( < ) p 0.01)

(* No exceptions at all: there is no evidence about what follows a breach,
   because no breach was ever followed by anything. Statistic 0, p-value 1 --
   "no evidence of dependence", which is different from "evidence of
   independence" and is the correct thing to report. *)
let test_christoffersen_no_exceptions () =
  let lr, p = VB.christoffersen_independence ~exceedances:(Array.create ~len:20 false) in
  Alcotest.check feq "LR" 0.0 lr;
  Alcotest.check feq "p" 1.0 p

(* The same degenerate branch reached the other way: one exception, and it is
   the final observation, so it is never followed by anything either. *)
let test_christoffersen_exception_on_the_last_day () =
  let e = Array.init 20 ~f:(fun i -> i = 19) in
  let lr, p = VB.christoffersen_independence ~exceedances:e in
  Alcotest.check feq "LR" 0.0 lr;
  Alcotest.check feq "p" 1.0 p

let test_christoffersen_too_short () =
  let lr, p = VB.christoffersen_independence ~exceedances:[| true |] in
  Alcotest.check feq "one observation has no transitions" 0.0 lr;
  Alcotest.check feq "p" 1.0 p

(* ------------------------------------------------------------------------ *)
(* Basel traffic light                                                       *)
(* ------------------------------------------------------------------------ *)

(* The zones here are computed from the binomial rather than looked up, so the
   check is that the general formula reproduces the published table for the
   parameters it was published at: 250 days, 99% VaR, green 0-4, yellow 5-9,
   red 10 and above.

     P[X <= 4]  = 0.89218763  < 0.95     -> green
     P[X <= 5]  = 0.95881682  >= 0.95    -> yellow
     P[X <= 9]  = 0.99974981  < 0.9999   -> yellow
     P[X <= 10] = 0.99994610  >= 0.9999  -> red *)
let test_basel_reproduces_the_published_table () =
  let zone x = fst (VB.traffic_light ~n:250 ~x ~confidence:0.99) in
  let name =
    Alcotest.testable (fun f z -> Fmt.string f (VB.Zone.to_string z)) VB.Zone.equal
  in
  Alcotest.check name "0" VB.Zone.Green (zone 0);
  Alcotest.check name "4 is the top of green" VB.Zone.Green (zone 4);
  Alcotest.check name "5 is the bottom of yellow" VB.Zone.Yellow (zone 5);
  Alcotest.check name "9 is the top of yellow" VB.Zone.Yellow (zone 9);
  Alcotest.check name "10 is the bottom of red" VB.Zone.Red (zone 10)

let test_basel_cumulative_probability () =
  let _, cum = VB.traffic_light ~n:250 ~x:4 ~confidence:0.99 in
  Alcotest.check (Alcotest.float 1e-6) "P[X <= 4]" 0.89218763 cum

(* ------------------------------------------------------------------------ *)
(* Point-in-time discipline                                                  *)
(* ------------------------------------------------------------------------ *)

let calm = Array.init 300 ~f:(fun i -> if i % 2 = 0 then 0.001 else -0.001)

(* Structural: observation i's forecast must equal the estimator applied to
   returns[i .. i+window-1] -- a slice that ends exactly one element before the
   day being forecast. Recomputed here from the raw array rather than trusting
   the module's own slicing.

   This is the assertion that no future information reaches a forecast. It is
   written as an equality against an independently-built window because that is
   the only form that cannot pass by accident. *)
let test_forecast_uses_only_the_prior_window () =
  let window = 50 in
  let returns =
    Array.init 200 ~f:(fun i -> Float.of_int ((i * 37 % 23) - 11) /. 1000.0)
  in
  let obs =
    VB.rolling ~returns ~window ~confidence:0.95 ~estimator:VB.Estimator.Historical
  in
  Alcotest.(check int)
    "one forecast per forecastable day" (200 - window) (List.length obs);
  List.iteri obs ~f:(fun i o ->
      let past = Array.sub returns ~pos:i ~len:window in
      Alcotest.check feq
        (Printf.sprintf "forecast %d is built from returns[%d..%d]" i i (i + window - 1))
        (Ohcamel.Risk_metrics.historical_var ~returns:past ~confidence:0.95)
        (Obs.var o);
      Alcotest.check feq
        (Printf.sprintf "and is scored against returns[%d]" (i + window))
        returns.(i + window)
        (Obs.realised o))

(* Behavioural: a crash immediately after a long calm stretch MUST be counted as
   an exception, because on the morning of the crash the window held nothing but
   calm days.

   If the window reached forward by even one observation the forecast would
   already be wide enough to cover the crash, the exception would disappear, and
   the model would look like it had seen it coming. *)
let test_a_crash_after_calm_is_an_exception () =
  let returns = Array.append calm [| -0.25 |] in
  let obs =
    VB.rolling ~returns ~window:100 ~confidence:0.95 ~estimator:VB.Estimator.Historical
  in
  let hits = VB.exceedances obs in
  Alcotest.(check bool) "the crash day is an exception" true hits.(Array.length hits - 1);
  Alcotest.(check int)
    "and it is the only one -- the calm days are covered by a calm window" 1
    (Array.count hits ~f:Fn.id)

let test_rolling_rejects_a_series_it_cannot_forecast () =
  check_invalid_arg "series no longer than the window" (fun () ->
      VB.rolling ~returns:(Array.create ~len:50 0.01) ~window:50 ~confidence:0.95
        ~estimator:VB.Estimator.Historical);
  check_invalid_arg "window of one" (fun () ->
      VB.rolling ~returns:calm ~window:1 ~confidence:0.95
        ~estimator:VB.Estimator.Historical)

(* ------------------------------------------------------------------------ *)
(* The battery, end to end                                                   *)
(* ------------------------------------------------------------------------ *)

(* Two series, each miscalibrating a real estimator in a different direction.
   Both are generated deterministically -- a fixed linear congruential sequence,
   no seed to forget and no run-to-run drift -- so the rejections below are
   facts about the estimators, not about a lucky sample.

   SPIKES: calm days interrupted by an identical large loss every twentieth
   day. Exactly 5% of days are big losses, which is exactly the 95% quantile,
   so a 100-day historical window always holds exactly five spikes and its
   fifth-worst observation IS the spike magnitude. The forecast therefore sits
   permanently at -0.08 and the realised -0.08 never strictly beats it.

   REGIME: a long calm stretch followed by a sustained violent one. The
   equal-weighted window takes a hundred days to absorb the change, so the
   exceptions bunch at the break. This is the failure mode that a count of
   exceptions cannot see and Christoffersen can. *)

(* x_{n+1} = (1103515245 x_n + 12345) mod 2^31, mapped to [-1, 1).

   The classic glibc constants. Not a good generator and not used as one -- it
   is here to produce a fixed sequence that is not visibly patterned, so the
   series below are not accidentally periodic in a way that flatters or
   punishes either estimator. OCaml's native int is 63-bit, so the multiply
   cannot overflow at these magnitudes. *)
let lcg_series ~length ~(scale : int -> float) =
  let state = ref 12345 in
  Array.init length ~f:(fun i ->
      state := ((1103515245 * !state) + 12345) % 2147483648;
      let u = float_of_int !state /. 2147483648.0 in
      ((2.0 *. u) -. 1.0) *. scale i)

let spikes =
  Array.init 500 ~f:(fun i ->
      if i % 20 = 19 then -0.08 else if i % 2 = 0 then 0.004 else -0.003)

let regime = lcg_series ~length:500 ~scale:(fun i -> if i < 300 then 0.006 else 0.05)

let test_battery_runs_end_to_end () =
  let r =
    VB.of_returns ~returns:spikes ~window:100 ~confidence:0.95
      ~estimator:VB.Estimator.Historical
  in
  Alcotest.(check int) "observations" 400 (VB.observations r);
  Alcotest.(check bool)
    "expected exceptions is n * (1 - confidence)" true
    (Float.( < ) (Float.abs (VB.expected_exceptions r -. 20.0)) 1e-9);
  Alcotest.(check bool)
    "observed rate is a fraction" true
    (Float.( >= ) (VB.observed_rate r) 0.0 && Float.( <= ) (VB.observed_rate r) 1.0);
  Alcotest.(check bool)
    "conditional coverage is the sum of its parts" true
    (Float.( < )
       (Float.abs
          (VB.conditional_coverage_statistic r
          -. (VB.kupiec_statistic r +. VB.independence_statistic r)))
       1e-12);
  Alcotest.(check bool)
    "the worst loss is the worst loss" true
    (Float.( < ) (Float.abs (VB.worst_loss r -. 0.08)) 1e-12)

(* Rejection in the direction people forget exists: a model that is never
   breached.

   Historical VaR on SPIKES produces zero exceptions in four hundred days where
   twenty were expected. Nothing about that output looks alarming -- a risk
   report showing no breaches all year reads as a well-run book -- and Kupiec
   rejects it at p ~ 1e-10. The model is not conservative, it is wrong, and
   every limit sized against it is slack by an unknown amount.

   Note which test fires: coverage rejects hard, independence finds nothing at
   all (there are no exceptions, so there is nothing to be dependent). That is
   the decomposition earning its keep -- the joint statistic alone would say
   "rejected" and leave you to guess why. *)
let test_rejects_a_model_that_is_never_breached () =
  let r =
    VB.of_returns ~returns:spikes ~window:100 ~confidence:0.95
      ~estimator:VB.Estimator.Historical
  in
  Alcotest.(check int) "not one exception in four hundred days" 0 (VB.exceptions r);
  Alcotest.(check bool)
    "which Kupiec rejects outright" true
    (Float.( < ) (VB.kupiec_p r) 1e-6);
  Alcotest.(check bool)
    "independence has nothing to say, correctly" true
    (Float.equal (VB.independence_p r) 1.0);
  Alcotest.(check bool) "so the model is rejected" true (VB.rejected r);
  let zone = VB.zone r in
  Alcotest.(check bool)
    "and Basel calls it green, which is exactly why a zone is not a test" true
    (VB.Zone.equal zone VB.Zone.Green)

(* Rejection in the direction only the independence test can see.

   Parametric VaR on REGIME is breached about the right number of times -- the
   count alone would pass -- but the breaches arrive in a cluster at the
   volatility break, because an equal-weighted hundred-day window needs a
   hundred days to notice that the world changed. Christoffersen finds the
   consecutive exceptions that Kupiec's count averages away.

   This is the test that justifies computing three statistics instead of one. *)
let test_rejects_a_model_whose_breaches_cluster () =
  let r =
    VB.of_returns ~returns:regime ~window:100 ~confidence:0.95
      ~estimator:VB.Estimator.Parametric
  in
  Alcotest.(check bool)
    (Printf.sprintf
       "the count alone does not reject (%d exceptions, %.1f expected, p = %.3f)"
       (VB.exceptions r) (VB.expected_exceptions r) (VB.kupiec_p r))
    true
    (Float.( > ) (VB.kupiec_p r) 0.05);
  Alcotest.(check bool)
    (Printf.sprintf "but independence does (p = %.4f)" (VB.independence_p r))
    true
    (Float.( < ) (VB.independence_p r) 0.05);
  Alcotest.(check bool)
    (Printf.sprintf "so the joint test rejects (p = %.4f)" (VB.conditional_coverage_p r))
    true (VB.rejected r);
  let hits =
    VB.exceedances
      (VB.rolling ~returns:regime ~window:100 ~confidence:0.95
         ~estimator:VB.Estimator.Parametric)
  in
  let consecutive = Array.counti hits ~f:(fun i h -> i > 0 && h && hits.(i - 1)) in
  Alcotest.(check bool)
    (Printf.sprintf "there are genuinely consecutive breaches (%d of them)" consecutive)
    true (consecutive > 0)

let test_run_rejects_an_empty_series () =
  check_invalid_arg "empty observations" (fun () ->
      VB.run ~observations:[] ~estimator:VB.Estimator.Historical ~confidence:0.95)

let test_exceedances_are_extracted_in_order () =
  let obs =
    [
      Obs.create ~var:0.01 ~realised:(-0.02);
      Obs.create ~var:0.01 ~realised:0.00;
      Obs.create ~var:0.01 ~realised:(-0.05);
    ]
  in
  Alcotest.check bools "in order" [| true; false; true |] (VB.exceedances obs)

(* ------------------------------------------------------------------------ *)
(* The duration-based independence test                                      *)
(* ------------------------------------------------------------------------ *)

(* Christoffersen and Pelletier's test exists because the Markov one has a
   specific blind spot, so these cases are chosen to sit on either side of it.

   The shape parameter is what carries the meaning, and it reads directly:
   b < 1 is clustering (a breach makes the next one sooner than chance), b = 1
   is memoryless, b > 1 is over-regular. Every assertion below is about which
   side of 1 the fit lands on and whether the likelihood ratio rejects, because
   those are the two things a reader acts on. *)

let duration_of (hits : bool list) =
  VB.duration_independence ~exceedances:(Array.of_list hits)

let expect_duration name hits ~f =
  match duration_of hits with
  | None -> Alcotest.failf "%s: expected the test to apply" name
  | Some (shape, statistic, p) -> f shape statistic p

(* THE CASE THE MARKOV TEST MISSES.

   Five exceedances inside seventeen sessions, every third or fourth day, then
   nothing for a long time. No two are adjacent, so every Markov transition
   count is the same as it would be for a well-scattered series and LR_ind is
   near zero. The durations are not: four short ones and one enormous one is
   about as far from exponential as a sample this size gets.

   This is the shape of the COVID window in `make backtest-crisis`, reduced to
   the smallest series that still shows it. *)
let test_a_burst_that_is_not_consecutive () =
  let hits =
    List.init 400 ~f:(fun i -> List.mem [ 40; 43; 47; 50; 56 ] i ~equal:Int.equal)
  in
  let markov, markov_p =
    VB.christoffersen_independence ~exceedances:(Array.of_list hits)
  in
  (* The Markov statistic is NOT exactly zero here, and the reason is worth
     knowing: with n11 = 0 the unrestricted model fits "a breach is never
     followed by a breach", which does differ from the restricted "every day is
     the same". So it registers 0.13 -- as a hint of ANTI-clustering, the
     opposite of what is happening -- and comes nowhere near rejecting. That is
     the blind spot stated precisely: not that the test returns nothing, but
     that what it returns is uninformative about a cluster whose members are
     three days apart. *)
  Alcotest.(check bool)
    (Printf.sprintf "the Markov test does not reject (LR = %.3f, p = %.4f)" markov
       markov_p)
    true (Float.( > ) markov_p 0.5);
  expect_duration "burst" hits ~f:(fun shape statistic p ->
      Alcotest.(check bool)
        (Printf.sprintf "the duration test fits a clustering shape (%.3f < 1)" shape)
        true (Float.( < ) shape 1.0);
      Alcotest.(check bool)
        (Printf.sprintf "and rejects at 5%% (LR = %.3f, p = %.4f)" statistic p)
        true (Float.( < ) p 0.05))

(* Perfectly regular exceedances: every twentieth day, exactly. The durations
   have zero variance, so the Weibull likelihood increases in the shape without
   limit and the MLE diverges -- the reported value is the search's upper bound
   rather than a fitted number.

   Asserted because the bound is load-bearing rather than decorative: without it
   this case would loop or overflow. The verdict is not in doubt either way, and
   a series this regular is a model whose tail is mechanically periodic rather
   than a market. *)
let test_perfect_regularity_pins_the_shape_at_the_bound () =
  let hits = List.init 400 ~f:(fun i -> i % 20 = 19) in
  expect_duration "regular" hits ~f:(fun shape statistic p ->
      Alcotest.(check bool)
        (Printf.sprintf "the shape runs to the upper bound (%.2f)" shape)
        true (Float.( >= ) shape 19.9);
      Alcotest.(check bool)
        "finite statistic despite a degenerate fit" true
        (Float.is_finite statistic && Float.( > ) statistic 100.0);
      Alcotest.(check bool) "and rejects overwhelmingly" true (Float.( < ) p 1e-6))

(* Genuinely memoryless durations, built deterministically.

   Getting this construction right took a correction worth recording. The
   obvious version -- a fixed list of plausible-looking gaps, cycled -- is NOT
   exponential and this test caught it: a repeating pattern has a bounded
   maximum and no right tail, so it fits a shape of 1.45 and rejects for
   over-regularity. An exponential's defining feature is exactly the long tail a
   hand-written list will not contain.

   So the gaps are generated by inverting the exponential CDF,
   d = round(-mean * ln u), over a golden-ratio low-discrepancy sequence for u.
   That is exponential by construction and still fully deterministic -- a test
   whose verdict depends on a seed is one that fails some morning for no
   reason. The resulting gaps run from 1 to 87 around a mean of 20, which is
   the shape a memoryless waiting time actually has. *)
let test_memoryless_durations_do_not_reject () =
  let mean = 20.0 in
  let phi = (Float.sqrt 5.0 -. 1.0) /. 2.0 in
  let hits = Array.create ~len:900 false in
  let t = ref 0 in
  let i = ref 1 in
  let continue_ = ref true in
  while !continue_ do
    let u = Float.max 1e-6 (Float.mod_float (float_of_int !i *. phi) 1.0) in
    let gap = Int.max 1 (Float.iround_nearest_exn (-.mean *. Float.log u)) in
    t := !t + gap;
    if !t >= Array.length hits then continue_ := false
    else begin
      hits.(!t) <- true;
      Int.incr i
    end
  done;
  match VB.duration_independence ~exceedances:hits with
  | None -> Alcotest.fail "expected the test to apply"
  | Some (shape, _, p) ->
      Alcotest.(check bool)
        (Printf.sprintf "shape is near 1 (%.3f)" shape)
        true
        (Float.( > ) shape 0.7 && Float.( < ) shape 1.4);
      Alcotest.(check bool)
        (Printf.sprintf "and does not reject (p = %.4f)" p)
        true (Float.( > ) p 0.05)

(* Fewer than two exceedances is [None], not a p-value.

   With none or one there is no complete duration to fit anything to, and
   reporting p = 1 would read as "no evidence of clustering" when the truth is
   "the test does not apply". Those are different statements and the type says
   which one this is. *)
let test_too_few_exceptions_is_not_applicable () =
  Alcotest.(check bool)
    "no exceedances at all" true
    (Option.is_none (duration_of (List.init 100 ~f:(fun _ -> false))));
  Alcotest.(check bool)
    "exactly one" true
    (Option.is_none (duration_of (List.init 100 ~f:(fun i -> i = 50))));
  Alcotest.(check bool)
    "two is enough" true
    (Option.is_some (duration_of (List.init 100 ~f:(fun i -> i = 20 || i = 60))))

(* The censored ends matter, and this is the case that shows it.

   Two series with identical INTERIOR durations, differing only in how much
   quiet time sits before the first exceedance and after the last. Treating
   those ends as complete waiting times rather than as censored would bias the
   shape, so the two fits should not be identical -- and the one with long quiet
   ends should not be dragged to a wildly different verdict by them. *)
let test_censoring_is_applied_to_the_ends () =
  let interior = [ 100; 120; 140; 160 ] in
  let series ~lead ~trail =
    let hits = Array.create ~len:(lead + 160 + trail + 200) false in
    let t = ref lead in
    hits.(!t) <- true;
    List.iter interior ~f:(fun gap ->
        t := !t + gap;
        if !t < Array.length hits then hits.(!t) <- true);
    hits
  in
  let short = VB.duration_independence ~exceedances:(series ~lead:5 ~trail:5) in
  let long = VB.duration_independence ~exceedances:(series ~lead:250 ~trail:250) in
  match (short, long) with
  | Some (s1, _, _), Some (s2, _, _) ->
      (* Different, because the censored ends really do carry information about
         the scale -- a long quiet run is evidence the waiting times are long. *)
      Alcotest.(check bool)
        (Printf.sprintf "the ends move the fit (%.3f vs %.3f)" s1 s2)
        true
        (Float.( > ) (Float.abs (s1 -. s2)) 1e-6);
      (* But only as survival terms, so neither collapses. Uncensored ends would
         push a series with a 250-day lead toward a wildly different shape. *)
      Alcotest.(check bool)
        "both fits stay in a sane range" true
        (Float.( > ) s1 0.2 && Float.( < ) s1 10.0 && Float.( > ) s2 0.2
       && Float.( < ) s2 10.0)
  | _ -> Alcotest.fail "both series have enough exceedances"

(* The report carries it, and does NOT fold it into conditional coverage.

   That second half is the assertion that matters. [conditional_coverage] is
   Christoffersen's decomposition -- coverage times FIRST-ORDER independence --
   and its two degrees of freedom follow from exactly those two pieces. Adding a
   third statistic to the sum would produce something with no derived
   distribution and would silently change every verdict this module has already
   published. *)
let test_the_report_keeps_the_two_independence_tests_separate () =
  let returns =
    Array.init 400 ~f:(fun i ->
        if List.mem [ 90; 93; 97; 100; 106 ] i ~equal:Int.equal then -0.30 else 0.001)
  in
  let r =
    VB.of_returns ~returns ~window:60 ~confidence:0.95 ~estimator:VB.Estimator.Historical
  in
  Alcotest.check feq "conditional coverage is still Kupiec + Markov"
    (VB.kupiec_statistic r +. VB.independence_statistic r)
    (VB.conditional_coverage_statistic r);
  Alcotest.(check bool)
    "and the duration test is reported alongside" true
    (Option.is_some (VB.duration_p r))

let suite =
  ( "var_backtest",
    [
      Alcotest.test_case "exceedance sign convention" `Quick
        test_exceedance_sign_convention;
      Alcotest.test_case "exceedances keep their order" `Quick
        test_exceedances_are_extracted_in_order;
      Alcotest.test_case "Kupiec: too many exceptions" `Quick
        test_kupiec_too_many_exceptions;
      Alcotest.test_case "Kupiec: too few exceptions is also a rejection" `Quick
        test_kupiec_too_few_exceptions;
      Alcotest.test_case "Kupiec: perfect calibration" `Quick
        test_kupiec_perfect_calibration;
      Alcotest.test_case "Kupiec rejects bad input" `Quick test_kupiec_rejects_bad_input;
      Alcotest.test_case "Christoffersen: clustered" `Quick test_christoffersen_clustered;
      Alcotest.test_case "Christoffersen: anti-clustered" `Quick
        test_christoffersen_alternating;
      Alcotest.test_case "Christoffersen: no exceptions" `Quick
        test_christoffersen_no_exceptions;
      Alcotest.test_case "Christoffersen: exception on the last day" `Quick
        test_christoffersen_exception_on_the_last_day;
      Alcotest.test_case "Christoffersen: too short to have a transition" `Quick
        test_christoffersen_too_short;
      Alcotest.test_case "Basel reproduces the published 250-day table" `Quick
        test_basel_reproduces_the_published_table;
      Alcotest.test_case "Basel cumulative probability" `Quick
        test_basel_cumulative_probability;
      Alcotest.test_case "a forecast sees only the days before it" `Quick
        test_forecast_uses_only_the_prior_window;
      Alcotest.test_case "a crash after calm is an exception" `Quick
        test_a_crash_after_calm_is_an_exception;
      Alcotest.test_case "rolling rejects a series it cannot forecast" `Quick
        test_rolling_rejects_a_series_it_cannot_forecast;
      Alcotest.test_case "the battery runs end to end" `Quick test_battery_runs_end_to_end;
      Alcotest.test_case "it rejects a model that is never breached" `Quick
        test_rejects_a_model_that_is_never_breached;
      Alcotest.test_case "it rejects a model whose breaches cluster" `Quick
        test_rejects_a_model_whose_breaches_cluster;
      Alcotest.test_case "an empty series is not a passing backtest" `Quick
        test_run_rejects_an_empty_series;
      Alcotest.test_case "DURATION: a burst the Markov test cannot see" `Quick
        test_a_burst_that_is_not_consecutive;
      Alcotest.test_case "DURATION: perfect regularity pins the shape at the bound" `Quick
        test_perfect_regularity_pins_the_shape_at_the_bound;
      Alcotest.test_case "DURATION: memoryless durations do not reject" `Quick
        test_memoryless_durations_do_not_reject;
      Alcotest.test_case "DURATION: fewer than two exceptions is not applicable" `Quick
        test_too_few_exceptions_is_not_applicable;
      Alcotest.test_case "DURATION: the censored ends are treated as censored" `Quick
        test_censoring_is_applied_to_the_ends;
      Alcotest.test_case "DURATION: kept out of the conditional-coverage sum" `Quick
        test_the_report_keeps_the_two_independence_tests_separate;
    ] )

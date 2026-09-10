(* Unit tests for garch_study.ml.

   The README's six-row table is NOT pinned here: it is 180 maximum-likelihood
   fits and `make garch` is the reproduction, held by the byte-identical gate.
   What is tested is the contract the served host depends on -- the progress
   callback's count and order, determinism from the seed alone, that the
   study is the same on a second domain -- and the two facts about the truth
   that the verdict paragraph prints. mean_sd's two values are hand
   derivations. *)

open Core
module Garch_study = Ohcamel.Garch_study
module Garch11 = Ohcamel.Vol_estimators.Garch11

(* Two sizes, two replications, a short burn-in: eight fits at n <= 125, which
   is quick enough to run twice in a test. *)
let tiny ?progress () =
  Garch_study.run ?progress ~seed:2026_09_02 ~replications:2 ~sample_sizes:[ 60; 125 ]
    ~truth:Garch_study.default_truth ~burn_in:100 ()

(* All seven Row.t fields, not just the three means: the determinism and
   spawned-domain tests below compare whole rows, so a divergence in a
   standard deviation alone -- which the means-only version of this function
   let through -- is caught too. *)
let shape (r : Garch_study.result) =
  List.map r.Garch_study.rows ~f:(fun (row : Garch_study.Row.t) ->
      ( row.Garch_study.Row.n,
        Printf.sprintf "%.12f %.12f %.12f %.12f %.12f %.12f"
          row.Garch_study.Row.alpha_mean row.Garch_study.Row.alpha_sd
          row.Garch_study.Row.beta_mean row.Garch_study.Row.beta_sd
          row.Garch_study.Row.persistence_mean row.Garch_study.Row.persistence_sd ))

(* 2 replications x 2 sizes = 4 fits, so the callback sees 1, 2, 3, 4 -- once
   after each fit, in order, and never a count it has already passed. The
   served host's "computing k of 180" line is this list's last element. *)
let test_progress_is_called_after_every_fit_in_order () =
  let seen = ref [] in
  let r = tiny ~progress:(fun k -> seen := k :: !seen) () in
  Alcotest.(check (list int)) "1, 2, 3, 4" [ 1; 2; 3; 4 ] (List.rev !seen);
  Alcotest.(check int)
    "the last count is replications x sizes"
    (r.Garch_study.replications * List.length r.Garch_study.sample_sizes)
    (List.hd_exn !seen)

(* The state is built from the seed inside [run], so two calls are two
   identical streams; nothing about the result depends on what else the
   process has drawn. This is what makes the served host's table a figure
   rather than a coin toss per restart. *)
let test_two_runs_with_one_seed_agree () =
  Alcotest.(check (list (pair int string)))
    "same seed, same rows"
    (shape (tiny ()))
    (shape (tiny ()))

let test_the_rows_echo_the_request () =
  let r = tiny () in
  Alcotest.(check (list int))
    "one row per size, in order" [ 60; 125 ]
    (List.map r.Garch_study.rows ~f:(fun row -> row.Garch_study.Row.n));
  Alcotest.(check int) "seed" 2026_09_02 r.Garch_study.seed;
  Alcotest.(check int) "burn-in" 100 r.Garch_study.burn_in;
  List.iter r.Garch_study.rows ~f:(fun (row : Garch_study.Row.t) ->
      let open Garch_study.Row in
      (* The fit is bounded inside the stationary region, so every mean is too,
         and a standard deviation is never negative. *)
      Alcotest.(check bool)
        "alpha in (0, 1)" true
        (Float.( > ) row.alpha_mean 0.0 && Float.( < ) row.alpha_mean 1.0);
      Alcotest.(check bool)
        "persistence below 0.999" true
        (Float.( < ) row.persistence_mean 0.999);
      Alcotest.(check bool) "sd >= 0" true (Float.( >= ) row.persistence_sd 0.0))

(* Textbook daily-equity parameters. 0.10 + 0.88 = 0.98, and the half-life is
   ln 0.5 / ln 0.98 = -0.693147 / -0.020203 = 34.31 periods -- the "34" the
   verdict paragraph prints with %.0f. *)
let test_the_truth_is_persistence_0_98_half_life_34 () =
  let t = Garch_study.default_truth in
  Alcotest.(check (float 1e-9)) "persistence" 0.98 (Garch11.persistence t);
  match Garch11.shock_half_life t with
  | None -> Alcotest.fail "a stationary process has a finite half-life"
  | Some h ->
      Alcotest.(check (float 0.01)) "34.31 periods" 34.31 h;
      Alcotest.(check string) "prints as 34" "34" (Printf.sprintf "%.0f" h)

let test_the_defaults_are_the_readmes_experiment () =
  Alcotest.(check int) "seed" 2026_08_25 Garch_study.default_seed;
  Alcotest.(check int) "thirty replications" 30 Garch_study.default_replications;
  Alcotest.(check (list int))
    "six sizes"
    [ 60; 125; 250; 500; 1000; 2000 ]
    Garch_study.default_sample_sizes;
  Alcotest.(check int) "burn-in 500" 500 Garch_study.default_burn_in;
  (* The verdict paragraph reads the row at the engine's own window; it must
     be one of the sizes or the paragraph silently disappears. *)
  Alcotest.(check bool)
    "the engine's window is a row" true
    (List.mem Garch_study.default_sample_sizes Ohcamel.Synthetic_book.return_window
       ~equal:Int.equal)

(* [1; 2; 3; 4]: mean 2.5, population variance (2.25 + 0.25 + 0.25 + 2.25) / 4
   = 1.25, sd = 1.118034. Population, not sample: the CLI divided by n, and the
   README's +/- columns are that number. *)
let test_mean_sd_is_the_population_sd () =
  let mean, sd = Garch_study.mean_sd [ 1.0; 2.0; 3.0; 4.0 ] in
  Alcotest.(check (float 1e-9)) "mean" 2.5 mean;
  Alcotest.(check (float 1e-6)) "population sd" 1.118034 sd

(* THE ONE THE SERVED HOST DEPENDS ON. Phase 5 runs the full study on a
   spawned domain so the page can come up before the 180 fits finish; if the
   study reached any state shared with the main domain, the rows would depend
   on the scheduler. Same seed, other domain, same rows. *)
let test_the_same_rows_on_a_spawned_domain () =
  let direct = tiny () in
  let spawned = Domain.join (Domain.spawn (fun () -> tiny ())) in
  Alcotest.(check (list (pair int string)))
    "same rows on either domain" (shape direct) (shape spawned)

let suite =
  ( "garch_study",
    [
      Alcotest.test_case "progress is called after every fit, in order" `Quick
        test_progress_is_called_after_every_fit_in_order;
      Alcotest.test_case "two runs with one seed agree" `Quick
        test_two_runs_with_one_seed_agree;
      Alcotest.test_case "the rows echo the request" `Quick test_the_rows_echo_the_request;
      Alcotest.test_case "the truth is persistence 0.98, half-life 34" `Quick
        test_the_truth_is_persistence_0_98_half_life_34;
      Alcotest.test_case "the defaults are the README's experiment" `Quick
        test_the_defaults_are_the_readmes_experiment;
      Alcotest.test_case "mean_sd is the population sd" `Quick
        test_mean_sd_is_the_population_sd;
      Alcotest.test_case "THE SAME ROWS ON A SPAWNED DOMAIN" `Quick
        test_the_same_rows_on_a_spawned_domain;
    ] )

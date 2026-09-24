(* The GARCH(1,1) sample-size experiment: simulate a KNOWN process, fit it
   back, and see how close the fit lands, at several sample sizes.

   This exists to justify an absence, which is an unusual thing for a program
   to do and is the point. GARCH is the obvious next step after EWMA -- it
   adds mean reversion -- and it is implemented and tested in
   vol_estimators.ml and NOT wired into the graph, because this measurement
   says it should not be: at sixty observations, the engine's own window, the
   persistence comes back biased and with a standard deviation the size of the
   quantity. `make garch` prints the table; the served page shows the same
   one, computed in the same process.

   WHY A CALLBACK. The full study is 180 maximum-likelihood fits and takes
   seconds; the served host runs it on a second domain so the page can come up
   first, and reports "k of 180" while it runs. [progress] is called after
   every fit with the running count. The CLI passes nothing and prints at the
   end, as it always did.

   WHY THE STATE IS BUILT INSIDE. [run] makes its own Random.State from
   [seed] and consumes it sequentially -- every innovation of every
   replication at n = 60, then n = 125, and so on -- exactly as the CLI's
   loop did. Nothing about the result depends on which domain runs it or on
   what the process drew before; test_garch_study.ml asserts both. *)

open Core

module Row = struct
  type t = {
    n : int;
    alpha_mean : float;
    alpha_sd : float;
    beta_mean : float;
    beta_sd : float;
    persistence_mean : float;
    persistence_sd : float;
  }
end

type result = {
  seed : int;
  replications : int;
  sample_sizes : int list;
  burn_in : int;
  truth : Vol_estimators.Garch11.t;
  rows : Row.t list;
}

let default_seed = 2026_08_25
let default_replications = 30
let default_sample_sizes = [ 60; 125; 250; 500; 1000; 2000 ]
let default_burn_in = 500

(* Textbook daily-equity parameters. Persistence 0.98 is a shock half-life of
   about 34 days, which is what an equity index actually looks like. *)
let default_truth = Vol_estimators.Garch11.{ omega = 4e-6; alpha = 0.10; beta = 0.88 }

(* Standard normal shocks. Synthetic_book.gaussian at sigma = 1.0 is the CLI's
   unscaled Box-Muller bit for bit -- multiplying by 1.0 is exact -- so the
   README's table is reproduced from the one generator rather than a copy. *)
let innovations ~(rng : Random.State.t) ~(n : int) : float array =
  Array.init n ~f:(fun _ -> Synthetic_book.gaussian ~rng ~sigma:1.0)

(* Population standard deviation -- divided by n, not n - 1. The README's +/-
   columns are this number and the choice is kept rather than corrected: with
   thirty replications the two differ in the third decimal, which is the
   decimal the table prints. *)
let mean_sd (xs : float list) : float * float =
  let n = float_of_int (List.length xs) in
  let mean = List.fold xs ~init:0.0 ~f:( +. ) /. n in
  let var = List.fold xs ~init:0.0 ~f:(fun acc x -> acc +. ((x -. mean) ** 2.0)) /. n in
  (mean, Float.sqrt var)

let run ?(progress : int -> unit = fun _ -> ()) ~(seed : int) ~(replications : int)
    ~(sample_sizes : int list) ~(truth : Vol_estimators.Garch11.t) ~(burn_in : int) () :
    result =
  let module G = Vol_estimators.Garch11 in
  if replications < 1 then
    invalid_argf "garch_study: need at least one replication, got %d" replications ();
  if List.is_empty sample_sizes then invalid_arg "garch_study: no sample sizes";
  List.iter sample_sizes ~f:(fun n ->
      if n < 3 then
        invalid_argf "garch_study: a GARCH(1,1) fit needs at least 3 observations, got %d"
          n ());
  let rng = Random.State.make [| seed |] in
  let fitted = ref 0 in
  let rows =
    List.map sample_sizes ~f:(fun n ->
        (* List.init, as the CLI had it, and not a for-loop into an array: Base
           calls f for the LAST index first, so the fits land in the list in
           reverse draw order, and mean_sd's fold then sums them in that order.
           Floating-point addition is not associative; the README's third
           decimal is the sum in this order. *)
        let fits =
          List.init replications ~f:(fun _ ->
              let innovations = innovations ~rng ~n:(n + burn_in) in
              let path = G.simulate ~burn_in ~innovations truth in
              let fit = G.fit ~returns:path () in
              incr fitted;
              progress !fitted;
              fit)
        in
        let alpha_mean, alpha_sd = mean_sd (List.map fits ~f:G.alpha) in
        let beta_mean, beta_sd = mean_sd (List.map fits ~f:G.beta) in
        let persistence_mean, persistence_sd = mean_sd (List.map fits ~f:G.persistence) in
        {
          Row.n;
          alpha_mean;
          alpha_sd;
          beta_mean;
          beta_sd;
          persistence_mean;
          persistence_sd;
        })
  in
  { seed; replications; sample_sizes; burn_in; truth; rows }

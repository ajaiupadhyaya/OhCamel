(* A strategy's live record as statistics (INC-01, Reporting).

   Pure, over a list of sessions, so every figure can be checked by hand, and with
   no verdict in it: what a record means is the charter's and the owner's. The
   stop rules read [drawdown] from here, so a stop and the page agree.

   The Sharpe and the PSR follow ohcamel_quant.backtest.metrics to the letter: a
   sample deviation, bias-corrected skewness and raw kurtosis, Sharpe per period
   inside the PSR. Bailey and Lopez de Prado (2012), eq. 6:

     PSR(SR* ) = Phi( (SR - SR* ) sqrt(n - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR^2) )

   test_record.ml pins one series to that module's output. *)

open Core

type session = {
  date : Date.t;
  allocated : float;
  pnl : float;
  cost : float;
  turnover : float;
}

type summary = {
  sessions : int;
  compounded : float;
  sharpe : float option;
  psr : float option;
  max_drawdown : float;
  hit_rate : float option;
  turnover_mean : float option;
  cost_bps : float option;
}

type fill = { date : Date.t; symbol : Ohcamel.Types.Symbol.t; qty : float; price : float }

(* A strategy's P&L marked to each close: what it held is worth this close less
   what it was worth at the last, less what its fills cost. So the sessions sum to
   its cash and what it still holds, whatever the path.

   It is counted from the strategy's own fills and never from the account, as
   Journal.source_fills counts its position. A fill on a date with no recorded
   session lands in the next one, and a name with no close that day holds its
   last, or its fill's price before it has one: a session lost to a restart moves
   a strategy's P&L a day and loses none of it. *)
let sessions_of ~dates ~close ~allocated fills =
  let module Symbol = Ohcamel.Types.Symbol in
  let value positions marks =
    Map.fold positions ~init:0.0 ~f:(fun ~key ~data acc ->
        acc +. (data *. Option.value (Map.find marks key) ~default:0.0))
  in
  let _, _, _, sessions =
    List.fold
      (List.sort dates ~compare:Date.compare)
      ~init:
        ( List.sort fills ~compare:(fun (a : fill) (b : fill) ->
              Date.compare a.date b.date),
          Symbol.Map.empty,
          Symbol.Map.empty,
          [] )
      ~f:(fun (pending, positions, marks, sessions) date ->
        let today, pending =
          List.split_while pending ~f:(fun (f : fill) -> Date.( <= ) f.date date)
        in
        let before = value positions marks in
        let positions =
          List.fold today ~init:positions ~f:(fun acc f ->
              Map.update acc f.symbol ~f:(fun q -> Option.value q ~default:0.0 +. f.qty))
        in
        let marks =
          List.fold today ~init:marks ~f:(fun acc f ->
              Map.update acc f.symbol ~f:(fun known ->
                  Option.value known ~default:f.price))
        in
        let marks =
          List.fold (Map.keys positions) ~init:marks ~f:(fun acc symbol ->
              match close date symbol with
              | Some c -> Map.set acc ~key:symbol ~data:c
              | None -> acc)
        in
        if Map.is_empty positions then (pending, positions, marks, sessions)
        else
          let traded (f : fill) = f.qty *. f.price in
          let session =
            {
              date;
              allocated = allocated date;
              pnl =
                value positions marks -. before -. List.sum (module Float) today ~f:traded;
              cost = 0.0;
              turnover = List.sum (module Float) today ~f:(fun f -> Float.abs (traded f));
            }
          in
          (pending, positions, marks, session :: sessions))
  in
  List.rev sessions

let periods_a_year = 252.0
let held s = Float.( > ) s.allocated 0.0

let returns sessions =
  List.filter_map sessions ~f:(fun s ->
      if held s then Some (s.pnl /. s.allocated) else None)

(* From a level of 1, so a loss in the first session counts. *)
let drawdown ~returns =
  let _, _, worst =
    List.fold returns ~init:(1.0, 1.0, 0.0) ~f:(fun (level, peak, worst) r ->
        let level = level *. (1.0 +. r) in
        let peak = Float.max peak level in
        (level, peak, Float.max worst ((peak -. level) /. peak)))
  in
  worst

let mean xs = List.sum (module Float) xs ~f:Fn.id /. float_of_int (List.length xs)

(* The central moments' divisor is n; the sample deviation's is n - 1. *)
let central ~about xs k = mean (List.map xs ~f:(fun x -> Float.int_pow (x -. about) k))

(* A constant series can leave a deviation of rounding error, and a Sharpe on that
   is a very large number about nothing. *)
let no_spread = 1e-12

let per_period_sharpe xs =
  let n = float_of_int (List.length xs) in
  if List.length xs < 2 then None
  else
    let about = mean xs in
    let sd = Float.sqrt (central ~about xs 2 *. n /. (n -. 1.0)) in
    if Float.( <= ) sd no_spread then None else Some (about /. sd)

let psr ~returns ~benchmark =
  match (per_period_sharpe returns, List.length returns >= 4) with
  | Some sr, true ->
      let n = float_of_int (List.length returns) in
      let about = mean returns in
      let m2 = central ~about returns 2 in
      let g1 = central ~about returns 3 /. (m2 ** 1.5) in
      let g2 = (central ~about returns 4 /. (m2 *. m2)) -. 3.0 in
      let skew = Float.sqrt (n *. (n -. 1.0)) /. (n -. 2.0) *. g1 in
      let kurtosis =
        ((n -. 1.0) /. ((n -. 2.0) *. (n -. 3.0)) *. (((n +. 1.0) *. g2) +. 6.0)) +. 3.0
      in
      let denominator = 1.0 -. (skew *. sr) +. ((kurtosis -. 1.0) /. 4.0 *. sr *. sr) in
      if Float.( <= ) denominator 0.0 then None
      else
        Some
          (Ohcamel.Options.Black_scholes.normal_cdf
             ((sr -. benchmark) *. Float.sqrt (n -. 1.0) /. Float.sqrt denominator))
  | _ -> None

let summarise sessions =
  let held_sessions = List.filter sessions ~f:held in
  let rs = returns sessions in
  let if_any xs value = if List.is_empty xs then None else Some (value ()) in
  let traded = List.sum (module Float) held_sessions ~f:(fun s -> s.turnover) in
  {
    sessions = List.length rs;
    compounded = List.fold rs ~init:1.0 ~f:(fun level r -> level *. (1.0 +. r)) -. 1.0;
    sharpe =
      Option.map (per_period_sharpe rs) ~f:(fun sr -> sr *. Float.sqrt periods_a_year);
    psr = psr ~returns:rs ~benchmark:0.0;
    max_drawdown = drawdown ~returns:rs;
    hit_rate =
      if_any rs (fun () ->
          float_of_int (List.count rs ~f:(fun r -> Float.( > ) r 0.0))
          /. float_of_int (List.length rs));
    turnover_mean =
      if_any held_sessions (fun () ->
          mean (List.map held_sessions ~f:(fun s -> s.turnover /. s.allocated)));
    cost_bps =
      (if Float.( > ) traded 0.0 then
         Some
           (List.sum (module Float) held_sessions ~f:(fun s -> s.cost)
           /. traded *. 10_000.0)
       else None);
  }

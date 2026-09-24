(* The six-name synthetic book, and the generator that seeds it.

   WHY THIS IS A LIBRARY MODULE. Until this phase the book was six literals at
   the top of bin/main.ml, and that was correct for as long as the only reader
   was a printer. It now has four: the CLI's printers, the stress suite, the
   crisis backtest (which values the book at these marks to fix its weights),
   and the served demo process, whose page has to say "this is the same book
   `make run` prints". Two copies of a book are two books, and the day one of
   them gains a name the other has not is the day the page's figures stop
   being about the terminal's.

   WHAT DOES NOT LIVE HERE. Nothing that formats, and nothing that owns a
   random state. [gaussian] and [daily_return] take the state as a parameter
   because the CLI draws every mode from ONE state in program order and its
   printed tables depend on that order; a state created here would either be a
   second stream (and the tables would move) or a global (and the demo host
   would share draws with the scaling probe). The caller owns the stream. *)

open Core
open Types

let sym = Symbol.of_string
let sec = Sector.of_string
let dollars = Notional.of_float

(* Long technology and financials, short energy: a book with real sector
   structure and a genuine short leg, so gross and net differ and the sector
   limits have something to say. *)
let book =
  [
    (sym "AAPL", sec "TECH", 150.00, 400.0);
    (sym "MSFT", sec "TECH", 300.00, 200.0);
    (sym "NVDA", sec "TECH", 900.00, 60.0);
    (sym "JPM", sec "FINANCIALS", 200.00, 250.0);
    (sym "XOM", sec "ENERGY", 100.00, -500.0);
    (sym "CVX", sec "ENERGY", 140.00, -300.0);
  ]

let instruments =
  List.map book ~f:(fun (symbol, sector, _, _) -> { Instrument.symbol; sector })

let limit name scope kind = { Limit.name; scope; kind }

let limits =
  [
    limit "nvda-cap"
      (Limit.Instrument (sym "NVDA"))
      (Limit.Gross_notional (dollars 55_000.0));
    limit "aapl-cap"
      (Limit.Instrument (sym "AAPL"))
      (Limit.Gross_notional (dollars 80_000.0));
    limit "tech-cap"
      (Limit.Sector (sec "TECH"))
      (Limit.Gross_notional (dollars 200_000.0));
    limit "energy-cap"
      (Limit.Sector (sec "ENERGY"))
      (Limit.Gross_notional (dollars 100_000.0));
    limit "book-cap" Limit.Portfolio (Limit.Gross_notional (dollars 400_000.0));
    limit "var-cap" Limit.Portfolio (Limit.Value_at_risk (dollars 12_000.0));
    limit "dd-cap" Limit.Portfolio (Limit.Max_drawdown 0.02);
    (* Two limits on risk SHARE rather than on notional, so this book exercises
       all four limit kinds and the difference between the two is visible rather
       than described.

       NVDA already has a notional cap above. This one caps its Euler share of
       portfolio VaR, and the two measure genuinely different things: trimming
       an unrelated position raises NVDA's risk share without touching its
       notional at all, and a volatility shock moves this one while leaving the
       notional cap exactly where it was. `make stress` shows precisely that --
       the vol-regime row has zero P&L, unchanged gross, and breaks the sector
       one and nothing else on the page. *)
    limit "nvda-risk"
      (Limit.Instrument (sym "NVDA"))
      (Limit.Component_var (dollars 900.0));
    limit "tech-risk" (Limit.Sector (sec "TECH")) (Limit.Component_var (dollars 2_000.0));
  ]

let starting_cash = dollars 1_000_000.0
let confidence = 0.95
let return_window = 60

(* ------------------------------------------------------------------------ *)
(* The synthetic market                                                      *)
(* ------------------------------------------------------------------------ *)

(* Box-Muller, cosine branch, with the first uniform floored at 1e-12 so the
   log cannot see a zero. This is the ONE generator every synthetic series in
   the project is drawn from -- the demo's ticks, the scaling probe, the
   validation battery's three series, the GARCH study's innovations -- and it
   is written once so that "seed N reproduces" is a statement about a seed
   rather than about which of four copies of this function ran. Two uniforms
   per draw, always, whether or not the sine branch would have been free. *)
let gaussian ~(rng : Random.State.t) ~(sigma : float) : float =
  let u1 = Float.max 1e-12 (Random.State.float rng 1.0) in
  let u2 = Random.State.float rng 1.0 in
  sigma *. Float.sqrt (-2.0 *. Float.log u1) *. Float.cos (2.0 *. Float.pi *. u2)

(* Returns are drawn as normal noise with a small negative drift, so the book
   tends to bleed and the drawdown breaker has something to do. *)
let daily_return ~(rng : Random.State.t) : float = gaussian ~rng ~sigma:0.012 -. 0.0008

(* Rate sensitivity per sector, as a return per percentage point of yield
   change: a 100bp rise costs a technology name 3% and pays an energy name 1%.

   These are assumptions, not measurements, and they are written down here
   rather than buried in a generator because the rate-shock scenario is only as
   meaningful as they are. The signs are the conventional ones -- long-duration
   growth equity discounts badly when rates rise, financials earn a wider spread
   -- and the magnitudes are the order of a real regression rather than the
   result of one.

   In live mode nothing like this is assumed. The betas come out of
   Risk_metrics.beta against the actual FRED series and the actual price
   history, which is the whole point of expressing a macro move as a factor
   shock. This exists so the synthetic book has a factor structure to shock at
   all; a factor uncorrelated with everything would make every beta zero and
   the scenario would truthfully report that nothing moved, which is a real
   state and a poor demonstration. *)
let rate_beta sector =
  match Sector.to_string sector with
  | "TECH" -> -0.030
  | "FINANCIALS" -> 0.009
  | "ENERGY" -> 0.010
  | _ -> 0.0

(* The factor series and the six return windows, written into [graph].

   Daily changes in a ten-year yield, in percentage points -- the units
   fred_client.ml delivers. A standard deviation of 5bp a day is about right
   for DGS10. Each name's window is then a common factor component plus
   idiosyncratic noise, so the betas the rate-shock scenario recovers are the
   ones written above rather than zero.

   DRAW ORDER IS LOAD-BEARING. The factor is drawn first, then one window per
   name in [book] order, which is the order bin/main.ml's inline version drew
   them; the stress table is a printed figure, and it reproduces only if the
   shared state is consumed in the same sequence. Prices and quantities are the
   caller's to set -- they draw nothing, so they may be set before or after. *)
let seed_returns ~(rng : Random.State.t) ~(graph : Graph.t) : unit =
  let factor = Array.init return_window ~f:(fun _ -> gaussian ~rng ~sigma:0.05) in
  Graph.set_factor_returns graph factor;
  List.iter book ~f:(fun (symbol, sector, _, _) ->
      let beta = rate_beta sector in
      Graph.set_returns graph symbol
        (Array.init return_window ~f:(fun i ->
             (beta *. factor.(i)) +. gaussian ~rng ~sigma:0.009)))

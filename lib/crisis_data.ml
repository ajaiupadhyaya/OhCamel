(* Cached daily closes through real market crises.

   var_backtest.ml validates the VaR model against deterministic synthetic
   series, and that is the right way to build a coverage battery: a series whose
   volatility regime you chose yourself is the only kind where you know in
   advance which tests should reject. What it cannot establish is whether the
   model survives the thing it exists for. Synthetic tail events are drawn from
   the distribution the author had in mind. Real ones are not, and the gap
   between those two facts is most of what goes wrong with risk models.

   So this module supplies the same battery with three real windows. Nothing
   about the analysis changes -- Var_backtest.rolling is used unmodified, at the
   same window and the same confidence -- and that is the point. The claim being
   upgraded is about the DATA, not about the method, so the method must be
   visibly identical or the comparison means nothing.

   WHY A CACHE, AND WHY IT IS COMMITTED

   The three CSVs in docs/crisis/ are in the repository. `make backtest-crisis`
   therefore needs no network, no credentials and no API key, and produces the
   same table on a reviewer's machine as it does on the author's. A backtest
   whose inputs are fetched at run time is a backtest whose published numbers
   nobody else can check, which is the failure mode this whole file exists to
   avoid one level up.

   The cache is populated by tools/fetch_crisis_data.py, which is a script and
   not part of this library because it runs once and the engine never calls it.
   That file documents where the data comes from and why the obvious source was
   not usable: Alpaca, which this project already speaks to, begins its
   historical bars in 2016, so the 2008 window is unreachable through it at any
   subscription tier.

   A MISSING CACHE IS FATAL

   [load] returns an error naming the file and the command that would create it.
   It never falls back to a synthetic series. The failure that convention exists
   to prevent is specific and bad: a "crisis backtest" silently scoring
   generated data would print a table indistinguishable from the real one, under
   a heading claiming it had been tested against 2008.

   Nothing here references Incremental. The portfolio return series is produced
   BY the engine -- see [portfolio_returns_of_book] -- rather than by arithmetic
   in this file. *)

open Core
open Types

module Window = struct
  type t = {
    (* The window's short name, which is also its file's basename. *)
    name : string;
    (* The first comment line of the CSV, carried through so the output can say
       what the reader is looking at without a second table of prose to keep in
       sync with the data. *)
    description : string;
    (* Session dates, ascending. Only sessions on which every symbol traded --
       the file is written as an inner join, because a covariance matrix needs
       its inputs aligned to the same days. Pairing one name's Monday with
       another's Wednesday is the failure graph.ml's [aligned_returns] node
       exists to prevent, and it is no less wrong offline. *)
    dates : string array;
    (* Adjusted closes per symbol, each array the same length as [dates] and in
       the same order. Adjusted, not raw: an unadjusted 2-for-1 split reads as a
       -50% single-day return and becomes the entire tail of a 60-day window at
       95% confidence. *)
    closes : float array Symbol.Map.t;
  }
  [@@deriving fields ~getters]

  let sessions t = Array.length t.dates
  let symbols t = Map.keys t.closes
end

(* Relative to the working directory, exactly as [Config.default_book_path] is.
   Every mode in this project is run from the repository root -- the Makefile
   targets all are -- so a relative default is the honest description of how it
   is used, and an absolute one baked in at build time would be a lie the first
   time the repo moved.

   [?dir] exists for the tests, which dune runs from inside _build with a
   different working directory. It is a parameter rather than an environment
   read on purpose: a library whose behaviour changes with the environment is
   the invisible dependency graph.ml is arranged against, and "which directory"
   is precisely the kind of thing that should travel down the call stack where
   it can be seen. *)
let cache_dir = "docs/crisis"
let path_for ?(dir = cache_dir) ~(name : string) () = Filename.concat dir (name ^ ".csv")

(* The three windows, and why these three.

   Two sharp shocks and one slow grind. That contrast is deliberate: an
   equal-weighted volatility window fails in a specific, predictable way when
   volatility jumps, and if all three windows were jumps the result would say
   less than it appears to. 2022 is the control -- a year with a large drawdown
   and no single day over 10% -- and a model that fails there is failing for a
   different reason than one that fails in 2008. *)
let window_names = [ "gfc"; "covid"; "rates-2022" ]

(* ------------------------------------------------------------------------ *)
(* Reading the cache                                                         *)
(* ------------------------------------------------------------------------ *)

let parse_float ~line ~field s =
  match Float.of_string_opt (String.strip s) with
  | Some f when Float.is_finite f && Float.( > ) f 0.0 -> Ok f
  | Some f -> Or_error.errorf "line %d, column %S: %f is not a usable close" line field f
  | None -> Or_error.errorf "line %d, column %S: %S is not a number" line field s

(* Parse the CSV text. Separate from [load] so the tests can drive it with a
   literal string rather than a fixture file -- the same split config.ml uses
   between [Book.of_string] and [Book.load], and for the same reason: a parser
   that can only be tested through the filesystem tends not to get tested at the
   edges. *)
let of_string ~(name : string) (contents : string) : Window.t Or_error.t =
  let open Or_error.Let_syntax in
  let all_lines = String.split_lines contents in
  let description =
    (* The first comment line, with its marker stripped. Absent is not an error;
       an unlabelled window is less useful, not broken. *)
    List.find_map all_lines ~f:(fun l ->
        if String.is_prefix l ~prefix:"#" then
          Some (String.strip (String.drop_prefix l 1))
        else None)
    |> Option.value ~default:name
  in
  let rows =
    List.filter all_lines ~f:(fun l ->
        (not (String.is_prefix l ~prefix:"#")) && not (String.is_empty (String.strip l)))
  in
  match rows with
  | [] -> Or_error.errorf "%s: no data rows" name
  | header :: data ->
      let columns = String.split header ~on:',' |> List.map ~f:String.strip in
      let%bind symbol_names =
        match columns with
        | "date" :: (_ :: _ as symbols) -> Ok symbols
        | _ ->
            Or_error.errorf
              "%s: header must be `date` followed by at least one symbol, got %S" name
              header
      in
      let width = List.length columns in
      if List.is_empty data then Or_error.errorf "%s: header but no rows" name
      else
        let%bind parsed =
          (* Row index offset by 2 so the reported line number matches what an
             editor shows for the data section: one for the header, one for
             counting from 1. Comment lines shift this, which is why the message
             says "data line" rather than "line". *)
          List.mapi data ~f:(fun i row ->
              let line = i + 2 in
              let fields = String.split row ~on:',' |> List.map ~f:String.strip in
              if List.length fields <> width then
                Or_error.errorf "%s: data line %d has %d fields, expected %d" name line
                  (List.length fields) width
              else
                match fields with
                | date :: values ->
                    let%map closes =
                      List.zip_exn symbol_names values
                      |> List.map ~f:(fun (field, v) -> parse_float ~line ~field v)
                      |> Or_error.all
                    in
                    (date, closes)
                | [] -> Or_error.errorf "%s: data line %d is empty" name line)
          |> Or_error.all
        in
        let dates = Array.of_list_map parsed ~f:fst in
        let closes =
          Symbol.Map.of_alist_exn
            (List.mapi symbol_names ~f:(fun column symbol ->
                 ( Symbol.of_string symbol,
                   Array.of_list_map parsed ~f:(fun (_, values) ->
                       List.nth_exn values column) )))
        in
        Ok { Window.name; description; dates; closes }

let load ?dir ~(name : string) () : Window.t Or_error.t =
  let path = path_for ?dir ~name () in
  match Sys_unix.file_exists path with
  | `Yes ->
      Or_error.tag_arg
        (of_string ~name (In_channel.read_all path))
        "ohcamel: malformed crisis cache" path String.sexp_of_t
  | `No | `Unknown ->
      (* Named, actionable, and deliberately NOT a fallback. Substituting a
         synthetic series here would produce a table that looked exactly like
         the real one under a heading claiming it had been tested against a real
         crisis, which is a worse outcome than not running at all. *)
      (* [error_string] rather than [errorf]: Error.createf builds a sexp atom,
         and printing a multi-line atom escapes every newline, so the one
         message whose whole job is to be read by a human comes out as a
         backslash-riddled s-expression. config.ml's missing-credential message
         is built the same way for the same reason. *)
      Or_error.error_string
        (String.concat ~sep:"\n"
           [
             Printf.sprintf "ohcamel: no cached crisis data at %s." path;
             "";
             "  The cache is normally committed to the repository. If it is missing,";
             "  repopulate it with:";
             "";
             "    python3 tools/fetch_crisis_data.py";
             "";
             "  That script fetches adjusted daily closes from a public endpoint and";
             "  rewrites docs/crisis/*.csv. It is the only part of this analysis that";
             "  touches the network, and it is not run by `make backtest-crisis`.";
           ])

(* Stops at the first missing window rather than accumulating every failure.

   [Or_error.all] would combine all three, and since a missing cache is almost
   always a missing DIRECTORY, the reader would get the same paragraph of
   instructions three times and have to notice it was the same paragraph. One
   cause, one message. *)
let load_all ?dir () : Window.t list Or_error.t =
  let open Or_error.Let_syntax in
  let%map reversed =
    List.fold_result window_names ~init:[] ~f:(fun acc name ->
        let%map window = load ?dir ~name () in
        window :: acc)
  in
  List.rev reversed

(* ------------------------------------------------------------------------ *)
(* From closes to a book's return series                                     *)
(* ------------------------------------------------------------------------ *)

(* Per-symbol simple returns, through the SAME function the live REST backfill
   uses.

   Reused rather than rewritten because the return convention is exactly the
   kind of thing that drifts between two implementations -- simple versus log,
   and whether a non-positive close is dropped or divided by -- and a backtest
   computing returns differently from the engine it is validating would be
   testing a model nobody runs. *)
let returns (window : Window.t) : float array Symbol.Map.t =
  Map.map window.Window.closes ~f:(fun closes ->
      Alpaca_rest.returns_of_closes (Array.to_list closes))

(* The book's own return series, computed BY THE ENGINE.

   This is the step that would otherwise be a second implementation. The obvious
   version -- sum over instruments of weight times return, four lines, right
   here -- is the same arithmetic as graph.ml's [portfolio_returns] node, and
   the two would be free to diverge the first time someone changed a convention
   in one of them. Instead a graph is built with the real book, the real return
   histories are written into its input cells, and the series is read back out
   of the node that produces the live VaR. Same discipline as stress.ml's use of
   [Graph.fork], applied offline.

   [instruments] and [positions] describe the book to hold through the window;
   [marks] are the prices to value it at. Together they fix the weights, and the
   weights are held constant across the window -- which is graph.ml's documented
   approximation and is the question a limit is actually asking: "what would
   TODAY's book have done through this history", not "what did the book that
   existed then do".

   Returns [None] if the window is too short to form a series, which for these
   files it never is; the caller surfaces it rather than assuming. *)
let portfolio_returns_of_book ~(instruments : Instrument.t list)
    ~(positions : (Symbol.t * Qty.t) list) ~(marks : (Symbol.t * Price.t) list)
    (window : Window.t) : float array option =
  let by_symbol = returns window in
  let periods =
    Map.fold by_symbol ~init:0 ~f:(fun ~key:_ ~data acc ->
        Int.max acc (Array.length data))
  in
  if periods < 2 then None
  else begin
    let graph =
      (* No limits: this graph exists to evaluate one node. A limit would be
         evaluated on every stabilize and reported to nobody.

         return_window is the whole series, so [set_returns] does not trim it.
         The 60-day rolling window that the coverage test actually uses is
         applied later, by Var_backtest.rolling, over the series this produces
         -- the two windows are different things and conflating them is how a
         backtest ends up scoring 60 observations instead of 500. *)
      Graph.create ~instruments ~limits:[] ~confidence:0.95 ~return_window:periods ()
    in
    Exn.protect
      ~finally:(fun () -> Graph.destroy graph)
      ~f:(fun () ->
        List.iter marks ~f:(fun (symbol, price) -> Graph.set_price graph symbol price);
        List.iter positions ~f:(fun (symbol, qty) -> Graph.set_qty graph symbol qty);
        Map.iteri by_symbol ~f:(fun ~key:symbol ~data ->
            if Graph.knows_symbol graph symbol then Graph.set_returns graph symbol data);
        Graph.stabilize graph;
        Graph.portfolio_returns graph)
  end

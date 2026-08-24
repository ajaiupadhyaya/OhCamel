(* Phase 2. Credentials, the book file, and runtime knobs.

   Two rules govern this module.

   1. SECRETS NEVER LEAVE. API keys are read from the process environment and
      wrapped in [Secret.t], whose [sexp_of_t] prints "<redacted>". That is not
      politeness -- this engine sexps its config into logs and will eventually
      serve state over HTTP, and a key that can be printed will eventually be
      printed. The only way to get the bytes back out is [Secret.to_string],
      which exists solely for the wire and is grep-able as a single call site
      per credential.

   2. A MISSING KEY IS FATAL. [Credentials.load] returns an error naming the
      variable; it never substitutes a default and never quietly falls back to
      synthetic mode. A risk engine that looks live but is showing made-up
      numbers is worse than one that refuses to start.

   The book (positions, cash, limits) is read from a sexp file rather than
   compiled in, so the same binary can run different books. The file's types are
   deliberately NOT Types.Limit and friends: this module owns plain
   string/float specs and converts, so that a malformed file produces a message
   about the file rather than a parse failure deep in a domain type. *)

open Core

(* ------------------------------------------------------------------------ *)
(* Secrets                                                                   *)
(* ------------------------------------------------------------------------ *)

module Secret : sig
  type t

  val of_string : string -> t

  (* The only way out. Call it at the point of use -- do not stash the result. *)
  val to_string : t -> string

  (* Prints "<redacted>". Deliberately shadows what deriving would have
     produced, and there is a test that pins it. *)
  val sexp_of_t : t -> Sexp.t
end = struct
  type t = string

  let of_string = Fn.id
  let to_string = Fn.id
  let sexp_of_t (_ : t) = Sexp.Atom "<redacted>"
end

(* ------------------------------------------------------------------------ *)
(* Credentials                                                               *)
(* ------------------------------------------------------------------------ *)

module Credentials = struct
  type t = { alpaca_key : Secret.t; alpaca_secret : Secret.t; fred_api_key : Secret.t }
  [@@deriving sexp_of]

  let alpaca_key_var = "ALPACA_API_KEY"
  let alpaca_secret_var = "ALPACA_SECRET_KEY"
  let fred_api_key_var = "FRED_API_KEY"

  (* An empty variable is treated as absent. `export FOO=` is a far more common
     way to end up without a credential than never setting it at all, and the
     failure it produces otherwise is a 401 from the far end rather than a
     message about configuration. *)
  let required var =
    match Sys.getenv var with
    | Some value when not (String.is_empty (String.strip value)) ->
        Ok (Secret.of_string (String.strip value))
    | Some _ -> Or_error.errorf "%s is set but empty" var
    | None -> Or_error.errorf "%s is not set" var

  let load () =
    let open Or_error.Let_syntax in
    (* Every missing variable is reported at once. Discovering them one run at a
       time is a needlessly slow way to configure three keys. *)
    let results =
      [
        (alpaca_key_var, required alpaca_key_var);
        (alpaca_secret_var, required alpaca_secret_var);
        (fred_api_key_var, required fred_api_key_var);
      ]
    in
    match List.filter_map results ~f:(fun (_, r) -> Result.error r) with
    | _ :: _ as errors ->
        Or_error.error_string
          (String.concat ~sep:"\n"
             ("ohcamel: missing credentials."
              :: List.map errors ~f:(fun e -> "  - " ^ Error.to_string_hum e)
             @ [
                 "";
                 "Live mode needs all three. Export them, or source a file that does:";
                 "  set -a; source /path/to/.env; set +a";
               ]))
    | [] ->
        let%bind alpaca_key = required alpaca_key_var in
        let%bind alpaca_secret = required alpaca_secret_var in
        let%map fred_api_key = required fred_api_key_var in
        { alpaca_key; alpaca_secret; fred_api_key }
end

(* ------------------------------------------------------------------------ *)
(* The book                                                                  *)
(* ------------------------------------------------------------------------ *)

(* ------------------------------------------------------------------------ *)
(* Alerting and the kill switch                                              *)
(* ------------------------------------------------------------------------ *)

(* Phase 4 is the only part of this system that can act on the outside world,
   and the brief is explicit about it: keep it behind explicit config, and do
   not wire the kill switch to anything that places real trades.

   So every default here is inert. [enabled = false] means a breach is computed,
   displayed, and otherwise ignored. Turning alerting on is a decision someone
   has to write down in a file, and turning the kill switch on is a second,
   separate decision -- because "tell me when a limit breaks" and "act when a
   limit breaks" are different levels of trust and should not share a switch. *)
module Alerts = struct
  module Sink = struct
    type t =
      | Log (* stdout, always safe *)
      | File of string (* append to a path *)
      | Slack (* POST to SLACK_WEBHOOK_URL, the only sink that leaves the machine *)
      | Dry_run (* format and print exactly what WOULD be sent, send nothing *)
    [@@deriving sexp, compare, equal]
  end

  type t = {
    enabled : bool;
    sinks : Sink.t list;
    (* Hysteresis. A limit sitting exactly on its threshold would otherwise
       oscillate breached/cleared on every tick and produce an alert storm --
       which is how an alerting system trains its reader to ignore it. Once
       raised, an alert clears only when utilisation falls back below this
       fraction of the limit. 0.95 means "it has to come back 5% inside the line
       before I will call it resolved". *)
    clear_below : float;
    kill_switch_enabled : bool;
    (* Which limits are hard enough to trip the switch. Empty means none, even
       when the switch is enabled -- so a misconfigured file cannot arm
       something that trips on everything. *)
    kill_switch_trips_on : string list;
  }
  [@@deriving sexp]

  let default =
    {
      enabled = false;
      sinks = [ Sink.Log ];
      clear_below = 0.95;
      kill_switch_enabled = false;
      kill_switch_trips_on = [];
    }

  let validate (t : t) : unit Or_error.t =
    if not (Float.( > ) t.clear_below 0.0 && Float.( <= ) t.clear_below 1.0) then
      Or_error.errorf
        "alerts: clear_below must be in (0, 1], got %f -- it is the fraction of the \
         limit an alert must fall back inside before it is called resolved"
        t.clear_below
    else if t.kill_switch_enabled && List.is_empty t.kill_switch_trips_on then
      Or_error.error_string
        "alerts: the kill switch is enabled but trips on no limits. Name the limits it \
         should act on, or disable it -- an armed switch with no trigger is a \
         configuration someone will misread."
    else Ok ()
end

module Book = struct
  module Position_spec = struct
    type t = { symbol : string; sector : string; qty : float } [@@deriving sexp]
  end

  module Limit_spec = struct
    (* Mirrors Types.Limit but in plain strings and floats, with round-trip sexp
       conversion. Types.Limit has sexp_of but no of_sexp (its Symbol and Sector
       are abstract), and adding one would mean giving those types a parser that
       accepts any string -- which is exactly the property they exist to
       withhold. Converting here keeps the abstraction and puts validation at
       the file boundary. *)
    type scope = Instrument of string | Sector of string | Portfolio [@@deriving sexp]

    type kind =
      | Gross_notional of float
      | Value_at_risk of float
      | Component_var of float
      | Max_drawdown of float
    [@@deriving sexp]

    type t = { name : string; scope : scope; kind : kind } [@@deriving sexp]

    let to_limit (t : t) : Types.Limit.t =
      {
        Types.Limit.name = t.name;
        scope =
          (match t.scope with
          | Instrument s -> Types.Limit.Instrument (Types.Symbol.of_string s)
          | Sector s -> Types.Limit.Sector (Types.Sector.of_string s)
          | Portfolio -> Types.Limit.Portfolio);
        kind =
          (match t.kind with
          | Gross_notional n -> Types.Limit.Gross_notional (Types.Notional.of_float n)
          | Value_at_risk n -> Types.Limit.Value_at_risk (Types.Notional.of_float n)
          | Component_var n -> Types.Limit.Component_var (Types.Notional.of_float n)
          | Max_drawdown f -> Types.Limit.Max_drawdown f);
      }
  end

  type t = {
    cash : float;
    positions : Position_spec.t list;
    limits : Limit_spec.t list;
    (* Optional, and absent means inert. An existing book file keeps working and
       keeps doing nothing, which is the right default for the one part of this
       system that can act. *)
    alerts : Alerts.t; [@sexp.default Alerts.default] [@sexp_drop_default.sexp]
  }
  [@@deriving sexp]

  let instruments (t : t) : Types.Instrument.t list =
    List.map t.positions ~f:(fun p ->
        {
          Types.Instrument.symbol = Types.Symbol.of_string p.symbol;
          sector = Types.Sector.of_string p.sector;
        })

  let limits (t : t) : Types.Limit.t list = List.map t.limits ~f:Limit_spec.to_limit

  let of_string (contents : string) : t Or_error.t =
    Or_error.try_with (fun () -> t_of_sexp (Sexp.of_string contents))

  let load (path : string) : t Or_error.t =
    Or_error.tag_arg
      (let open Or_error.Let_syntax in
       let%bind contents = Or_error.try_with (fun () -> In_channel.read_all path) in
       of_string contents)
      "ohcamel: cannot load book file" path String.sexp_of_t
end

(* ------------------------------------------------------------------------ *)
(* Runtime settings                                                          *)
(* ------------------------------------------------------------------------ *)

module Runtime = struct
  type t = {
    (* Alpaca's data feed tier. "iex" is what a free account gets; "sip" needs
         a paid subscription and "delayed_sip" is 15 minutes behind. Wrong value
         here shows up as an authorisation failure after a successful connect,
         which is confusing enough to be worth naming in config. *)
    alpaca_feed : string;
    (* FRED series driving the factor-exposure node. DGS10 is the 10-year
         constant-maturity Treasury yield. *)
    fred_series_id : string;
    fred_poll_interval : Time_ns.Span.t;
    (* How long a symbol may go without a print before it is called stale.
         This is a judgement about the instrument, not about the network: a
         thinly-traded name can legitimately be quiet for minutes during RTH,
         while a liquid one going quiet for thirty seconds means the feed is
         gone. One threshold for now, and it is deliberately generous. *)
    staleness_threshold : Time_ns.Span.t;
    (* How often the staleness clock advances. Only the feed-health nodes are
         downstream of it -- see graph.ml, where that isolation is enforced and
         tested. *)
    clock_interval : Time_ns.Span.t;
    confidence : float;
    return_window : int;
    snapshot_interval : Time_ns.Span.t;
  }
  [@@deriving sexp_of]

  let default =
    {
      alpaca_feed = "iex";
      fred_series_id = "DGS10";
      fred_poll_interval = Time_ns.Span.of_hr 6.0;
      staleness_threshold = Time_ns.Span.of_sec 90.0;
      clock_interval = Time_ns.Span.of_sec 5.0;
      confidence = 0.95;
      return_window = 60;
      snapshot_interval = Time_ns.Span.of_sec 10.0;
    }

  (* Environment overrides for the two knobs most likely to need changing
     without a rebuild. Everything else is edited in source, on the grounds that
     a config surface nobody uses is a config surface nobody tests. *)
  let of_env () =
    let string_var name default =
      match Sys.getenv name with
      | Some v when not (String.is_empty (String.strip v)) -> String.strip v
      | _ -> default
    in
    {
      default with
      alpaca_feed = string_var "OHCAMEL_ALPACA_FEED" default.alpaca_feed;
      fred_series_id = string_var "OHCAMEL_FRED_SERIES" default.fred_series_id;
    }
end

type t = { credentials : Credentials.t; book : Book.t; runtime : Runtime.t }
[@@deriving sexp_of]

let default_book_path = "book.sexp"

let load ?(book_path = default_book_path) () : t Or_error.t =
  let open Or_error.Let_syntax in
  let%bind credentials = Credentials.load () in
  let%map book = Book.load book_path in
  { credentials; book; runtime = Runtime.of_env () }

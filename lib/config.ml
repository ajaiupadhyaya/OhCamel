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

(* ------------------------------------------------------------------------ *)
(* The universe cap                                                          *)
(* ------------------------------------------------------------------------ *)

(* The most names a book may declare. Alpaca's free plan streams at most
   [stream_cap] of them; the rest are marked by polling the latest trades
   (design §4.3), so the cap is the engine's and not the plan's. run_live
   (bin/main.ml) refuses a larger book before it connects, and [Book.errors]
   below reports it, and both read THIS number: check-book exists so the engine
   never dies on a book at startup, and a cap the checker spelled for itself
   would be a second number, free to drift from the one the engine enforces. *)
let universe_cap = 64

(* How many names the free plan's websocket carries. A book's (stream ...) list
   may name at most this many, and they are streamed first. *)
let stream_cap = 30

module Book = struct
  module Position_spec = struct
    type t = { symbol : string; sector : string; qty : float } [@@deriving sexp]
  end

  (* The desk's rules and the spread table its cost analysis compares against
     (design §3.3, §3.7, §3.9). Configuration only: plain numbers the desk
     library reads. Nothing here trades, and the defaults trade nothing --
     [trading] is Disabled unless a book says otherwise, for the same reason
     alerting is off unless a book says otherwise. *)
  module Desk_spec = struct
    type trading = Enabled | Disabled [@@deriving sexp, compare, equal]

    type t = {
      trading : trading; [@sexp.default Disabled]
      max_order_notional : float; [@sexp.default 25_000.0]
      max_adv_participation : float; [@sexp.default 0.01]
      price_collar : float; [@sexp.default 0.05]
      duplicate_window_s : float; [@sexp.default 10.0]
      (* 200: a roster of two dozen strategies rests more than twenty
         market-on-open orders at once; the example book says so beside the
         field. *)
      max_open_orders : int; [@sexp.default 200]
      spread_bps_default : float; [@sexp.default 5.0]
      spread_bps : (string * float) list; [@sexp.default []]
      (* Scales the incubation roster: each incubation strategy's effective
         fraction is its capital_fraction x min(1, this / their sum)
         (Signals_spec.effective_fraction; design §4.3). 0 sizes every
         incubation strategy to nothing. *)
      incubation_cap : float; [@sexp.default 0.0]
      (* The overlay's annualised volatility target, and how it is read: an
         overlay older than [overlay_max_age_minutes] is stale, a stale one
         falls back to [overlay_fallback], and a move of more than
         [overlay_band] in the effective scale re-sizes the strategies that
         adjust intraday. *)
      vol_target : float; [@sexp.default 0.10]
      overlay_max_age_minutes : int; [@sexp.default 60]
      overlay_fallback : float; [@sexp.default 0.5]
      overlay_band : float; [@sexp.default 0.10]
      (* The intraday rebalance: limit legs priced this far past the mark,
         legs below [min_leg_notional] dropped, each wave waited on for at
         most [wave_timeout_s]. *)
      intraday_slip_bps : float; [@sexp.default 10.0]
      min_leg_notional : float; [@sexp.default 200.0]
      wave_timeout_s : float; [@sexp.default 180.0]
      (* New York wall-clock HH:MM at which a (horizon close) strategy is
         flattened. *)
      flatten_at : string; [@sexp.default "15:50"]
      (* The names streamed first; the rest of the universe fills the stream
         to [stream_cap] in book order, and anything past it is polled. *)
      stream : string list; [@sexp.default []]
    }
    [@@deriving sexp, compare, equal]

    let default = t_of_sexp (Sexp.List [])

    (* [flatten_at] as a time of day, refused unless it is HH:MM inside the
       regular session: at or after the 09:30 open and before the 16:00
       close, because a flatten at the close is a flatten nobody can fill. *)
    let flatten_ofday (s : string) : (Time_ns.Ofday.t, string) Result.t =
      let shape =
        String.length s = 5
        && Char.is_digit s.[0]
        && Char.is_digit s.[1]
        && Char.equal s.[2] ':'
        && Char.is_digit s.[3]
        && Char.is_digit s.[4]
      in
      let not_hh_mm () = Error (sprintf "flatten_at must be HH:MM, got %S" s) in
      if not shape then not_hh_mm ()
      else
        match Time_ns.Ofday.of_string s with
        | exception _ -> not_hh_mm ()
        | ofday ->
            let opens = Time_ns.Ofday.create ~hr:9 ~min:30 () in
            let closes = Time_ns.Ofday.create ~hr:16 () in
            if Time_ns.Ofday.(ofday >= opens && ofday < closes) then Ok ofday
            else
              Error
                (sprintf
                   "flatten_at must be inside the regular session [09:30, 16:00), got %s"
                   s)

    (* The bounds after the spread table are written positively too, for the
       reason the spread comment gives: "not (0 <= x && x <= 1)" refuses a nan
       where "x < 0 || x > 1" lets it through. *)
    let validate ~(universe : string list) (t : t) : unit Or_error.t =
      let fail field why value = Or_error.errorf "desk: %s %s, got %g" field why value in
      if Float.( <= ) t.max_order_notional 0.0 then
        fail "max_order_notional" "must be positive" t.max_order_notional
      else if
        not
          (Float.( > ) t.max_adv_participation 0.0
          && Float.( <= ) t.max_adv_participation 1.0)
      then fail "max_adv_participation" "must be in (0, 1]" t.max_adv_participation
      else if not (Float.( > ) t.price_collar 0.0 && Float.( < ) t.price_collar 1.0) then
        fail "price_collar" "must be in (0, 1)" t.price_collar
      else if Float.( < ) t.duplicate_window_s 0.0 then
        fail "duplicate_window_s" "may not be negative" t.duplicate_window_s
      else if t.max_open_orders < 1 then
        fail "max_open_orders" "must be at least 1" (Float.of_int t.max_open_orders)
      else if
        (not (Float.is_finite t.spread_bps_default))
        || Float.( < ) t.spread_bps_default 0.0
      then
        fail "spread_bps_default" "must be finite and may not be negative"
          t.spread_bps_default
      else
        (* A nan compares false against every bound, so "b < 0.0" alone lets a
           nan spread through and it would turn LVaR into nan downstream
           (liquidity.ml). [not (is_finite b)] catches nan and both infinities
           regardless of sign. *)
        match
          List.find t.spread_bps ~f:(fun (_, b) ->
              (not (Float.is_finite b)) || Float.( < ) b 0.0)
        with
        | Some (symbol, b) ->
            fail ("spread_bps for " ^ symbol) "must be finite and may not be negative" b
        | None -> (
            let unit_interval x = Float.(x >= 0.0 && x <= 1.0) in
            let collar_bps = t.price_collar *. 1e4 in
            if not (unit_interval t.incubation_cap) then
              fail "incubation_cap" "must be in [0, 1]" t.incubation_cap
            else if not Float.(t.vol_target > 0.0 && t.vol_target <= 1.0) then
              fail "vol_target" "must be in (0, 1]" t.vol_target
            else if t.overlay_max_age_minutes < 1 then
              fail "overlay_max_age_minutes" "must be at least 1"
                (Float.of_int t.overlay_max_age_minutes)
            else if not (unit_interval t.overlay_fallback) then
              fail "overlay_fallback" "must be in [0, 1]" t.overlay_fallback
            else if not (unit_interval t.overlay_band) then
              fail "overlay_band" "must be in [0, 1]" t.overlay_band
            else if
              (* At least 0, because a negative slip prices the leg on the
                 near side of the mark; below the collar, because the
                 collar rule would refuse the leg. *)
              not Float.(t.intraday_slip_bps >= 0.0 && t.intraday_slip_bps < collar_bps)
            then
              fail "intraday_slip_bps" "must be in [0, price_collar x 1e4)"
                t.intraday_slip_bps
            else if not Float.(t.min_leg_notional >= 0.0) then
              fail "min_leg_notional" "may not be negative" t.min_leg_notional
            else if not Float.(t.wave_timeout_s > 0.0) then
              fail "wave_timeout_s" "must be positive" t.wave_timeout_s
            else
              match flatten_ofday t.flatten_at with
              | Error why -> Or_error.errorf "desk: %s" why
              | Ok _ -> (
                  if List.length t.stream > stream_cap then
                    Or_error.errorf "desk: stream names %d symbols; at most %d stream"
                      (List.length t.stream) stream_cap
                  else
                    match
                      List.find t.stream ~f:(fun sym ->
                          not (List.mem universe sym ~equal:String.equal))
                    with
                    | Some sym ->
                        Or_error.errorf
                          "desk: stream names %s, which is not in the book's universe" sym
                    | None -> Ok ()))
  end

  (* The strategies whose signals the desk reads (design §3.12). Configuration
     only, like Desk_spec: the desk library reads these names and numbers, and
     nothing here reads a file or judges a signal.

     SIZING DEFAULTS TO ADVISORY (ruling 2). A signal that passes R1-R7 is
     sized only when its strategy says (sizing live), and promoting one is the
     owner's decision, written into the book by hand -- a model does not
     promote itself, so an absent field can only ever mean the safer of the
     two.

     Validated against the book's own universe, because a strategy that names
     a symbol the book does not hold could never pass R7 and would only ever
     be a typo discovered one signal at a time. *)
  module Signals_spec = struct
    (* INCUBATION (the charter's paper tier, docs/CHARTER.md): a strategy the
       desk sizes on the paper venue whatever its validation says, scaled
       by [Desk_spec.incubation_cap], labelled and scored, never live. *)
    type sizing = Advisory | Live | Incubation [@@deriving sexp, compare, equal]

    let sizing_to_string = function
      | Advisory -> "advisory"
      | Live -> "live"
      | Incubation -> "incubation"

    (* How often the strategy's documents arrive: once a session, or through
       it. *)
    module Cadence = struct
      type t = Daily | Intraday [@@deriving sexp, compare, equal]
    end

    (* When an overlay move re-sizes the strategy: intraday, or at the next
       open only. *)
    module Adjust = struct
      type t = Intraday | Open [@@deriving sexp, compare, equal]
    end

    (* Whether a position is carried overnight or flattened at the desk's
       [flatten_at]. *)
    module Horizon = struct
      type t = Carry | Close [@@deriving sexp, compare, equal]
    end

    (* The stop rules: the strategy is stopped when its drawdown from peak on
       allocated capital reaches [drawdown], or [sessions] sessions have passed
       since its first fill. *)
    module Stop = struct
      type t = { drawdown : float; sessions : int } [@@deriving sexp, compare, equal]
    end

    module Strategy = struct
      type t = {
        name : string;
        symbols : string list;
        (* R4's bound, in recorded sessions: interface/README.md's default. *)
        max_age : int; [@sexp.default 3]
        sizing : sizing; [@sexp.default Advisory]
        capital_fraction : float;
        cadence : Cadence.t; [@sexp.default Cadence.Daily]
        adjust : Adjust.t; [@sexp.default Adjust.Intraday]
        (* R4's bound for an intraday document, in minutes. *)
        max_age_minutes : int; [@sexp.default 20]
        horizon : Horizon.t; [@sexp.default Horizon.Carry]
        (* [@sexp.option], so the book writes (stop ((drawdown F) (sessions N)))
           and (reinstated DATE) bare, and an absent field is None. *)
        stop : Stop.t option; [@sexp.option]
        reinstated : Date.t option; [@sexp.option]
      }
      [@@deriving sexp, compare, equal]
    end

    type t = { strategies : Strategy.t list } [@@deriving sexp, compare, equal]

    (* interface/signal.schema.json's pattern for [strategy], ^[a-z][a-z0-9_]{1,63}$,
       written out: a name the schema would refuse can never arrive in a
       signal, so registering one is a book that can never be fed. *)
    let name_ok name =
      let n = String.length name in
      n >= 2 && n <= 64
      && Char.between name.[0] ~low:'a' ~high:'z'
      && String.for_all name ~f:(fun c ->
          Char.between c ~low:'a' ~high:'z' || Char.is_digit c || Char.equal c '_')

    (* Written positively, as contract.ml's R7 is: every comparison with NaN is
       false, so "not (f > 0 && f <= 1)" refuses a NaN fraction where
       "f <= 0 || f > 1" would let it through. The live sum carries R7's own
       1e-9, so that fractions like 0.1 x 10 are not refused for the last bit
       of a float, and so do the incubation sum and the two tiers' together. *)
    let validate ~(universe : string list) (t : t) : unit Or_error.t =
      let fail name fmt =
        Printf.ksprintf (fun s -> Or_error.errorf "signals: %s: %s" name s) fmt
      in
      let names = List.map t.strategies ~f:(fun s -> s.Strategy.name) in
      let check (s : Strategy.t) =
        let open Strategy in
        if not (name_ok s.name) then
          fail s.name "the name must match ^[a-z][a-z0-9_]{1,63}$, the schema's pattern"
        else if List.is_empty s.symbols then fail s.name "symbols is empty"
        else
          match
            ( List.find s.symbols ~f:(fun sym ->
                  not (List.mem universe sym ~equal:String.equal)),
              List.find_a_dup s.symbols ~compare:String.compare )
          with
          | Some sym, _ -> fail s.name "%s is not in the book's universe" sym
          | None, Some sym -> fail s.name "symbols lists %s twice" sym
          | None, None -> (
              if
                (* At least 1: with 0 no signal could ever be deferred -- the one
                 the service writes before its close is recorded would be
                 rejected at R3 on its first look. *)
                s.max_age < 1
              then fail s.name "max_age must be at least 1, got %d" s.max_age
              else if not Float.(s.capital_fraction > 0.0 && s.capital_fraction <= 1.0)
              then
                fail s.name "capital_fraction must be in (0, 1], got %g"
                  s.capital_fraction
              else if s.max_age_minutes < 1 then
                fail s.name "max_age_minutes must be at least 1, got %d" s.max_age_minutes
              else
                match (s.stop, s.reinstated) with
                | Some stop, _
                  when not Float.(stop.Stop.drawdown > 0.0 && stop.Stop.drawdown <= 1.0)
                  ->
                    fail s.name "stop.drawdown must be in (0, 1], got %g"
                      stop.Stop.drawdown
                | Some stop, _ when stop.Stop.sessions < 1 ->
                    fail s.name "stop.sessions must be at least 1, got %d"
                      stop.Stop.sessions
                | None, Some date ->
                    (* Reinstating is the owner's answer to a stop, and the
                       desk never un-stops on its own: with no stop there is
                       nothing for the date to answer. *)
                    fail s.name "reinstated is %s but the strategy has no stop"
                      (Date.to_string date)
                | _ -> Ok ())
      in
      match List.find_a_dup names ~compare:String.compare with
      | Some name -> fail name "two strategies have this name"
      | None -> (
          match List.find_map t.strategies ~f:(fun s -> Result.error (check s)) with
          | Some e -> Error e
          | None ->
              let tier sizing =
                List.sum
                  (module Float)
                  t.strategies
                  ~f:(fun s ->
                    if equal_sizing s.Strategy.sizing sizing then
                      s.Strategy.capital_fraction
                    else 0.0)
              in
              let live = tier Live in
              let incubation = tier Incubation in
              if not Float.(live <= 1.0 +. 1e-9) then
                Or_error.errorf
                  "signals: the live strategies' capital_fraction values sum to %g, more \
                   than 1"
                  live
              else if not Float.(incubation <= 1.0 +. 1e-9) then
                Or_error.errorf
                  "signals: the incubation strategies' capital_fraction values sum to \
                   %g, more than 1"
                  incubation
              else if not Float.(live +. incubation <= 1.0 +. 1e-9) then
                Or_error.errorf
                  "signals: the live and incubation strategies' capital_fraction values \
                   sum to %g, more than 1"
                  (live +. incubation)
              else Ok ())

    (* [Desk_spec.incubation_cap] is a proportional ceiling, not a bound the
       fractions must fit under: each incubation strategy's effective fraction
       is capital_fraction x min(1, incubation_cap / the incubation fractions'
       sum), so the canary is (incubation_cap 0.10) over the same roster and
       raising it is one edit. A live or advisory strategy's fraction is its
       own, unchanged. Pure: the desk sizes with it, check-book prints it. *)
    let effective_fraction ~(incubation_cap : float) (t : t) (s : Strategy.t) : float =
      match s.Strategy.sizing with
      | Advisory | Live -> s.Strategy.capital_fraction
      | Incubation ->
          let sum =
            List.sum
              (module Float)
              t.strategies
              ~f:(fun x ->
                if equal_sizing x.Strategy.sizing Incubation then
                  x.Strategy.capital_fraction
                else 0.0)
          in
          if Float.(sum <= 0.0) then 0.0
          else s.Strategy.capital_fraction *. Float.min 1.0 (incubation_cap /. sum)
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
    (* Optional, and absent means no trading -- see Desk_spec's comment above.
       An existing book file keeps parsing and the desk it feeds keeps its
       venue read-only. *)
    desk : Desk_spec.t; [@sexp.default Desk_spec.default] [@sexp_drop_default.sexp]
    (* Optional, and absent means no intake: the desk registers no strategy and
       reads no signal file. An existing book keeps parsing unchanged. *)
    signals : Signals_spec.t option; [@sexp.option]
  }
  [@@deriving sexp]

  let instruments (t : t) : Types.Instrument.t list =
    List.map t.positions ~f:(fun p ->
        {
          Types.Instrument.symbol = Types.Symbol.of_string p.symbol;
          sector = Types.Sector.of_string p.sector;
        })

  let limits (t : t) : Types.Limit.t list = List.map t.limits ~f:Limit_spec.to_limit

  (* The parse alone, with nothing validated. [of_string] is the startup path and
     stops at the first failure; [Config.check] wants a parsed book it can hand to
     every validator, so the two steps are separable here.

     The exception comes back raw rather than as an [Error.t], because the two
     callers want different things from it: [of_string] wants the [Error.t] it has
     always produced, and [check] wants the sentence inside it on one line. *)
  let parse (contents : string) : (t, exn) Result.t =
    try Ok (t_of_sexp (Sexp.of_string contents)) with exn -> Error exn

  (* Every validation failure this book has, as sentences, rather than the first
     one.

     [of_string] stops at the first, which is right on a startup path: the engine
     refuses to run either way and the operator's next act is to fix that one
     thing. [check-book] is the opposite case. Someone has just edited the file
     and wants the whole list, and a checker that reports one problem per run
     teaches its reader to fix one line and run again -- which is how an edit
     with three typos in it takes three rounds and a deploy window.

     The order is the order the engine applies them in once the file has
     parsed: the universe (run_live's cap on the count, then the graph's two
     rules about the list), the limits (graph construction), the alerts (sink
     construction), the desk, then the signals.

     Every sentence below is one of two kinds. Most come from the validator that
     owns them -- Limits.validate, Alerts.validate, Desk_spec.validate and
     Signals_spec.validate are CALLED here, not copied, so their wording cannot
     drift from what the engine prints. Three are restated, because this module
     cannot call the code that owns them: Graph.create's "need at least one
     instrument" and "appears twice" (graph.ml is downstream of config.ml) and
     run_live's universe cap (bin/ is downstream of lib/). Those three are
     spelled over the same list run_live hands the graph, with the same
     comparison, and the cap is [universe_cap] above, which run_live reads too.

     So a book this list passes is a book the engine starts on, as far as the
     BOOK decides it. The one direction this list is STRICTER in: Alerts.validate
     runs here always, while Alerts.attach validates only when the block is
     enabled (lib/alerts.ml), so a wrong but disabled alerts block is refused
     here and started on by the engine. That is deliberate -- the block would
     otherwise fail on the day it is enabled -- and it is the one case where a
     deploy's check-book refuses a book the running engine accepts. A startup
     refusal added elsewhere -- a new rule in
     Graph.create, a sink lib/alerts.ml declines to build -- is one more this
     list does not see until it is restated here; round 1's review found the
     universe's three exactly that way, after an earlier version of this comment
     had claimed there were none. *)
  let errors (t : t) : string list =
    (* [invalid_argf], which is what Limits.validate raises: the message alone,
       because [Error.of_exn] would print it inside its constructor and this list
       is read by a person who has just made a typo. *)
    let raised f =
      match try Ok (f ()) with exn -> Error exn with
      | Ok () -> None
      | Error (Invalid_argument m | Failure m) -> Some m
      | Error exn -> Some (Exn.to_string exn)
    in
    let returned = function Ok () -> None | Error e -> Some (Error.to_string_hum e) in
    (* A rule about the WHOLE set -- two limits with one name, two strategies
       with one name, the live fractions' sum -- cannot be seen by validating one
       member. So each member is validated alone, which collects every
       per-member problem, and a run over the whole set adds the set-level
       sentence. A whole-set run stops at its first problem, and when that is a
       member's it repeats a sentence the parts already hold, which is why a
       whole-set sentence is dropped when it is already listed rather than
       appended blind: the alternative is a second copy of each set rule's
       wording here, free to drift from the one the engine prints.

       Limits.validate checks its set rule before the members, so one run over
       the whole set sees it. Signals_spec.validate checks the duplicate name
       before the members but the live fractions' sum AFTER them, so a member's
       own problem hid the sum for a round (round 1's N1). Hence two runs for
       the signals: over every strategy, for the duplicate name, and over the
       strategies that passed alone, which gets past the members to the sum.
       The sum that second run names is over those strategies -- a lower bound
       on the book's when a failing member is live too. When that bound is
       still over 1 the sentence appears beside the member's own bullet; when
       it is not, the sum goes unreported until the member is fixed, and the
       next run names it in full. *)
    let whole_and_parts ~wholes ~parts =
      List.fold wholes ~init:parts ~f:(fun acc whole ->
          match whole with
          | Some e when not (List.mem acc e ~equal:String.equal) -> acc @ [ e ]
          | Some _ | None -> acc)
    in
    let instruments = instruments t in
    let universe_errors =
      let count = List.length instruments in
      (* The cap before the shape, as run_live refuses before Graph.create is
         reached. *)
      let cap =
        if count > universe_cap then
          Some
            (sprintf
               "universe: the book declares %d names and the free Alpaca plan streams at \
                most %d; remove names, or move to a paid feed and raise \
                Config.universe_cap"
               count universe_cap)
        else None
      in
      let shape =
        if count = 0 then
          Some "universe: no positions; the graph needs at least one instrument"
        else
          match
            List.find_a_dup instruments ~compare:(fun a b ->
                Types.Symbol.compare (Types.Instrument.symbol a)
                  (Types.Instrument.symbol b))
          with
          | Some dup ->
              Some
                (sprintf
                   "universe: instrument %S appears twice; the graph refuses a duplicate \
                    at construction"
                   (Types.Symbol.to_string (Types.Instrument.symbol dup)))
          | None -> None
      in
      List.filter_opt [ cap; shape ]
    in
    let limit_errors =
      let each = limits t in
      whole_and_parts
        ~parts:
          (List.filter_map each ~f:(fun l ->
               raised (fun () -> Limits.validate ~instruments [ l ])))
        ~wholes:[ raised (fun () -> Limits.validate ~instruments each) ]
    in
    let alert_errors = Option.to_list (returned (Alerts.validate t.alerts)) in
    let universe = List.map t.positions ~f:(fun p -> p.Position_spec.symbol) in
    let desk_errors = Option.to_list (returned (Desk_spec.validate ~universe t.desk)) in
    let signal_errors =
      match t.signals with
      | None -> []
      | Some signals ->
          let whole strategies =
            returned (Signals_spec.validate ~universe { Signals_spec.strategies })
          in
          let verdicts =
            List.map signals.Signals_spec.strategies ~f:(fun s -> (s, whole [ s ]))
          in
          let passed =
            List.filter_map verdicts ~f:(fun (s, verdict) ->
                Option.some_if (Option.is_none verdict) s)
          in
          whole_and_parts
            ~parts:(List.filter_map verdicts ~f:snd)
            ~wholes:[ whole signals.Signals_spec.strategies; whole passed ]
    in
    universe_errors @ limit_errors @ alert_errors @ desk_errors @ signal_errors

  let of_string (contents : string) : t Or_error.t =
    let open Or_error.Let_syntax in
    let%bind book = Result.map_error (parse contents) ~f:Error.of_exn in
    let universe = List.map book.positions ~f:(fun p -> p.Position_spec.symbol) in
    let%bind () = Desk_spec.validate ~universe book.desk in
    let%map () =
      match book.signals with
      | None -> Ok ()
      | Some signals -> Signals_spec.validate ~universe signals
    in
    book

  let load (path : string) : t Or_error.t =
    Or_error.tag_arg
      (let open Or_error.Let_syntax in
       let%bind contents = Or_error.try_with (fun () -> In_channel.read_all path) in
       of_string contents)
      "ohcamel: cannot load book file" path String.sexp_of_t
end

(* ------------------------------------------------------------------------ *)
(* What a book says, for someone who has just edited it                      *)
(* ------------------------------------------------------------------------ *)

(* [check-book]'s other half. A checker that only says "no problems" is a
   checker whose reader cannot tell a book it fixed from a book it saved to the
   wrong path, so the summary states what the engine would start on: the
   universe and the sectors it implies, every limit with the scope and threshold
   the graph will read, every strategy with its sizing, and the two switches that
   decide whether this process can act -- the desk's [trading] and alerting's
   kill switch.

   Every string here is rendered by the function the engine itself renders with
   ([Types.Limit.scope_to_string], [Limits.render_value]), so the summary cannot
   describe a limit in words the rest of the system does not use. *)
module Summary = struct
  module Holding = struct
    type t = { symbol : string; sector : string; qty : float }
    [@@deriving sexp_of, compare, equal]
  end

  module Limit_line = struct
    type t = { name : string; scope : string; kind : string; threshold : string }
    [@@deriving sexp_of, compare, equal]
  end

  module Strategy_line = struct
    type t = {
      name : string;
      symbols : string list;
      max_age : int;
      sizing : string;
      capital_fraction : float;
      (* What the desk sizes with at the book's incubation_cap
         (Signals_spec.effective_fraction): the fraction itself unless the
         strategy is incubation and the cap scales the roster down. *)
      effective_fraction : float;
    }
    [@@deriving sexp_of, compare, equal]
  end

  type t = {
    path : string;
    cash : float;
    universe : Holding.t list;
    sectors : string list;
    limits : Limit_line.t list;
    strategies : Strategy_line.t list;
    (* The desk's and alerting's switches as (field, value) pairs in the order a
       reader wants them, rendered here rather than in bin/main.ml so that the
       one caller that prints them and any later caller that reads them cannot
       render them differently. *)
    desk : (string * string) list;
    alerts : (string * string) list;
  }
  [@@deriving sexp_of]

  (* A limit's kind as one word. The same five words graph.ml's topology uses;
     it cannot be borrowed from there, because graph.ml is downstream of this
     module. The Greek case is unreachable from a book -- Book.Limit_spec has no
     Greek constructor, so no file can ask for one -- and is here because the
     kind it matches on is Types.Limit's, which does. *)
  let kind_name : Types.Limit.kind -> string = function
    | Types.Limit.Gross_notional _ -> "gross_notional"
    | Types.Limit.Value_at_risk _ -> "value_at_risk"
    | Types.Limit.Component_var _ -> "component_var"
    | Types.Limit.Max_drawdown _ -> "max_drawdown"
    | Types.Limit.Greek_limit (greek, _) -> "greek:" ^ Types.Greek.to_string greek

  let of_book ~(path : string) (book : Book.t) : t =
    let holdings =
      List.map book.Book.positions ~f:(fun p ->
          {
            Holding.symbol = p.Book.Position_spec.symbol;
            sector = p.Book.Position_spec.sector;
            qty = p.Book.Position_spec.qty;
          })
    in
    let limits =
      List.map book.Book.limits ~f:(fun spec ->
          let limit = Book.Limit_spec.to_limit spec in
          let kind = Types.Limit.kind limit in
          {
            Limit_line.name = Types.Limit.name limit;
            scope = Types.Limit.scope_to_string (Types.Limit.scope limit);
            kind = kind_name kind;
            threshold = Limits.render_value kind (Limits.threshold kind);
          })
    in
    let strategies =
      Option.value_map book.Book.signals ~default:[] ~f:(fun signals ->
          List.map signals.Book.Signals_spec.strategies ~f:(fun s ->
              {
                Strategy_line.name = s.Book.Signals_spec.Strategy.name;
                symbols = s.Book.Signals_spec.Strategy.symbols;
                max_age = s.Book.Signals_spec.Strategy.max_age;
                sizing =
                  Book.Signals_spec.sizing_to_string s.Book.Signals_spec.Strategy.sizing;
                capital_fraction = s.Book.Signals_spec.Strategy.capital_fraction;
                effective_fraction =
                  Book.Signals_spec.effective_fraction
                    ~incubation_cap:book.Book.desk.Book.Desk_spec.incubation_cap signals s;
              }))
    in
    let desk = book.Book.desk in
    let alerts = book.Book.alerts in
    {
      path;
      cash = book.Book.cash;
      universe = holdings;
      sectors =
        List.dedup_and_sort
          (List.map holdings ~f:(fun h -> h.Holding.sector))
          ~compare:String.compare;
      limits;
      strategies;
      desk =
        [
          ( "trading",
            match desk.Book.Desk_spec.trading with
            | Book.Desk_spec.Enabled -> "enabled"
            | Book.Desk_spec.Disabled -> "disabled" );
          ("max_order_notional", sprintf "$%.2f" desk.Book.Desk_spec.max_order_notional);
          ( "max_adv_participation",
            sprintf "%.2f%%" (desk.Book.Desk_spec.max_adv_participation *. 100.0) );
          ("price_collar", sprintf "%.2f%%" (desk.Book.Desk_spec.price_collar *. 100.0));
          ("duplicate_window_s", sprintf "%g" desk.Book.Desk_spec.duplicate_window_s);
          ("max_open_orders", Int.to_string desk.Book.Desk_spec.max_open_orders);
          ("spread_bps_default", sprintf "%g" desk.Book.Desk_spec.spread_bps_default);
          ( "spread_bps",
            if List.is_empty desk.Book.Desk_spec.spread_bps then "(none)"
            else
              String.concat ~sep:" "
                (List.map desk.Book.Desk_spec.spread_bps ~f:(fun (sym, bps) ->
                     sprintf "%s=%g" sym bps)) );
        ];
      alerts =
        [
          ("enabled", Bool.to_string alerts.Alerts.enabled);
          ( "sinks",
            String.concat ~sep:" "
              (List.map alerts.Alerts.sinks ~f:(fun sink ->
                   Sexp.to_string (Alerts.Sink.sexp_of_t sink))) );
          ("clear_below", sprintf "%g" alerts.Alerts.clear_below);
          ( "kill_switch",
            if alerts.Alerts.kill_switch_enabled then "enabled" else "disabled" );
          ( "kill_switch_trips_on",
            if List.is_empty alerts.Alerts.kill_switch_trips_on then "(none)"
            else String.concat ~sep:" " alerts.Alerts.kill_switch_trips_on );
        ];
    }

  (* The printed form. One function, so that the page deploy.sh's log shows and
     the page the owner reads after an edit are the same page. *)
  let lines (t : t) : string list =
    let section name = [ ""; name ] in
    let pairs = List.map ~f:(fun (field, value) -> sprintf "  %-22s %s" field value) in
    [ sprintf "book %s" t.path; sprintf "  %-22s $%.2f" "cash" t.cash ]
    @ section
        (sprintf "universe (%d names, %d sectors: %s)" (List.length t.universe)
           (List.length t.sectors)
           (String.concat ~sep:" " t.sectors))
    @ List.map t.universe ~f:(fun h ->
        sprintf "  %-8s %-14s %g" h.Holding.symbol h.Holding.sector h.Holding.qty)
    @ section (sprintf "limits (%d)" (List.length t.limits))
    @ List.map t.limits ~f:(fun l ->
        sprintf "  %-14s %-18s %-16s %s" l.Limit_line.name l.Limit_line.scope
          l.Limit_line.kind l.Limit_line.threshold)
    @ section "desk" @ pairs t.desk @ section "alerts" @ pairs t.alerts
    @ section
        (if List.is_empty t.strategies then
           "strategies (none: the desk registers none and reads no signal file)"
         else sprintf "strategies (%d)" (List.length t.strategies))
    @ List.map t.strategies ~f:(fun s ->
        sprintf "  %-16s %-10s max_age %d  capital_fraction %g  effective %g  [%s]"
          s.Strategy_line.name s.Strategy_line.sizing s.Strategy_line.max_age
          s.Strategy_line.capital_fraction s.Strategy_line.effective_fraction
          (String.concat ~sep:" " s.Strategy_line.symbols))
end

type summary = Summary.t

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
    (* The origin of the other host, when there is one. Set only on the live
         container: /ops on the live origin fills its second column from the
         public demo engine, which is possible only in that direction, because
         the demo engine publishes a CORS header on its JSON in demo mode and
         the live one publishes none and stays behind the password. None here
         is not a failure -- it is the demo host, and its /ops says where to
         look for both. *)
    peer_origin : string option;
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
      peer_origin = None;
    }

  (* Split out of [of_env] so the "empty means absent" rule can be tested
     without a test that mutates the process environment. Same rule as
     Credentials.required, and for the same reason: `export FOO=` is how
     people end up without a value. *)
  let peer_origin_of (raw : string option) : string option =
    match raw with
    | Some v when not (String.is_empty (String.strip v)) -> Some (String.strip v)
    | Some _ | None -> None

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
      peer_origin = peer_origin_of (Sys.getenv "OHCAMEL_PEER_ORIGIN");
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

(* One book, every validation, and no credentials.

   The error side is a LIST, and that is the whole design. deploy.sh runs this
   before it restarts the engine (the plan's Task 16) and the owner runs it after
   every edit, so the reader is always someone who has just changed the file and
   wants to know everything that is wrong with it -- not the first thing.

   No credential is read, so this runs in a container with nothing mounted and on
   a laptop with nothing exported: a book is checkable on any machine that has
   the binary. *)
let check (path : string) : (summary, string list) Result.t =
  (* One problem is one line. The caller prints these as a bulleted list and its
     reader counts the bullets, so a sentence that arrived with a newline in it --
     a sexp error pretty-printed across three lines is the one that does -- would
     read as three problems. *)
  let one_line s =
    String.split_lines s
    |> List.filter_map ~f:(fun l ->
        let l = String.strip l in
        if String.is_empty l then None else Some l)
    |> String.concat ~sep:" "
  in
  let refuse sentences = Error (List.map sentences ~f:one_line) in
  (* A file that did not parse has no book to validate, so there is exactly one
     thing to report. Both exceptions are unwrapped rather than printed through
     [Error.of_exn], which would render them as their own constructors: the reader
     of this list has just made a typo and wants the sentence, not the sexp of the
     exception that carried it. *)
  match try Ok (In_channel.read_all path) with exn -> Error exn with
  | Error (Sys_error message) -> refuse [ sprintf "cannot read the book: %s" message ]
  | Error exn -> refuse [ sprintf "cannot read %s: %s" path (Exn.to_string exn) ]
  | Ok contents -> (
      match Book.parse contents with
      | Error (Sexp.Of_sexp_error (inner, offending)) ->
          refuse
            [
              sprintf "%s does not parse: %s, at %s" path
                (match inner with
                | Invalid_argument m | Failure m -> m
                | exn -> Exn.to_string exn)
                (Sexp.to_string offending);
            ]
      | Error (Failure message) -> refuse [ sprintf "%s does not parse: %s" path message ]
      | Error exn -> refuse [ sprintf "%s does not parse: %s" path (Exn.to_string exn) ]
      | Ok book -> (
          match Book.errors book with
          | [] -> Ok (Summary.of_book ~path book)
          | problems -> refuse problems))

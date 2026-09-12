(* Identifiers the desk mints.

   A client order id is the one name an order has before the venue has heard
   of it, and invariant 10 leans on it entirely: after a submission whose
   outcome is unknown, the only honest question to ask the venue is "do you
   have THIS id", and that question needs an id minted here, written to the
   journal, and sent with the order.

   ULIDs rather than random UUIDs. The first 48 bits are milliseconds since
   the epoch, so ids sort in the order they were minted -- the journal's
   orders table reads in time order by its primary key -- and two orders in
   the same millisecond are still distinct by 80 random bits. Crockford's
   base32 has no I, L, O or U, so an id read aloud or copied out of a log
   cannot be misread.

   The "ohc-" prefix marks an order THIS desk created. The paper account can
   also hold orders placed by hand in Alpaca's own interface, and the kill
   switch cancels only orders carrying the prefix: cancelling an order someone
   placed deliberately elsewhere is not the switch's decision to make. *)

open Core

module Ulid = struct
  let alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

  (* [symbols] symbols from the low bits of a non-negative int, most
     significant first. An OCaml int has 63 bits, so the 48-bit timestamp fits
     with room to spare, and so does each 40-bit half of the randomness. *)
  let encode_int (x : int) ~(symbols : int) : string =
    let b = Bytes.create symbols in
    let x = ref x in
    for i = symbols - 1 downto 0 do
      Bytes.set b i alphabet.[!x land 31];
      x := !x lsr 5
    done;
    Bytes.to_string b

  let max_ms = (1 lsl 48) - 1

  let encode_time ~(ms : int) : string =
    if ms < 0 || ms > max_ms then invalid_argf "ulid: %d ms does not fit 48 bits" ms ();
    encode_int ms ~symbols:10

  (* Eighty bits do not fit an int, so the ten bytes are read as two 40-bit
     halves, each exactly eight symbols: 40 is a multiple of 5, which is the
     whole reason the split is at the fifth byte. *)
  let encode_randomness (bytes : string) : string =
    if String.length bytes <> 10 then
      invalid_argf "ulid: randomness must be 10 bytes, got %d" (String.length bytes) ();
    let half pos =
      let acc = ref 0 in
      for i = pos to pos + 4 do
        acc := (!acc lsl 8) lor Char.to_int bytes.[i]
      done;
      !acc
    in
    encode_int (half 0) ~symbols:8 ^ encode_int (half 5) ~symbols:8

  let encode ~(ms : int) ~(randomness : string) : string =
    encode_time ~ms ^ encode_randomness randomness

  let generate ~(now : Time_ns.t) ~(rng : Random.State.t) : string =
    let ms = Time_ns.to_int_ns_since_epoch now / 1_000_000 in
    encode ~ms
      ~randomness:
        (String.init 10 ~f:(fun _ -> Char.of_int_exn (Random.State.int rng 256)))

  (* 26 symbols from the alphabet, the first at most 7: a first symbol of 8 or
     more would need a 49th bit of timestamp. *)
  let is_valid (s : string) : bool =
    String.length s = 26
    && Char.( <= ) s.[0] '7'
    && String.for_all s ~f:(fun c -> String.mem alphabet c)
end

module Client_order_id : sig
  type t [@@deriving sexp, compare, equal]

  include Comparable.S with type t := t

  val prefix : string
  val generate : now:Time_ns.t -> rng:Random.State.t -> t
  val of_string : string -> t option
  val to_string : t -> string
  val is_ours : string -> bool
end = struct
  module T = struct
    type t = string [@@deriving sexp, compare, equal]
  end

  include T
  include Comparable.Make (T)

  let prefix = "ohc-"
  let generate ~now ~rng = prefix ^ Ulid.generate ~now ~rng

  let of_string (s : string) : t option =
    match String.chop_prefix s ~prefix with
    | Some rest when Ulid.is_valid rest -> Some s
    | _ -> None

  let to_string = Fn.id
  let is_ours (raw : string) : bool = Option.is_some (of_string raw)
end

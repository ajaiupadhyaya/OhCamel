(* The identifiers the desk mints.

   The timestamp reference is the ULID specification's own example
   (github.com/ulid/spec): 1469918176385 ms encodes to 01ARYZ6S41. Every other
   expected string below is derived by hand in the comment beside it, five
   bits at a time, against Crockford's alphabet:

     0-9 -> '0'-'9', 10 A, 11 B, 12 C, 13 D, 14 E, 15 F, 16 G, 17 H, 18 J,
     19 K, 20 M, 21 N, 22 P, 23 Q, 24 R, 25 S, 26 T, 27 V, 28 W, 29 X, 30 Y, 31 Z *)

open Core
module Ids = Ohcamel_desk.Ids

let test_the_specification's_example () =
  Alcotest.(check string)
    "1469918176385 ms is 01ARYZ6S41, as the ULID specification prints it" "01ARYZ6S41"
    (Ids.Ulid.encode_time ~ms:1469918176385)

let test_the_edges_of_the_timestamp () =
  (* Zero is ten zero symbols. 2^48 - 1 is forty-eight ones in a fifty-bit
     field: the first symbol holds the top five bits, 00111 = 7, and the other
     nine are all ones, Z. *)
  Alcotest.(check string) "0 ms" "0000000000" (Ids.Ulid.encode_time ~ms:0);
  Alcotest.(check string)
    "2^48 - 1 ms" "7ZZZZZZZZZ"
    (Ids.Ulid.encode_time ~ms:((1 lsl 48) - 1))

let test_the_random_half_bit_by_bit () =
  (* Eighty ones are sixteen Z. Seventy-nine zeros and a one are fifteen 0 and
     a 1. And 01 23 45 67 89 | ab cd ef 01 23, each half forty bits in eight
     five-bit groups:
       0x0123456789 = 00000 00100 10001 10100 01010 11001 11100 01001
                    =   0     4    17    20    10    25    28     9  = 04HMASW9
       0xabcdef0123 = 10101 01111 00110 11110 11110 00000 01001 00011
                    =  21    15     6    30    30     0     9     3  = NF6YY093 *)
  Alcotest.(check string)
    "ten 0xff bytes" "ZZZZZZZZZZZZZZZZ"
    (Ids.Ulid.encode_randomness (String.make 10 '\xff'));
  Alcotest.(check string)
    "nine zero bytes and a one" "0000000000000001"
    (Ids.Ulid.encode_randomness (String.make 9 '\x00' ^ "\x01"));
  Alcotest.(check string)
    "0123456789abcdef0123" "04HMASW9NF6YY093"
    (Ids.Ulid.encode_randomness "\x01\x23\x45\x67\x89\xab\xcd\xef\x01\x23")

let test_ids_sort_in_the_order_they_were_minted () =
  (* The timestamp is the prefix, so one millisecond later sorts later even
     against the largest possible randomness minted a millisecond earlier. *)
  let ms = 1_789_223_400_000 in
  let earlier = Ids.Ulid.encode ~ms ~randomness:(String.make 10 '\xff') in
  let later = Ids.Ulid.encode ~ms:(ms + 1) ~randomness:(String.make 10 '\x00') in
  Alcotest.(check bool) "later sorts after earlier" true String.(earlier < later)

let test_a_client_order_id_is_the_prefix_and_a_ulid_and_nothing_else () =
  (* 2026-09-12T14:30:00Z is 1,789,223,400,000 ms since the epoch, whose ten
     symbols are 01M2B0CWJ0. *)
  let now = Time_ns.of_string_with_utc_offset "2026-09-12T14:30:00Z" in
  let id = Ids.Client_order_id.generate ~now ~rng:(Random.State.make [| 7 |]) in
  let s = Ids.Client_order_id.to_string id in
  Alcotest.(check string)
    "the prefix and the timestamp" "ohc-01M2B0CWJ0" (String.prefix s 14);
  Alcotest.(check int) "four and twenty-six characters" 30 (String.length s);
  Alcotest.(check bool)
    "it parses back" true
    (Option.is_some (Ids.Client_order_id.of_string s));
  List.iter
    [
      ("one character short", "ohc-01ARYZ6S41TSV4RRFFQ69G5FA");
      ("I is not in the alphabet", "ohc-01ARYZ6S41TSV4RRFFQ69G5FAI");
      ("another desk's prefix", "abc-01ARYZ6S41TSV4RRFFQ69G5FAV");
      ("a first symbol above 7 is past 48 bits", "ohc-81ARYZ6S41TSV4RRFFQ69G5FAV");
    ]
    ~f:(fun (why, raw) ->
      Alcotest.(check bool) why false (Option.is_some (Ids.Client_order_id.of_string raw)));
  Alcotest.(check bool) "ours" true (Ids.Client_order_id.is_ours s);
  Alcotest.(check bool)
    "an id Alpaca's own interface minted is not ours" false
    (Ids.Client_order_id.is_ours "4642fd68-d59a-47d7-a9ac-e22f536828d1")

let suite =
  ( "desk_ids",
    [
      Alcotest.test_case "the timestamp half is the ULID specification's example" `Quick
        test_the_specification's_example;
      Alcotest.test_case "the edges of the timestamp" `Quick
        test_the_edges_of_the_timestamp;
      Alcotest.test_case "the random half, bit by bit" `Quick
        test_the_random_half_bit_by_bit;
      Alcotest.test_case "ids sort in the order they were minted" `Quick
        test_ids_sort_in_the_order_they_were_minted;
      Alcotest.test_case "a client order id is the prefix and a ULID, and nothing else"
        `Quick test_a_client_order_id_is_the_prefix_and_a_ulid_and_nothing_else;
    ] )

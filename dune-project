(lang dune 3.16)

(name ohcamel)

(generate_opam_files true)

(source
 (github ajaiupadhyaya/OhCamel))

(authors "Ajai Upadhyaya")

(maintainers "Ajai Upadhyaya")

(license MIT)

(package
 (name ohcamel)
 (synopsis "Reactive real-time risk and limits engine")
 (description
  "A risk and limits engine built on self-adjusting computation. Risk quantities \
   (exposure, VaR, expected shortfall, limit breaches) are nodes in an Incremental \
   dependency graph: when a tick or fill changes one input, only the nodes downstream \
   of that change recompute. There is no polling loop.")
 (depends
  (ocaml
   (>= 5.1.0))
  (core
   (>= v0.17.0))
  (async
   (>= v0.17.0))
  (incremental
   (>= v0.17.0))
  (owl
   (>= 1.2))
  ; Upper bound inherited from websocket-async 2.17, which pins
  ; cohttp-async < 6.0.0. Phase 0 installed 6.2.2; Phase 2 downgrades to 5.3.x
  ; to get a tested RFC 6455 client. Nothing was written against cohttp in
  ; between, so the downgrade cost nothing. See the README for the full note.
  (cohttp-async
   (and
    (>= 5.3.0)
    (< 6.0.0)))
  ; The Alpaca market data feed. Async-native, so it shares one scheduler with
  ; the rest of the system rather than bridging two concurrency runtimes.
  (websocket-async
   (>= 2.17))
  ; TLS for wss:// and for FRED over https. Not a free choice: conduit-async
  ; carries async_ssl as its only TLS depopt, so tls-async (pure OCaml) would
  ; not be picked up by cohttp-async. Needs system libffi and OpenSSL.
  (async_ssl
   (>= v0.17.0))
  ; Wire formats. Both Alpaca and FRED speak JSON.
  yojson
  (ppx_yojson_conv
   (>= v0.17.0))
  (alcotest
   (and
    :with-test
    (>= 1.9.0)))))

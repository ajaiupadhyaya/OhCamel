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
  (cohttp-async
   (>= 6.2.0))
  (alcotest
   (and
    :with-test
    (>= 1.9.0)))))

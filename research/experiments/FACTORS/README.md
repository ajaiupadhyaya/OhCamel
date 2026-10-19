# The factor trial ledger

`trials.jsonl` is append-only. `make factor` writes one line per evaluation, keyed by the expression's canonical
hash, with the hypothesis written before it ran. The number of DISTINCT hashes is the `n_trials` that any
Deflated Sharpe on a factor promoted from here must use. A failed or abandoned trial still counts.

The droplet's weekly `factors.screen` job re-scores every distinct expression in this file on the warehouse
universe (exploration window only, to 2023-12-31) and publishes the result. It never adds a line and never
proposes an expression. Only the owner does that, with a hypothesis.

The file starts empty on purpose.

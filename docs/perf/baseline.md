# G-RT baseline: the live engine's stabilize latency

The compute program's Global Constraint 4
(`docs/superpowers/plans/2026-09-24-quant-compute-program.md`): the live
engine's stabilize latency p99 may not regress more than 10 % against this
baseline while the batch tier runs at full load. `tools/perf/gate_rt.py` is
the check; this file holds what it is checked against.

## What is measured

The engine records the duration of every stabilize of its served graph that
recomputed at least one node (`Graph.on_stabilize`; the tick handlers'
stabilizes above all) in a ring of the last 4096, and `/api/ops` reports
their nearest-rank percentiles as `stabilize_ms` (`n`, `p50`, `p99`, `max`,
`window`), in milliseconds. A stabilize with nothing dirty is not counted.

## How a row is made

On the droplet, during a session (ticks arriving), after Task 0.3's resource
budget is deployed (owner step O-3):

```bash
python3 tools/perf/gate_rt.py --engine <engine URL> --baseline docs/perf/baseline.md
```

`OHCAMEL_GATE_AUTH=user:password` in the environment if the URL is the live
host behind its password. The script samples five minutes at rest, starts
`tools/perf/load.py` in `ohcamel-batch.slice` at the batch tier's weight and
cap, samples five minutes under that load, and prints one row for the table
below. The newest row's rest p99 is the baseline every later run is compared
against; with no row yet, a run compares against its own rest p99.

## Rows

No row yet: the first is the owner's O-3 run on the droplet. Nothing here is
estimated; a row is a measurement or it is absent.

| date | sha | rest p99 ms | load p99 ms | change | verdict |
| --- | --- | --- | --- | --- | --- |

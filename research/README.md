# ohcamel-research — the research layer

Python. Replays committed real bars, emits signals in the contract's shape,
and runs strategies through `fdq`'s validation battery. See the repository
[README](../README.md) ("Signals and research") and
[`../docs/CHARTER.md`](../docs/CHARTER.md) for the charter's gates, and
[`../interface/README.md`](../interface/README.md) for the signal contract
this layer writes to.

## Installing and testing

```
cd research
uv sync --locked --extra dev
uv run pytest                 # hermetic, offline, seeded -- no network, no credentials
uv run ruff check
```

From the repository root, `make research-test` does the same and also runs
`fixtures doctor` (below) against both committed fixture directories; `make
research-reproduce` re-derives every committed battery manifest from HEAD's
`research/`, `fixtures/` and lockfile, on a temporary copy, and diffs the
result against what is checked in.

## The `ohcamel-research` CLI

Installed as a console script (`[project.scripts]` in `pyproject.toml`); run
it as `uv run ohcamel-research <command>` from `research/`, or
`ohcamel-research` directly once the project is installed.

- **`replay [--fixtures DIR] [--start DATE] [--end DATE] [--symbols A,B]`** —
  stream the committed real bars as JSON lines. Refuses a fixtures directory
  whose files lack a provenance sidecar.
- **`fixtures doctor [--fixtures DIR]`** — print every fixture's provenance
  (source, date span, whether it is synthetic) and exit non-zero if any
  fixture lacks one.
- **`signal emit --strategy SLUG --fdq-strategy RULE --as-of DATE --sequence
  N --out PATH [--params JSON] [--fixtures DIR] [--validation-from
  MANIFEST]`** — run an `fdq` strategy point-in-time at `--as-of` (via
  `ohcamel_research.signal.emit`, the same call the live service makes, so
  neither one can drift from the other's copy of the rule) and write one
  signal document in the contract's shape. `SLUG` is the document's strategy
  name, registered with the desk and distinct per symbol; `RULE` is which
  `fdq` rule computes the weights and may be shared by several slugs.
  `--validation-from` is the only way to get a `validation.status` other
  than `unvalidated`: it is built from a battery manifest, and counts only
  when the manifest's slug, strategy and `selected_params` match this
  invocation's exactly.
- **`signal check PATH`** — validate one signal document against the schema
  and R7.
- **`battery run EXPERIMENT_DIR`** — run a pre-registered experiment (its
  `config.yaml` names the bars, friction file, macro series, windows and
  seed) and write one manifest per strategy under that directory. Refuses to
  start on an uncommitted battery.
- **`contract check [--core PATH] [--fixtures DIR]`** — run
  `interface/examples/` through the schema, and through the built OCaml core
  binary if `--core` is given, checking both sides against
  `interface/examples/expected.json`.

## The live service

`src/ohcamel_research/service.py` is what Docker Compose's
`ohcamel-research` container (the `live` profile only) runs. Once a trading
day, from 19:15 America/New_York once that day's bar can be fetched, it
calls `signal.emit` directly for its weight -- never a second implementation
of the strategy's rule -- and writes it for every registered strategy.
`service.yaml` configures it, validated the same way `battery/config.py`
validates an experiment's `config.yaml`: an unknown or missing key is
refused, not ignored.

## Layout

- `src/ohcamel_research/` — the package: `replay.py`, `signal.py`,
  `contract.py`, `manifest.py`, `service.py`, `battery/` (the validation
  battery: config, data provenance, the pre-registered measures, PBO), and
  `cli.py` above.
- `experiments/` — one directory per pre-registered experiment. `EXP-A01/`
  holds its `config.yaml`, `hypothesis.md`, the committed
  `manifest.<slug>.json` files and `report.md`.
- `config/` — shared configuration such as `friction_v1.yaml`.
- `tests/` — this project's own test suite (see *Installing and testing*).

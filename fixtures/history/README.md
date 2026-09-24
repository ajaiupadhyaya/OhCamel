# The ten-year history, the VIX series and the friction file

Real market data, not synthetic, with provenance. Nothing here was fetched by
this repository: every file was already on disk in `capitallimits`
(`~/Documents/capitallimits/data/raw/`), fetched there by `fdq`'s Alpaca and
FRED clients, and is copied here byte-for-byte. Copying is a repository
operation only; no network request was made to produce this directory.

## Where each file came from

| File | Source | Fetched at (UTC) | Window |
|---|---|---|---|
| `GLD.parquet` | Alpaca (consolidated/SIP daily bars) | 2026-06-09T17:53:12.884909+00:00 | 2016-06-01 .. 2026-06-01 |
| `IEF.parquet` | Alpaca | 2026-06-09T17:53:12.491199+00:00 | 2016-06-01 .. 2026-06-01 |
| `IWM.parquet` | Alpaca | 2026-06-09T17:53:11.625637+00:00 | 2016-06-01 .. 2026-06-01 |
| `QQQ.parquet` | Alpaca | 2026-06-09T17:53:10.839491+00:00 | 2016-06-01 .. 2026-06-01 |
| `SPY.parquet` | Alpaca | 2026-06-09T17:53:10.423796+00:00 | 2016-06-01 .. 2026-06-01 |
| `TLT.parquet` | Alpaca | 2026-06-09T17:53:12.136908+00:00 | 2016-06-01 .. 2026-06-01 |
| `XLE.parquet` | Alpaca | 2026-06-09T17:53:13.424699+00:00 | 2016-06-01 .. 2026-06-01 |
| `XLF.parquet` | Alpaca | 2026-06-09T17:53:13.931487+00:00 | 2016-06-01 .. 2026-06-01 |
| `XLK.parquet` | Alpaca | 2026-06-09T17:53:14.416327+00:00 | 2016-06-01 .. 2026-06-01 |
| `../macro/macro.parquet` | FRED (`vix`, `dgs10`, `dgs2`) | 2026-06-09T17:53:15.907436+00:00 | 2016-06-01 .. 2026-06-01 |

Each `.parquet` has a sibling `.parquet.meta.json` sidecar (`fdq`'s
provenance format) copied alongside it, unmodified. `macro.parquet` lives in
`../macro/` — a directory of its own — because `ohcamel_research.replay.load_bars`
treats every `*.parquet` under its fixtures directory as one symbol's OHLCV
bars; a macro series in the same directory as the nine ETFs would be read as
a tenth, malformed "symbol".

The friction file, `../../research/config/friction_v1.yaml`, is not data on
disk in `capitallimits` — it is vendored from `fdq`'s source tree at the
pinned commit `652474c02b4919ed9842ec2d9510c6327b92f874` (the commit this
project's `fdq` dependency is pinned to), via:

```
git -C ~/Documents/capitallimits show 652474c:config/friction_v1.yaml
```

because that file is not shipped in `fdq`'s built wheel.

## SHA-256 and row counts

Row count is the number of daily bars (`len(df)` after `pd.read_parquet`).

| File | Rows | SHA-256 |
|---|---:|---|
| `GLD.parquet` | 2514 | `31beca4f61e43af3c42bf465cd2186e722c1a9fc2617c7b7103a982be9c223e1` |
| `IEF.parquet` | 2514 | `0bf9a7a3d1d25b1ec974a7cb222f59fa72470636d12211d09800edc3f61958ac` |
| `IWM.parquet` | 2514 | `70aa6d6e720e177470f95a534d9d5b452b4f63667de959079cb315dae33285b9` |
| `QQQ.parquet` | 2514 | `5d78eb20bf6e3a73ebeffd8186d469491c756b53f3c034f4fadaee991d8426df` |
| `SPY.parquet` | 2514 | `e990ccc2a544be237c848d16266d98a55f9331a808e1c4f1a4dbab1b5e08ccdf` |
| `TLT.parquet` | 2514 | `d749ba34cfb00152e8b0ed1edd02ef0f3e6d44897a6fe238e577593daa38c7f2` |
| `XLE.parquet` | 2514 | `31dc0650e488cd494033cdc54d290f99bffd8f9606e46f550bc309e4352dbae1` |
| `XLF.parquet` | 2514 | `6103519a7d0d0ca8fb48e3f040ebdc413398267af38b1be48b8fd289c68ba9e3` |
| `XLK.parquet` | 2514 | `1d13c4cb9db16c7871ea5c8e9e98d6d71318b642687ff7f61b9d1e20af8bc7cf` |
| `GLD.parquet.meta.json` | -- | `f8ca1e5c5eddfa21995174618ae3a7497285cad2cbd4cad01b7456c6daeedaac` |
| `IEF.parquet.meta.json` | -- | `faaf6066db6513be75e821a5139234c81bf5232b9db31a43ec0f5bd6650b96cd` |
| `IWM.parquet.meta.json` | -- | `e0e5f8d389ee6e34f7f60ba5446d0c84eba44600f29a600533d23acbed514b55` |
| `QQQ.parquet.meta.json` | -- | `93c91ac6d6f6c30a0278783c1e9a1f8bf5db07e34b45ae4ce1ab6c768a7de081` |
| `SPY.parquet.meta.json` | -- | `0703279315e94244e94ad40196acf2aaf226a5385558e757986d5998aea120e5` |
| `TLT.parquet.meta.json` | -- | `e9bf893212b42024a2c59506cf6b2ab0151aaa3f25f0d06796cf814b80c9436c` |
| `XLE.parquet.meta.json` | -- | `d1ea128b1ec9bc9d8aa34a4d5ea3922c12b4748af155efa66aa73a63939b9c2e` |
| `XLF.parquet.meta.json` | -- | `b37927cb7efec812d9346ff1cad65a144418a5ee0e695f78ee965b72f5dc782c` |
| `XLK.parquet.meta.json` | -- | `94dae1a4ba97150d4f396639ac0526de4d2bd88621ba2d948a2ef141f39c58d6` |
| `../macro/macro.parquet` | 2609 | `4b05a2b74d89c6f987754ea9112aa9f69fad96d93feab18590f0753c78416cf0` |
| `../macro/macro.parquet.meta.json` | -- | `f8d591f35fd7346bf5578a1f784017edf9a155e69495d32e9034c8a37a1c40d9` |
| `../../research/config/friction_v1.yaml` (at `652474c`) | -- (32 lines) | `1c09f884a55487ca7bc228efc285b69a865d1df59ee4ea367d9b4c09dc1d4cc9` |

`macro.parquet` has more rows (2609) than the ETFs (2514) because it is a
FRED series (`vix`, `dgs10`, `dgs2`) on FRED's own calendar, not the equity
trading calendar the ETFs share.

## What these bars are

- **Consolidated (SIP) volumes**, not IEX-only. Turnover and liquidity checks
  against these bars are checks against the tape, not against one venue's
  slice of it.
- **Unadjusted**: `close_adj == close` on every row of every ETF file
  (verified below). No split or dividend adjustment has been applied.
- **Distributions excluded**: because the closes are unadjusted, a
  buy-and-hold return computed from `close` alone misses the distributions
  the ETF actually paid — about 3% a year on TLT (ruling 1c). Any Sharpe or
  return number computed from these bars understates the true total return
  by that amount, and is biased *against* the strategy under test, not for
  it. EXP-A01's pre-registration states this before its run, not after.
- **`macro.parquet`** carries `vix`, `dgs10` and `dgs2` from FRED — no OHLCV
  columns, no `close_adj` — which is the other reason it is kept out of
  `fixtures/history/`: it does not have the shape `load_bars` expects of a
  symbol.

## Assertions (see Task 8 report for the run that produced these)

- Every sidecar (all 10: nine ETFs plus macro) reads `"synthetic": false`.
- Every ETF parquet file's first and last index dates equal its sidecar's
  `start`/`end` window (`2016-06-01` .. `2026-06-01`), and so does
  `macro.parquet`'s.
- The nine ETFs jointly cover `2016-06-01` → `2026-06-01`: the minimum of all
  nine files' first dates is `2016-06-01` and the maximum of all nine files'
  last dates is `2026-06-01`.
- On every ETF file, `close_adj` equals `close` on every row (confirms
  "unadjusted" above).

## Refreshing this data

A refresh means going back to `capitallimits`, re-fetching, and re-copying —
this repository has no fetch path of its own and reads no Alpaca or FRED
keys. **A refresh is a new data version.** Every manifest that hashes these
fixture files (Task 10's content-hash staleness check) was built against the
SHA-256 values above; replacing any file here changes that hash, and every
manifest built on the old version becomes stale rather than silently wrong.
Re-run the affected battery and regenerate manifests after any refresh —
do not hand-edit a manifest to match new data.

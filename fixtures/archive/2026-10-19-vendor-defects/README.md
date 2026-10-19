# Vendor defects repaired in fixtures/history (archived 2026-10-19)

These are the files `fixtures/history/XLE.parquet`, `XLK.parquet` and `SPY.parquet` held until 2026-10-19, byte for
byte, with their sidecars. They are kept, not deleted, because committed results were computed on them:
EXP-K01's v1 report (`research/experiments/EXP-K01/report.md` at commit `0eb1402`).

**The defect.** Both funds split 2-for-1 with ex-date 2025-12-05 (Alpaca corporate actions: forward_split
1 -> 2, XLE cusip 81369Y506, XLK cusip 81369Y803). The fixtures were copied from fdq's raw Alpaca cache, whose
`close_adj` equals `close` throughout, so each file showed a one-day "return" of about -50% on 2025-12-05.

**The fix in `fixtures/history/`.** On every session before 2025-12-05, open/high/low/close/close_adj are divided
by 2 and volume is multiplied by 2. Nothing else changed, and each sidecar records the adjustment under
`adjustments`.

**SPY.** One decimal-shift print: the 2026-02-02 low was 69.005 on a 695.41 close. It is replaced by
min(open, close) = 689.58, the rule `quant/src/ohcamel_quant/data/fixtures.py:_repair` already applied at load
time. That repair, and its split rule, stay in place as a guard, and the files no longer need them.

**What else is known and NOT changed.** `close_adj` in these fixtures is not adjusted for dividends or
distributions in any file (it equals `close`). The largest consequence is XLF on 2016-09-19 (-18%: the
distribution that created XLRE). That is out of EXP-K01's window, and it is recorded here rather than silently
repaired.

This directory is outside `fixtures/history/`, so the replay feed and the fixture doctor never read it.

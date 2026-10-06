"""The G-RT gate's arithmetic (tools/perf/gate_rt.py), stdlib unittest only.

Run by deploy/test/gate_rt_test.sh, which the lint job picks up with every
other deploy/test/*.sh. Nothing here reaches an engine or Docker: these are
the pure functions the gate's verdict rests on.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gate_rt


class PhaseP99(unittest.TestCase):
    def test_median_of_the_sampled_p99s(self):
        # Sampled p99s 1.0, 3.0, 2.0 -> sorted 1, 2, 3 -> median 2.0.
        samples = [{"n": 10, "p99": 1.0}, {"n": 20, "p99": 3.0}, {"n": 30, "p99": 2.0}]
        self.assertEqual(gate_rt.phase_p99(samples), 2.0)

    def test_even_count_is_the_mean_of_the_middle_two(self):
        # 1, 2, 4, 8 -> (2 + 4) / 2 = 3.0.
        samples = [{"n": 1, "p99": x} for x in (8.0, 1.0, 4.0, 2.0)]
        self.assertEqual(gate_rt.phase_p99(samples), 3.0)

    def test_null_samples_are_skipped_and_none_left_is_none(self):
        self.assertEqual(gate_rt.phase_p99([{"n": 0, "p99": None}, {"n": 5, "p99": 4.0}]), 4.0)
        self.assertIsNone(gate_rt.phase_p99([{"n": 0, "p99": None}]))
        self.assertIsNone(gate_rt.phase_p99([]))


class Baseline(unittest.TestCase):
    TABLE = """# Baseline

| date | sha | rest p99 ms | load p99 ms | change | verdict |
| --- | --- | --- | --- | --- | --- |
| 2026-10-07 | abc1234 | 0.80 | 0.84 | +5.0% | pass |
| 2026-10-09 | def5678 | 0.90 | 0.93 | +3.3% | pass |
"""

    def test_the_newest_rows_rest_p99(self):
        self.assertEqual(gate_rt.parse_baseline(self.TABLE), 0.90)

    def test_no_row_is_no_baseline(self):
        self.assertIsNone(gate_rt.parse_baseline("# Baseline\n\nNot yet measured.\n"))
        header_only = "| date | sha | rest p99 ms | load p99 ms | change | verdict |\n| --- | --- | --- | --- | --- | --- |\n"
        self.assertIsNone(gate_rt.parse_baseline(header_only))


class Verdict(unittest.TestCase):
    def test_ten_percent_is_the_line(self):
        # 1.00 -> 1.10 is exactly +10%: allowed ("more than 10 %" fails).
        self.assertEqual(gate_rt.verdict(1.00, 1.10, 0.10), (0.10, True))
        # 1.00 -> 1.11 is +11%: fails.
        change, ok = gate_rt.verdict(1.00, 1.11, 0.10)
        self.assertAlmostEqual(change, 0.11)
        self.assertFalse(ok)
        # Faster under load is a pass with a negative change.
        self.assertEqual(gate_rt.verdict(2.0, 1.5, 0.10), (-0.25, True))

    def test_an_unmeasured_side_fails(self):
        self.assertEqual(gate_rt.verdict(None, 1.0, 0.10), (None, False))
        self.assertEqual(gate_rt.verdict(1.0, None, 0.10), (None, False))
        self.assertEqual(gate_rt.verdict(0.0, 1.0, 0.10), (None, False))


class Row(unittest.TestCase):
    def test_markdown_row_round_trips_through_parse_baseline(self):
        row = gate_rt.markdown_row("2026-10-07", "abc1234", 0.8, 0.84, 0.05, True)
        self.assertEqual(row, "| 2026-10-07 | abc1234 | 0.800 | 0.840 | +5.0% | pass |")
        header = "| date | sha | rest p99 ms | load p99 ms | change | verdict |\n| --- | --- | --- | --- | --- | --- |\n"
        self.assertEqual(gate_rt.parse_baseline(header + row + "\n"), 0.8)

    def test_an_unmeasured_row_says_so(self):
        row = gate_rt.markdown_row("2026-10-07", "abc1234", None, 0.84, None, False)
        self.assertEqual(row, "| 2026-10-07 | abc1234 | n/a | 0.840 | n/a | FAIL |")


class DockerCommand(unittest.TestCase):
    def test_the_load_runs_in_the_batch_slice_at_batch_weight(self):
        cmd = gate_rt.load_command(Path("/repo/tools/perf/load.py"), procs=2, seconds=300)
        joined = " ".join(cmd)
        self.assertEqual(cmd[:3], ["docker", "run", "--rm"])
        self.assertIn("--cgroup-parent ohcamel-batch.slice", joined)
        self.assertIn("--cpu-shares 128", joined)
        self.assertIn("--cpus 1.75", joined)
        self.assertIn("/repo/tools/perf/load.py:/load.py:ro", joined)
        self.assertEqual(cmd[-4:], ["--procs", "2", "--seconds", "300"])


if __name__ == "__main__":
    unittest.main()

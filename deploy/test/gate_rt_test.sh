#!/usr/bin/env bash
#
# The G-RT gate's arithmetic (compute plan Task 0.5): runs
# tools/perf/test_gate_rt.py, stdlib unittest, with the system python3.
# Picked up by the lint job's deploy/test/*.sh step. No engine, no Docker.
#
#   deploy/test/gate_rt_test.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
python3 -m unittest -v tools/perf/test_gate_rt.py

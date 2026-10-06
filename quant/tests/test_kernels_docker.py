"""Text pins on how the kernels reach production (the controller's deployment
ruling): the wheel is built inside quant/Dockerfile, production refuses to run
without it, and both workflows still build and test it."""

from __future__ import annotations

import re

from ohcamel_quant.config import REPO_ROOT

DOCKERFILE = (REPO_ROOT / "quant" / "Dockerfile").read_text(encoding="utf-8")
IGNORE = (REPO_ROOT / "quant" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
CI = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
IMAGE = (REPO_ROOT / ".github" / "workflows" / "image.yml").read_text(encoding="utf-8")


def test_wheel_is_built_in_a_rust_stage():
    assert re.search(r"^FROM rust:1\.80\S* AS rust-toolchain$", DOCKERFILE, re.M)
    assert re.search(r"^FROM python:3\.12-slim AS kernels$", DOCKERFILE, re.M)
    assert "maturin build --release --locked -m kernels/Cargo.toml" in DOCKERFILE
    assert "ls /wheels/ohcamel_kernels-*.whl" in DOCKERFILE


def test_wheel_is_installed_and_production_refuses_to_run_without_it():
    assert "COPY --from=kernels /wheels/ /wheels/" in DOCKERFILE
    assert "uv pip install --python /app/quant/.venv/bin/python --no-deps /wheels/ohcamel_kernels-" in DOCKERFILE
    assert "OHCAMEL_QUANT_KERNELS=rust" in DOCKERFILE
    assert "k.ENGINE == 'rust'" in DOCKERFILE


def test_benchmark_table_ships_in_the_image():
    assert "COPY docs/perf/kernels.json /app/docs/perf/kernels.json" in DOCKERFILE
    assert "!docs/perf/kernels.json" in IGNORE


def test_dockerignore_admits_native_but_not_its_build_output():
    assert "!native/" in IGNORE
    assert "native/target/" in IGNORE


def test_maturin_pin_matches_uv_lock():
    pin = re.search(r"^ARG MATURIN_VERSION=(\S+)$", DOCKERFILE, re.M)
    lock = (REPO_ROOT / "quant" / "uv.lock").read_text(encoding="utf-8")
    locked = re.search(r'^name = "maturin"\nversion = "([^"]+)"', lock, re.M)
    assert pin and locked and pin.group(1) == locked.group(1)


def test_image_workflow_still_builds_quant_from_the_repo_root():
    assert "dockerfile: quant/Dockerfile" in IMAGE
    assert "context: ." in IMAGE


def test_ci_native_job_builds_the_wheel_and_runs_parity_both_ways():
    job = CI[CI.index("\n  native:"):CI.index("\n  quant:")]
    assert "maturin build --release --locked -m kernels/Cargo.toml" in job
    assert "OHCAMEL_QUANT_KERNELS: rust" in job
    assert "OHCAMEL_QUANT_KERNELS: python" in job
    assert "tests/test_kernels_*.py" in job
    assert "--features python" in job


def test_image_asserts_every_kernel_runs_on_rust():
    assert "all(k.engine_of(n) == 'rust' for n in k.API)" in DOCKERFILE

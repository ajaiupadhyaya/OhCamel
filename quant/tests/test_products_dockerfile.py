"""Lane M, M6: the image can run EXP-Q01 (LightGBM's OpenMP runtime and the frozen config ship in it)."""

from ohcamel_quant.config import REPO_ROOT


def test_runtime_has_libgomp_and_the_experiment_configs():
    text = (REPO_ROOT / "quant" / "Dockerfile").read_text()
    runtime = text[text.index("AS runtime"):]
    assert "libgomp1" in runtime and "COPY research/experiments/ /app/research/experiments/" in runtime
    assert "import lightgbm" in runtime


def test_the_build_context_lets_the_experiment_configs_through():
    """quant/Dockerfile.dockerignore starts from `*`, so a COPY of research/experiments/ fails the
    build ("not found") unless the directory is allow-listed there."""
    lines = [ln.strip() for ln in (REPO_ROOT / "quant" / "Dockerfile.dockerignore").read_text().splitlines()]
    assert lines[lines.index("*"):].count("!research/experiments/") == 1


def test_the_image_carries_kronos_isolated_with_the_experiments_pinned_weights():
    """kronos.shadow runs research/kronos's venv as a subprocess; the image must bake the SAME revisions EXP-K01's
    frozen config pins, with CPU-only torch, and prove a forecast at build time as the runtime user."""
    import yaml

    text = (REPO_ROOT / "quant" / "Dockerfile").read_text()
    cfg = yaml.safe_load((REPO_ROOT / "research/experiments/EXP-K01/config.yaml").read_text())["kronos"]
    stage = text[text.index("AS kronos"):text.index("AS runtime")]
    assert cfg["model_revision"] in stage and cfg["tokenizer_revision"] in stage
    assert "'+cpu' in torch.__version__" in stage and "--no-editable" in stage
    runtime = text[text.index("AS runtime"):]
    assert "COPY --from=kronos /opt/kronos /opt/kronos" in runtime
    assert "OHCAMEL_KRONOS_WEIGHTS=/opt/kronos/weights" in runtime and "HF_HUB_OFFLINE=1" in runtime
    assert runtime.index("USER quant") < runtime.index("-m ohcamel_kronos shadow")
    ignore = [ln.strip() for ln in (REPO_ROOT / "quant" / "Dockerfile.dockerignore").read_text().splitlines()]
    for p in ("research/kronos/uv.lock", "research/kronos/src/", "research/kronos/vendor/"):
        assert f"!{p}" in ignore, p


def test_the_runtime_image_holds_the_lock_it_was_installed_from_where_the_manifest_reads_it():
    """Task 3.3: every manifest's lock_hash is the SHA-256 of quant/uv.lock under REPO_ROOT (/app in the image). The
    pydeps stage syncs --frozen from that file and the runtime stage copies /app/quant whole, so the lock on disk
    in the running container is the one installed; a COPY that dropped it would publish lock_hash null."""
    from ohcamel_quant.jobs.provenance import lock_path

    text = (REPO_ROOT / "quant" / "Dockerfile").read_text()
    pydeps = text[text.index("AS pydeps"):text.index("AS kronos")]
    runtime = text[text.index("AS runtime"):]
    assert "COPY quant/pyproject.toml quant/uv.lock quant/README.md ./" in pydeps and "uv sync --frozen" in pydeps
    assert "COPY --from=pydeps /app/quant /app/quant" in runtime
    assert lock_path() == REPO_ROOT / "quant" / "uv.lock" and lock_path().is_file()
    lines = [ln.strip() for ln in (REPO_ROOT / "quant" / "Dockerfile.dockerignore").read_text().splitlines()]
    assert any(ln in ("!quant/uv.lock", "!quant/") for ln in lines), "the lock must pass the build context"

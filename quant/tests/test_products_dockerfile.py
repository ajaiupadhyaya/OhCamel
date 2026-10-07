"""Lane M, M6: the image can run EXP-Q01 (LightGBM's OpenMP runtime and the frozen config ship in it)."""

from ohcamel_quant.config import REPO_ROOT


def test_runtime_has_libgomp_and_the_experiment_configs():
    text = (REPO_ROOT / "quant" / "Dockerfile").read_text()
    runtime = text[text.index("AS runtime"):]
    assert "libgomp1" in runtime and "COPY research/experiments/ /app/research/experiments/" in runtime
    assert "import lightgbm" in runtime

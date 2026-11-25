"""The kernel dispatcher (ohcamel_quant.kernels, contract II.4): engine choice,
the stale-wheel guard, coercion and validation."""

from __future__ import annotations

import importlib
import logging
import os
import sys
import types

import numpy as np
import pytest

from ohcamel_quant import kernels


@pytest.fixture
def reload_kernels(monkeypatch):
    """Reload the dispatcher under a patched environment; restore it afterwards."""

    def go(env, fake=...):
        if env is None:
            monkeypatch.delenv(kernels.ENV, raising=False)
        else:
            monkeypatch.setenv(kernels.ENV, env)
        if fake is not ...:
            monkeypatch.setitem(sys.modules, "ohcamel_kernels", fake)
        return importlib.reload(kernels)

    yield go
    monkeypatch.undo()
    importlib.reload(kernels)


def test_api_is_contract_ii4_in_order():
    assert kernels.API == ("fhs_paths", "copula_t_paths", "var_es_from_pnl", "stationary_bootstrap_means",
                           "cscv_pbo", "garch_nll", "garch_fit", "backtest_weights", "svi_fit",
                           "realized_vol_minute", "american_price", "american_iv")


def test_python_env_selects_the_reference(reload_kernels):
    k = reload_kernels("python")
    assert k.ENGINE == "python" and k._RUST is None
    assert k.engine_of("tail_count") == "python"


def test_rust_env_without_wheel_raises(reload_kernels):
    # sys.modules[name] = None makes `import name` raise ImportError
    with pytest.raises(ImportError, match="OHCAMEL_QUANT_KERNELS=rust"):
        reload_kernels("rust", None)


def test_auto_without_wheel_falls_back_and_says_so(reload_kernels, caplog):
    with caplog.at_level(logging.WARNING, logger="ohcamel_quant.kernels"):
        k = reload_kernels(None, None)
    assert k.ENGINE == "python"
    assert "not importable" in caplog.text


def test_stale_wheel_is_refused(reload_kernels, caplog):
    stale = types.SimpleNamespace(API_VERSION=0)
    with pytest.raises(ImportError, match="API_VERSION"):
        reload_kernels("rust", stale)
    with caplog.at_level(logging.WARNING, logger="ohcamel_quant.kernels"):
        k = reload_kernels(None, stale)
    assert k.ENGINE == "python" and "API_VERSION" in caplog.text


def test_bad_env_value(reload_kernels):
    with pytest.raises(ValueError, match="OHCAMEL_QUANT_KERNELS"):
        reload_kernels("fortran")


def test_rust_env_means_rust_engine():
    """CI's guard: under OHCAMEL_QUANT_KERNELS=rust nothing may fall back."""
    if os.environ.get(kernels.ENV) != "rust":
        pytest.skip("only meaningful under OHCAMEL_QUANT_KERNELS=rust")
    assert kernels.ENGINE == "rust"


def test_forced_switches_and_restores():
    before = kernels.engine_of("tail_count")
    with kernels.forced("python"):
        assert kernels.engine_of("tail_count") == "python"
    assert kernels.engine_of("tail_count") == before


def test_forced_rust_without_wheel_raises(monkeypatch):
    monkeypatch.setattr(kernels, "_RUST", None)
    with pytest.raises(RuntimeError, match="not importable"):
        with kernels.forced("rust"):
            pass


def test_coercion_makes_c_contiguous_float64():
    f = np.asfortranarray(np.arange(6, dtype=np.int64).reshape(3, 2))
    a = kernels._arr(f, "x", 2)
    assert a.flags["C_CONTIGUOUS"] and a.dtype == np.float64
    assert a.tolist() == [[0.0, 1.0], [2.0, 3.0], [4.0, 5.0]]
    m = kernels._matrix(np.arange(4.0), "x")          # 1-D -> one column
    assert m.shape == (4, 1) and m.flags["C_CONTIGUOUS"]


def test_coercion_rejects_wrong_dimensionality():
    with pytest.raises(ValueError, match="x must be 2-D"):
        kernels._arr(np.ones(3), "x", 2)
    with pytest.raises(ValueError, match="x must be 1-D or 2-D"):
        kernels._matrix(np.ones((2, 2, 2)), "x")


@pytest.mark.parametrize("bad", [0, -1, 65, 1000])
def test_threads_validation(bad):
    with pytest.raises(ValueError, match="threads"):
        kernels._threads(bad)


@pytest.mark.parametrize("bad", [-1, 2**64])
def test_seed_validation(bad):
    with pytest.raises(ValueError, match="seed"):
        kernels._seed(bad)


@pytest.mark.parametrize("bad", [0.5, 1.0, 0.2, float("nan")])
def test_alpha_validation(bad):
    with pytest.raises(ValueError, match="alpha"):
        kernels.tail_count(100, bad)


def test_every_api_function_exists_on_both_sides():
    from ohcamel_quant.kernels import reference

    for name in kernels.API:
        assert callable(getattr(kernels, name)) and callable(getattr(reference, name)), name
    if kernels._RUST is not None:
        missing = [n for n in kernels.API if not hasattr(kernels._RUST, n)]
        assert not missing, f"ohcamel_kernels lacks {missing}"


def test_rust_env_means_every_kernel_on_rust():
    if os.environ.get(kernels.ENV) != "rust":
        pytest.skip("only meaningful under OHCAMEL_QUANT_KERNELS=rust")
    # ... except a kernel the committed benchmark table measured faster on the reference (Task 4.4)
    assert all(kernels.engine_of(n) == "rust" for n in kernels.API if n not in kernels.TABLE_ENGINE)


# ---------------------------------------------------------------- Task 4.4: garch_fit follows the table
class _FakeWheel(types.SimpleNamespace):
    def garch_fit(self, y, kind, x):                # records that Rust was asked
        self.calls = getattr(self, "calls", 0) + 1
        raise AssertionError("garch_fit must not reach Rust when the table says Python is faster")


@pytest.fixture(autouse=True)
def _host(monkeypatch):
    from ohcamel_quant.kernels import bench

    monkeypatch.setattr(bench, "cpu_model", lambda: "Test CPU")


def _write_table(path, python_ms, rust_ms, host="Test CPU"):
    import json

    row = {"name": "garch_fit", "python_ms": python_ms, "rust_1t_ms": rust_ms, "rust_2t_ms": None,
           "host": host, "measured_on": "test box", "sha": "0b87dec"}
    path.write_text(json.dumps({"kernels": [row]}))


def test_garch_fit_runs_on_the_reference_when_the_table_says_it_is_faster(reload_kernels, monkeypatch, tmp_path):
    t = tmp_path / "kernels.json"
    _write_table(t, 3.0, 20.0)
    monkeypatch.setenv("OHCAMEL_QUANT_KERNEL_TABLE", str(t))
    fake = _FakeWheel(API_VERSION=reload_kernels(None, None).API_VERSION, garch_fit=None)
    k = reload_kernels("rust", fake)
    assert k.TABLE_ENGINE == {"garch_fit": "python"}
    assert k.engine_of("garch_fit") == "python" and k.engine_of("fhs_paths") == "python"  # fake has no fhs_paths
    y = 100.0 * np.random.default_rng(0).standard_normal(300)
    res = k.garch_fit(y, "gjr", None)
    assert res.params.size == 6 and not hasattr(fake, "calls")


def test_garch_fit_runs_on_rust_when_the_table_says_so(reload_kernels, monkeypatch, tmp_path):
    t = tmp_path / "kernels.json"
    _write_table(t, 30.0, 2.0)
    monkeypatch.setenv("OHCAMEL_QUANT_KERNEL_TABLE", str(t))
    fake = types.SimpleNamespace(API_VERSION=reload_kernels(None, None).API_VERSION, garch_fit=lambda *a: None)
    k = reload_kernels("rust", fake)
    assert k.TABLE_ENGINE == {"garch_fit": "rust"} and k.engine_of("garch_fit") == "rust"


def test_a_missing_or_invalid_table_leaves_the_kernel_on_rust(reload_kernels, monkeypatch, tmp_path):
    fake = types.SimpleNamespace(API_VERSION=reload_kernels(None, None).API_VERSION, garch_fit=lambda *a: None)
    monkeypatch.setenv("OHCAMEL_QUANT_KERNEL_TABLE", str(tmp_path / "absent.json"))
    assert reload_kernels("rust", fake).TABLE_ENGINE == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setenv("OHCAMEL_QUANT_KERNEL_TABLE", str(bad))
    assert reload_kernels("rust", fake).TABLE_ENGINE == {}


def test_forced_engine_overrides_the_table(reload_kernels, monkeypatch, tmp_path):
    t = tmp_path / "kernels.json"
    _write_table(t, 3.0, 20.0)
    monkeypatch.setenv("OHCAMEL_QUANT_KERNEL_TABLE", str(t))
    fake = types.SimpleNamespace(API_VERSION=reload_kernels(None, None).API_VERSION, garch_fit=lambda *a: None)
    k = reload_kernels("rust", fake)
    with k.forced("rust"):
        assert k.engine_of("garch_fit") == "rust"


def test_a_row_from_another_host_never_governs(reload_kernels, monkeypatch, tmp_path):
    """The image bakes the table in: a laptop's or a CI runner's row must leave the droplet's kernel on Rust."""
    t = tmp_path / "kernels.json"
    _write_table(t, 3.0, 20.0, host="Apple M2 Pro")
    monkeypatch.setenv("OHCAMEL_QUANT_KERNEL_TABLE", str(t))
    fake = types.SimpleNamespace(API_VERSION=reload_kernels(None, None).API_VERSION, garch_fit=lambda *a: None)
    k = reload_kernels("rust", fake)
    assert k.TABLE_ENGINE == {} and k.engine_of("garch_fit") == "rust"


def test_the_committed_table_keeps_every_kernel_on_rust_on_a_host_it_has_no_row_for(reload_kernels, monkeypatch):
    """What quant/Dockerfile's build-time assertion relies on."""
    monkeypatch.delenv("OHCAMEL_QUANT_KERNEL_TABLE", raising=False)
    from ohcamel_quant.kernels import bench

    monkeypatch.setattr(bench, "cpu_model", lambda: "Some CI Runner CPU")
    fake = types.SimpleNamespace(API_VERSION=reload_kernels(None, None).API_VERSION,
                                 **{n: (lambda *a: None) for n in kernels.API})
    k = reload_kernels("rust", fake)
    assert k.TABLE_ENGINE == {} and all(k.engine_of(n) == "rust" for n in k.API)

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
                           "realized_vol_minute")


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
    assert all(kernels.engine_of(n) == "rust" for n in kernels.API)

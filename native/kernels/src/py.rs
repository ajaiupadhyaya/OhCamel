//! The `ohcamel_kernels` Python module (contract II.4). Every binding takes
//! C-contiguous float64 (int64 for indices and timestamps) arrays -- the
//! dispatcher in quant/src/ohcamel_quant/kernels/__init__.py guarantees it --
//! releases the GIL around the core, and maps a core's Err to ValueError.
use crate::fhs::GarchPath;
use crate::par::Sim;
use numpy::prelude::*;
use numpy::{PyArray1, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn err(e: String) -> PyErr {
    PyValueError::new_err(e)
}

fn s1<'a>(a: &'a PyReadonlyArray1<'_, f64>, name: &str) -> PyResult<&'a [f64]> {
    a.as_slice()
        .map_err(|_| err(format!("{name} must be a C-contiguous float64 array")))
}

fn s2<'a>(a: &'a PyReadonlyArray2<'_, f64>, name: &str) -> PyResult<(&'a [f64], usize, usize)> {
    let shape = a.shape();
    let (rows, cols) = (shape[0], shape[1]);
    let s = a
        .as_slice()
        .map_err(|_| err(format!("{name} must be a C-contiguous float64 array")))?;
    Ok((s, rows, cols))
}

#[pyfunction]
fn tail_count(n: usize, alpha: f64) -> usize {
    crate::tail::tail_count(n, alpha)
}

#[pyfunction]
fn var_es_from_pnl(
    py: Python<'_>,
    pnl: PyReadonlyArray1<'_, f64>,
    alpha: f64,
) -> PyResult<(f64, f64)> {
    let p = s1(&pnl, "pnl")?;
    Ok(py.allow_threads(|| crate::tail::var_es_from_pnl(p, alpha)))
}

#[pyfunction]
fn fhs_paths<'py>(
    py: Python<'py>,
    std_resid: PyReadonlyArray2<'py, f64>,
    mu: PyReadonlyArray1<'py, f64>,
    omega: PyReadonlyArray1<'py, f64>,
    alpha: PyReadonlyArray1<'py, f64>,
    gamma: PyReadonlyArray1<'py, f64>,
    beta: PyReadonlyArray1<'py, f64>,
    sigma2_next: PyReadonlyArray1<'py, f64>,
    weights: PyReadonlyArray1<'py, f64>,
    horizon: usize,
    n_paths: usize,
    seed: u64,
    threads: usize,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let (z, n, k) = s2(&std_resid, "std_resid")?;
    let g = GarchPath {
        mu: s1(&mu, "mu")?,
        omega: s1(&omega, "omega")?,
        alpha: s1(&alpha, "alpha")?,
        gamma: s1(&gamma, "gamma")?,
        beta: s1(&beta, "beta")?,
        sigma2_next: s1(&sigma2_next, "sigma2_next")?,
    };
    let w = s1(&weights, "weights")?;
    let sim = Sim {
        horizon,
        n_paths,
        seed,
        threads,
    };
    let out = py
        .allow_threads(|| crate::fhs::fhs_paths(z, n, k, &g, w, &sim))
        .map_err(err)?;
    Ok(out.into_pyarray_bound(py))
}

#[pyfunction]
fn copula_t_paths<'py>(
    py: Python<'py>,
    returns: PyReadonlyArray2<'py, f64>,
    weights: PyReadonlyArray1<'py, f64>,
    nu: f64,
    horizon: usize,
    n_paths: usize,
    seed: u64,
    threads: usize,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let (x, n, k) = s2(&returns, "returns")?;
    let w = s1(&weights, "weights")?;
    let sim = Sim {
        horizon,
        n_paths,
        seed,
        threads,
    };
    let out = py
        .allow_threads(|| crate::copula::copula_t_paths(x, n, k, w, nu, &sim))
        .map_err(err)?;
    Ok(out.into_pyarray_bound(py))
}

#[pyfunction]
#[pyo3(name = "_kendall_corr")]
fn kendall_corr_py<'py>(
    py: Python<'py>,
    returns: PyReadonlyArray2<'py, f64>,
    threads: usize,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let (x, n, k) = s2(&returns, "returns")?;
    let out = py
        .allow_threads(|| crate::copula::kendall_corr(x, n, k, threads))
        .map_err(err)?;
    Ok(out.into_pyarray_bound(py))
}

#[pyfunction]
fn stationary_bootstrap_means<'py>(
    py: Python<'py>,
    x: PyReadonlyArray2<'py, f64>,
    mean_block: f64,
    reps: usize,
    seed: u64,
    threads: usize,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let (v, n, k) = s2(&x, "x")?;
    let out = py
        .allow_threads(|| {
            crate::bootstrap::stationary_bootstrap_means(v, n, k, mean_block, reps, seed, threads)
        })
        .map_err(err)?;
    Ok(out.into_pyarray_bound(py))
}

#[pyfunction]
fn cscv_pbo<'py>(
    py: Python<'py>,
    perf: PyReadonlyArray2<'py, f64>,
    n_partitions: usize,
    threads: usize,
) -> PyResult<(
    f64,
    Bound<'py, PyArray1<f64>>,
    usize,
    Bound<'py, PyArray1<i64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
)> {
    let (m, t, nt) = s2(&perf, "perf")?;
    let r = py
        .allow_threads(|| crate::cscv::cscv_pbo(m, t, nt, n_partitions, threads))
        .map_err(err)?;
    let n = r.logits.len();
    let sel: Vec<i64> = r.selected.iter().map(|&v| v as i64).collect();
    Ok((
        r.pbo,
        r.logits.into_pyarray_bound(py),
        n,
        sel.into_pyarray_bound(py),
        r.is_sharpe.into_pyarray_bound(py),
        r.oos_sharpe.into_pyarray_bound(py),
    ))
}

#[pyfunction]
fn garch_nll(
    py: Python<'_>,
    params: PyReadonlyArray1<'_, f64>,
    r: PyReadonlyArray1<'_, f64>,
    kind: String,
) -> PyResult<f64> {
    let k = crate::garch::Kind::parse(&kind).map_err(err)?;
    let (p, y) = (s1(&params, "params")?, s1(&r, "r")?);
    py.allow_threads(|| crate::garch::garch_nll(p, y, k))
        .map_err(err)
}

#[pyfunction]
#[pyo3(signature = (r, kind, x0=None))]
fn garch_fit<'py>(
    py: Python<'py>,
    r: PyReadonlyArray1<'py, f64>,
    kind: String,
    x0: Option<PyReadonlyArray1<'py, f64>>,
) -> PyResult<(
    Bound<'py, PyArray1<f64>>,
    f64,
    bool,
    usize,
    Bound<'py, PyArray1<f64>>,
    f64,
    Bound<'py, PyArray1<f64>>,
)> {
    let k = crate::garch::Kind::parse(&kind).map_err(err)?;
    let y = s1(&r, "r")?;
    let start = match &x0 {
        Some(a) => Some(s1(a, "x0")?),
        None => None,
    };
    let f = py
        .allow_threads(|| crate::garch::garch_fit(y, k, start))
        .map_err(err)?;
    Ok((
        f.params.into_pyarray_bound(py),
        f.nll,
        f.converged,
        f.iterations,
        f.sigma2.into_pyarray_bound(py),
        f.next_variance,
        f.std_resid.into_pyarray_bound(py),
    ))
}

#[pyfunction]
fn backtest_weights<'py>(
    py: Python<'py>,
    prices: PyReadonlyArray2<'py, f64>,
    target_w: PyReadonlyArray2<'py, f64>,
    decision_idx: PyReadonlyArray1<'py, i64>,
    cost_bps: f64,
    borrow_bps: f64,
    rf: PyReadonlyArray1<'py, f64>,
) -> PyResult<(
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
    bool,
)> {
    let (px, nt, na) = s2(&prices, "prices")?;
    let (tw, _, _) = s2(&target_w, "target_w")?;
    let idx = decision_idx
        .as_slice()
        .map_err(|_| err("decision_idx must be a C-contiguous int64 array".into()))?;
    let rfs = s1(&rf, "rf")?;
    let p = py
        .allow_threads(|| {
            crate::backtest::backtest_weights(px, nt, na, tw, idx, cost_bps, borrow_bps, rfs)
        })
        .map_err(err)?;
    Ok((
        p.gross.into_pyarray_bound(py),
        p.net.into_pyarray_bound(py),
        p.weights.into_pyarray_bound(py),
        p.turnover.into_pyarray_bound(py),
        p.trades.into_pyarray_bound(py),
        p.costs.into_pyarray_bound(py),
        p.borrow.into_pyarray_bound(py),
        p.ruined,
    ))
}

#[pyfunction]
fn svi_fit(
    py: Python<'_>,
    k: PyReadonlyArray1<'_, f64>,
    w: PyReadonlyArray1<'_, f64>,
    weights: PyReadonlyArray1<'_, f64>,
) -> PyResult<(f64, f64, f64, f64, f64, f64, bool)> {
    let (kk, ww, wt) = (s1(&k, "k")?, s1(&w, "w")?, s1(&weights, "weights")?);
    let f = py
        .allow_threads(|| crate::svi::svi_fit(kk, ww, wt))
        .map_err(err)?;
    Ok((f.a, f.b, f.rho, f.m, f.sigma, f.sse, f.constrained_a))
}

#[pyfunction]
fn realized_vol_minute<'py>(
    py: Python<'py>,
    ts_ns: PyReadonlyArray1<'py, i64>,
    px: PyReadonlyArray1<'py, f64>,
    session_bounds: PyReadonlyArray2<'py, i64>,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let ts = ts_ns
        .as_slice()
        .map_err(|_| err("ts_ns must be a C-contiguous int64 array".into()))?;
    let b = session_bounds
        .as_slice()
        .map_err(|_| err("session_bounds must be a C-contiguous int64 array".into()))?;
    let p = s1(&px, "px")?;
    let out = py
        .allow_threads(|| crate::rv::realized_variance(ts, p, b))
        .map_err(err)?;
    Ok(out.into_pyarray_bound(py))
}

#[pymodule]
fn ohcamel_kernels(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("API_VERSION", crate::API_VERSION)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(tail_count, m)?)?;
    m.add_function(wrap_pyfunction!(var_es_from_pnl, m)?)?;
    m.add_function(wrap_pyfunction!(fhs_paths, m)?)?;
    m.add_function(wrap_pyfunction!(copula_t_paths, m)?)?;
    m.add_function(wrap_pyfunction!(kendall_corr_py, m)?)?;
    m.add_function(wrap_pyfunction!(stationary_bootstrap_means, m)?)?;
    m.add_function(wrap_pyfunction!(cscv_pbo, m)?)?;
    m.add_function(wrap_pyfunction!(garch_nll, m)?)?;
    m.add_function(wrap_pyfunction!(garch_fit, m)?)?;
    m.add_function(wrap_pyfunction!(backtest_weights, m)?)?;
    m.add_function(wrap_pyfunction!(svi_fit, m)?)?;
    m.add_function(wrap_pyfunction!(realized_vol_minute, m)?)?;
    Ok(())
}

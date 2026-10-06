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

#[pymodule]
fn ohcamel_kernels(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("API_VERSION", crate::API_VERSION)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(tail_count, m)?)?;
    m.add_function(wrap_pyfunction!(var_es_from_pnl, m)?)?;
    m.add_function(wrap_pyfunction!(fhs_paths, m)?)?;
    m.add_function(wrap_pyfunction!(copula_t_paths, m)?)?;
    m.add_function(wrap_pyfunction!(kendall_corr_py, m)?)?;
    Ok(())
}

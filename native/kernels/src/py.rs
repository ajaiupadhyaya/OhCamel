//! The `ohcamel_kernels` Python module (contract II.4). Every binding takes
//! C-contiguous float64 (int64 for indices and timestamps) arrays -- the
//! dispatcher in quant/src/ohcamel_quant/kernels/__init__.py guarantees it --
//! releases the GIL around the core, and maps a core's Err to ValueError.
use numpy::prelude::*;
use numpy::{PyReadonlyArray1, PyReadonlyArray2};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[allow(dead_code)]
fn err(e: String) -> PyErr {
    PyValueError::new_err(e)
}

#[allow(dead_code)]
fn s1<'a>(a: &'a PyReadonlyArray1<'_, f64>, name: &str) -> PyResult<&'a [f64]> {
    a.as_slice()
        .map_err(|_| err(format!("{name} must be a C-contiguous float64 array")))
}

#[allow(dead_code)]
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

#[pymodule]
fn ohcamel_kernels(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("API_VERSION", crate::API_VERSION)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(tail_count, m)?)?;
    Ok(())
}

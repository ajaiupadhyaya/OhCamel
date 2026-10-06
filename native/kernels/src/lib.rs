//! `ohcamel_kernels`: OhCamel Quant's native kernels (compute plan contract
//! II.4, docs/superpowers/plans/2026-09-24-quant-compute-program.md). Each
//! module is a pure-Rust core with no Python types; `py.rs` binds them behind
//! the `python` feature, so `cargo test` needs no libpython and the wheel
//! (maturin: `features = ["python", "pyo3/extension-module"]`) is the only
//! thing that links them into Python.
// Numeric kernels index several parallel arrays with one loop index, and the
// bindings take the contract's argument lists, and return its result tuples, as
// they are.
#![allow(
    clippy::needless_range_loop,
    clippy::too_many_arguments,
    clippy::type_complexity
)]

pub mod bootstrap;
pub mod copula;
pub mod fhs;
pub mod par;
pub mod special;
pub mod tail;

#[cfg(feature = "python")]
mod py;

/// The binding surface's version. The Python dispatcher refuses a wheel whose
/// value it does not know (a stale build), so bump it with any signature or
/// semantics change in `py.rs`.
pub const API_VERSION: u32 = 1;

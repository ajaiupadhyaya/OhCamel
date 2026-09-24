"""Method catalog and a single dispatcher used by the API and the walk-forward engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import hrp as hrp_mod
from . import optimize as opt
from .optimize import Constraints, PortfolioResult


@dataclass(frozen=True)
class MethodSpec:
    name: str
    label: str
    description: str
    reference: str
    needs_mu: bool = False
    needs_rf: bool = False
    honours_constraints: bool = True
    parameters: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "description": self.description,
                "reference": self.reference, "needs_expected_returns": self.needs_mu,
                "needs_risk_free": self.needs_rf, "honours_constraints": self.honours_constraints,
                "parameters": list(self.parameters)}


def _p(name: str, typ: str, default: Any, desc: str) -> dict[str, Any]:
    return {"name": name, "type": typ, "default": default, "description": desc}


METHODS: dict[str, MethodSpec] = {m.name: m for m in (
    MethodSpec("equal_weight", "Equal weight (1/N)",
               "Every asset gets 1/N. No estimation error at all -- the benchmark that optimised "
               "portfolios routinely fail to beat out of sample.",
               "DeMiguel, Garlappi & Uppal (2009), 'Optimal versus naive diversification', RFS 22(5)",
               honours_constraints=False),
    MethodSpec("inverse_volatility", "Inverse volatility",
               "w_i proportional to 1/sigma_i: naive risk parity, ignores correlations.",
               "Maillard, Roncalli & Teiletche (2010), JPM 36(4) (special case of ERC)",
               honours_constraints=False),
    MethodSpec("min_variance", "Global minimum variance",
               "min w'Sigma w subject to the constraints; needs no expected returns.",
               "Markowitz (1952), JF 7(1); Clarke, de Silva & Thorley (2006), JPM 33(1)"),
    MethodSpec("max_sharpe", "Maximum Sharpe (tangency)",
               "max (mu'w - rf)/sqrt(w'Sigma w), solved as a convex QP after the "
               "Cornuejols-Tutuncu homogenisation y = kappa w.",
               "Sharpe (1964); Cornuejols & Tutuncu (2007), Optimization Methods in Finance, 8.2",
               needs_mu=True, needs_rf=True),
    MethodSpec("mean_variance", "Mean-variance",
               "Markowitz with a target return, target volatility or risk-aversion coefficient.",
               "Markowitz (1952), 'Portfolio selection', JF 7(1)", needs_mu=True,
               parameters=(_p("target_return", "float|null", None, "annual decimal expected return floor"),
                           _p("target_vol", "float|null", None, "annual volatility cap"),
                           _p("risk_aversion", "float|null", None, "gamma in mu'w - gamma/2 w'Sigma w"))),
    MethodSpec("risk_parity", "Equal risk contribution",
               "Each asset contributes the same share (or its budget) of portfolio volatility; "
               "Spinu's convex log-barrier problem solved by Newton's method. Long-only.",
               "Maillard, Roncalli & Teiletche (2010), JPM 36(4); Spinu (2013), SSRN 2297383",
               honours_constraints=False,
               parameters=(_p("risk_budgets", "dict[str,float]|null", None,
                              "relative risk budgets (normalised); default equal"),)),
    MethodSpec("hrp", "Hierarchical risk parity",
               "Cluster by correlation distance, quasi-diagonalise, allocate by recursive "
               "bisection with inverse-variance splits. No matrix inversion.",
               "Lopez de Prado (2016), 'Building diversified portfolios that outperform out of "
               "sample', JPM 42(4)", honours_constraints=False,
               parameters=(_p("linkage", "single|ward|average|complete", "single", "tree linkage"),)),
    MethodSpec("herc", "Hierarchical equal risk contribution",
               "Top-down risk parity between dendrogram clusters, inverse-variance within them.",
               "Raffinot (2018), 'The hierarchical equal risk contribution portfolio', SSRN 3237540",
               honours_constraints=False,
               parameters=(_p("linkage", "single|ward|average|complete", "ward", "tree linkage"),
                           _p("n_clusters", "int|null", None, "clusters; default = largest merge-height gap"))),
    MethodSpec("max_diversification", "Maximum diversification",
               "max w'sigma / sqrt(w'Sigma w): the portfolio with the highest diversification ratio.",
               "Choueifaty & Coignard (2008), 'Toward maximum diversification', JPM 35(1)"),
    MethodSpec("min_cvar", "Minimum CVaR",
               "Minimise historical expected shortfall of daily losses as a linear program (HiGHS).",
               "Rockafellar & Uryasev (2000), 'Optimization of conditional value-at-risk', J. Risk 2(3)",
               parameters=(_p("cvar_alpha", "float", 0.95, "confidence level"),
                           _p("target_return", "float|null", None, "annual expected return floor"))),
)}


def run_method(name: str, returns: pd.DataFrame, sigma: pd.DataFrame, mu: pd.Series | None,
               rf: float | None, cons: Constraints | None = None, *,
               target_return: float | None = None, target_vol: float | None = None,
               risk_aversion: float | None = None, risk_budgets: dict[str, float] | None = None,
               linkage: str | None = None, n_clusters: int | None = None,
               cvar_alpha: float = 0.95, with_diagnostics: bool = True) -> PortfolioResult:
    """Run one catalogued method and return weights with ex-ante diagnostics.

    ``returns`` are the DAILY estimation-window returns (used by min-CVaR
    scenarios); ``sigma``/``mu`` are annualized; ``rf`` is an annual decimal.
    """
    w, spec, params, notes, extra = _compute(
        name, returns, sigma, mu, rf, cons, target_return=target_return, target_vol=target_vol,
        risk_aversion=risk_aversion, risk_budgets=risk_budgets, linkage=linkage, n_clusters=n_clusters,
        cvar_alpha=cvar_alpha, with_diagnostics=with_diagnostics)
    return opt.evaluate(name, w, sigma, mu, rf, reference=spec.reference, params=params,
                        notes=notes, extra=extra)


def method_weights(name: str, returns: pd.DataFrame, sigma: pd.DataFrame, mu: pd.Series | None,
                   rf: float | None, cons: Constraints | None = None, **kw: Any) -> np.ndarray:
    """Weights only (no ex-ante diagnostics) -- the walk-forward engine's hot path."""
    return _compute(name, returns, sigma, mu, rf, cons, with_diagnostics=False, **kw)[0]


def _compute(name: str, returns: pd.DataFrame, sigma: pd.DataFrame, mu: pd.Series | None,
             rf: float | None, cons: Constraints | None = None, *,
             target_return: float | None = None, target_vol: float | None = None,
             risk_aversion: float | None = None, risk_budgets: dict[str, float] | None = None,
             linkage: str | None = None, n_clusters: int | None = None,
             cvar_alpha: float = 0.95, with_diagnostics: bool = True
             ) -> tuple[np.ndarray, MethodSpec, dict[str, Any], list[str], dict[str, Any]]:
    if name not in METHODS:
        raise ValueError(f"unknown method {name!r}; choose from {', '.join(METHODS)}")
    spec = METHODS[name]
    cons = cons or Constraints()
    names = list(sigma.columns)
    n = len(names)
    if spec.needs_mu and mu is None:
        raise ValueError(f"{name} needs expected returns")
    if spec.needs_rf and rf is None:
        raise ValueError(f"{name} needs a risk-free rate")
    notes: list[str] = []
    params: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    if name == "equal_weight":
        w = opt.equal_weight(n)
    elif name == "inverse_volatility":
        w = opt.inverse_volatility(sigma)
    elif name == "min_variance":
        w = opt.min_variance(sigma, cons)
    elif name == "max_sharpe":
        w = opt.max_sharpe(mu, sigma, float(rf), cons)  # type: ignore[arg-type]
    elif name == "mean_variance":
        if target_return is None and target_vol is None and risk_aversion is None:
            raise ValueError("mean_variance needs target_return, target_vol or risk_aversion")
        w = opt.mean_variance(mu, sigma, cons, target_return=target_return,  # type: ignore[arg-type]
                              target_vol=target_vol, risk_aversion=risk_aversion)
        params = {"target_return": target_return, "target_vol": target_vol, "risk_aversion": risk_aversion}
    elif name == "risk_parity":
        b = None
        if risk_budgets:
            unknown = sorted(set(risk_budgets) - set(names))
            if unknown:
                raise ValueError(f"risk budgets given for tickers outside the universe: {', '.join(unknown)}")
            b = np.array([float(risk_budgets.get(t, 0.0)) for t in names])
            if not np.all(np.isfinite(b)) or np.any(b <= 0):
                missing = [t for t, x in zip(names, b, strict=True) if not x > 0]
                raise ValueError(f"risk budgets must be positive for every asset (missing: {', '.join(missing)})")
            b = b / b.sum()
        w, it = opt.risk_parity(sigma, b)
        params = {"newton_iterations": it,
                  "budgets": dict(zip(names, (b if b is not None else np.full(n, 1 / n)).tolist(), strict=True))}
    elif name == "hrp":
        lk = linkage or "single"
        w, h, steps = hrp_mod.hrp(sigma, lk)  # type: ignore[arg-type]
        params = {"linkage": lk, "distance": "paper (Euclidean distance of correlation-distance columns)"}
        if with_diagnostics:
            extra = {"dendrogram": h.dendrogram(), "bisection": steps}
    elif name == "herc":
        lk = linkage or "ward"
        w, h, info = hrp_mod.herc(sigma, lk, n_clusters)  # type: ignore[arg-type]
        params = {"linkage": lk, **info}
        if with_diagnostics:
            extra = {"dendrogram": h.dendrogram()}
    elif name == "max_diversification":
        w = opt.max_diversification(sigma, cons)
    elif name == "min_cvar":
        w, info = opt.min_cvar(returns[names], cvar_alpha, cons, mu, target_return)
        params = {"cvar_alpha": cvar_alpha, "target_return": target_return}
        extra = {"cvar_daily": info["cvar_daily"], "var_daily": info["var_daily"],
                 "scenarios": info["scenarios"]}
    else:  # pragma: no cover
        raise ValueError(name)
    if not spec.honours_constraints and not cons.is_default(n) and not cons.satisfied(w):
        notes.append(f"{spec.label} is a heuristic allocation that does not take constraints; "
                     "these weights violate the requested bounds/turnover.")
    return np.asarray(w, float), spec, params, notes, extra

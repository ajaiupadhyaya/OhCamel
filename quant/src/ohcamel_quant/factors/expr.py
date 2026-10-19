"""A point-in-time factor expression language, with qlib's operator semantics.

Ported from Microsoft qlib (MIT, (c) Microsoft Corporation): the operator set and its semantics follow
``qlib/data/ops.py`` and ``qlib/data/_libs/rolling.pyx`` (v0.9.7). qlib itself is NOT a dependency. It would
bring MLflow, redis, pymongo and its own binary data store, and OhCamel already has DuckDB, provenance sidecars and
an artifact ledger. A factor is written as qlib writes it::

    Mean($close, 20) / $close
    Corr($close / Ref($close, 1), Log($volume / Ref($volume, 1) + 1), 10)
    ($close - Min($low, 30)) / (Max($high, 30) - Min($low, 30) + 1e-12)

and evaluates on a dict of (date x ticker) frames (``open, high, low, close, volume`` and anything else named
with ``$``). Every time-series operator works down each ticker's column independently. ``CSRank`` (not in qlib)
ranks across tickers on each date.

**Departures from qlib, all in the direction of the charter (``compat=True`` restores qlib exactly, for the
parity test against qlib's own output):**

1. **Lookahead is a parse error.** ``Ref(x, -1)`` reads tomorrow, and qlib allows it. Here it raises
   ``LookAheadError``.
2. **No window depends on where the data starts.** ``N = 0`` (expanding) and ``Ref(x, 0)`` (the first loaded
   value) give a different number for the same date depending on how much history was loaded, so a
   "point-in-time" value would change with the query. Refused.
3. **No warm-up results.** qlib uses ``min_periods=1``, so ``Mean($close, 60)`` on a name's third session is a
   3-day mean, under the name of a 60-day one. Here a window that is not full is NaN.

``canonical(expr)`` is the normal form, the same for any spacing or redundant parentheses, and
``expr_hash`` hashes it. That hash is a factor's identity in the trial ledger (``factors.workbench``).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from ..backtest.engine import LookAheadError

Frame = pd.DataFrame
Data = dict[str, Frame]


class ExprError(ValueError):
    """A malformed expression: unknown operator or field, wrong arity, bad window."""


# ============================================================ AST
@dataclass(frozen=True)
class Node:
    def __str__(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError


@dataclass(frozen=True)
class Num(Node):
    value: float

    def __str__(self) -> str:
        v = self.value
        return str(int(v)) if float(v).is_integer() and abs(v) < 1e15 else repr(float(v))


@dataclass(frozen=True)
class Field(Node):
    name: str

    def __str__(self) -> str:
        return f"${self.name}"


@dataclass(frozen=True)
class Call(Node):
    op: str
    args: tuple[Node, ...]

    def __str__(self) -> str:
        return f"{self.op}({', '.join(str(a) for a in self.args)})"


# ============================================================ parser
_TOKEN = re.compile(r"\s*(?:(?P<num>\d+\.\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?|\d+(?:[eE][-+]?\d+)?)"
                    r"|(?P<field>\$[A-Za-z_][A-Za-z0-9_]*)|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
                    r"|(?P<op>>=|<=|==|!=|[-+*/(),<>&|]))")
_BINARY = [  # lowest precedence first; each level is left-associative
    {"|": "Or"}, {"&": "And"},
    {">": "Gt", ">=": "Ge", "<": "Lt", "<=": "Le", "==": "Eq", "!=": "Ne"},
    {"+": "Add", "-": "Sub"}, {"*": "Mul", "/": "Div"},
]


def _tokens(s: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    s = s.rstrip()
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if not m or m.end() == pos:
            raise ExprError(f"cannot read {s[pos:pos + 12]!r} at position {pos}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, s: str) -> None:
        self.t = _tokens(s)
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if tok is None or (value is not None and tok[1] != value):
            raise ExprError(f"expected {value or 'a token'}, found {tok[1] if tok else 'the end'}")
        self.i += 1
        return tok

    def parse(self) -> Node:
        node = self.level(0)
        if self.peek() is not None:
            raise ExprError(f"unexpected {self.peek()[1]!r}")
        return node

    def level(self, k: int) -> Node:
        if k == len(_BINARY):
            return self.unary()
        node = self.level(k + 1)
        while (tok := self.peek()) and tok[0] == "op" and tok[1] in _BINARY[k]:
            self.take()
            node = Call(_BINARY[k][tok[1]], (node, self.level(k + 1)))
        return node

    def unary(self) -> Node:
        tok = self.peek()
        if tok and tok[1] == "-":
            self.take()
            inner = self.unary()
            return Num(-inner.value) if isinstance(inner, Num) else Call("Mul", (Num(-1.0), inner))
        if tok and tok[1] == "+":
            self.take()
            return self.unary()
        return self.atom()

    def atom(self) -> Node:
        kind, val = self.take()
        if kind == "num":
            return Num(float(val))
        if kind == "field":
            return Field(val[1:])
        if kind == "op" and val == "(":
            node = self.level(0)
            self.take(")")
            return node
        if kind == "name":
            self.take("(")
            args: list[Node] = []
            if self.peek() and self.peek()[1] != ")":
                args.append(self.level(0))
                while self.peek() and self.peek()[1] == ",":
                    self.take(",")
                    args.append(self.level(0))
            self.take(")")
            return _check(Call(val, tuple(args)))
        raise ExprError(f"unexpected {val!r}")


def parse(expr: str) -> Node:
    """Parse and validate. Lookahead raises ``LookAheadError``; anything else malformed raises ``ExprError``."""
    return _Parser(expr).parse()


def canonical(expr: str | Node) -> str:
    return str(parse(expr) if isinstance(expr, str) else expr)


def expr_hash(expr: str | Node) -> str:
    return hashlib.sha256(canonical(expr).encode()).hexdigest()[:16]


def fields_of(node: Node) -> set[str]:
    if isinstance(node, Field):
        return {node.name}
    if isinstance(node, Call):
        return set().union(*(fields_of(a) for a in node.args)) if node.args else set()
    return set()


# ============================================================ operators
def _bool(x: Frame) -> Frame:
    return x.astype(float)


ELEM: dict[str, Callable[[Frame], Frame]] = {
    "Abs": lambda x: x.abs(), "Sign": lambda x: np.sign(x), "Log": lambda x: np.log(x),
    "Not": lambda x: _bool(~x.astype(bool)),
}
PAIR: dict[str, Callable] = {
    "Add": lambda a, b: a + b, "Sub": lambda a, b: a - b, "Mul": lambda a, b: a * b, "Div": lambda a, b: a / b,
    "Power": lambda a, b: a ** b,
    "Greater": lambda a, b: np.maximum(a, b), "Less": lambda a, b: np.minimum(a, b),  # qlib: np.maximum
    "Gt": lambda a, b: _bool(a > b), "Ge": lambda a, b: _bool(a >= b), "Lt": lambda a, b: _bool(a < b),
    "Le": lambda a, b: _bool(a <= b), "Eq": lambda a, b: _bool(a == b), "Ne": lambda a, b: _bool(a != b),
    "And": lambda a, b: _bool(a.astype(bool) & b.astype(bool)),
    "Or": lambda a, b: _bool(a.astype(bool) | b.astype(bool)),
}
ROLL_PANDAS = {"Mean": "mean", "Sum": "sum", "Std": "std", "Var": "var", "Skew": "skew", "Kurt": "kurt",
               "Max": "max", "Min": "min", "Med": "median", "Count": "count"}
ROLL_OTHER = {"IdxMax", "IdxMin", "Quantile", "Mad", "Rank", "Slope", "Rsquare", "Resi", "WMA", "EMA", "Delta",
              "Ref"}
PAIR_ROLL = {"Corr", "Cov"}
CROSS = {"CSRank"}
ARITY = {**dict.fromkeys(ELEM, 1), **dict.fromkeys(PAIR, 2), **dict.fromkeys(ROLL_PANDAS, 2),
         **dict.fromkeys(ROLL_OTHER, 2), "Quantile": 3, **dict.fromkeys(PAIR_ROLL, 3), "If": 3, "CSRank": 1}
OPERATORS = tuple(sorted(ARITY))


def _window(node: Call, k: int) -> float:
    w = node.args[k]
    if not isinstance(w, Num):
        raise ExprError(f"{node.op}: the window must be a number, got {w}")
    return w.value


def _check(node: Call) -> Call:
    op = node.op
    if op not in ARITY:
        raise ExprError(f"unknown operator {op!r}; known: {', '.join(OPERATORS)}")
    if len(node.args) != ARITY[op]:
        raise ExprError(f"{op} takes {ARITY[op]} arguments, got {len(node.args)}")
    if op in ROLL_PANDAS or op in ROLL_OTHER or op in PAIR_ROLL:
        n = _window(node, 2 if op in PAIR_ROLL else 1)
        if op == "Ref" and n < 0:
            raise LookAheadError(f"{node}: Ref with a negative lag reads the future")
        if op == "EMA" and 0 < n < 1:
            return node                                    # an alpha, as qlib allows
        if n != int(n) or n < 0:
            raise ExprError(f"{node}: window must be a non-negative integer")
    if op == "Quantile":
        q = node.args[2]
        if not isinstance(q, Num) or not 0 <= q.value <= 1:
            raise ExprError(f"{node}: quantile must be a number in [0, 1]")
    return node


# ============================================================ evaluation
@dataclass(frozen=True)
class Mode:
    compat: bool = False

    def minp(self, n: int) -> int:
        return 1 if self.compat else n


def _per_column(x: Frame, fn: Callable[[np.ndarray], np.ndarray]) -> Frame:
    return pd.DataFrame({c: fn(x[c].to_numpy(float)) for c in x.columns}, index=x.index)[x.columns]


def _apply(x: Frame, n: int, mode: Mode, fn: Callable[[np.ndarray], float]) -> Frame:
    return x.rolling(n, min_periods=mode.minp(n)).apply(fn, raw=True)


def _linreg(y: np.ndarray, n: int, compat: bool, what: str) -> np.ndarray:
    """Rolling OLS of y on its position in the window (rolling.pyx's Slope/Rsquare/Resi). With ``compat`` the
    first n-1 rows use the observations so far, as qlib's NaN-filled buffer does; otherwise they are NaN."""
    T = y.size
    out = np.full(T, np.nan)

    def stats(w: np.ndarray) -> np.ndarray:            # w: (rows, k) windows, no NaN handling needed per row
        k = w.shape[1]
        x = np.arange(1.0, k + 1.0)
        xm = x.mean()
        ym = w.mean(axis=1)
        sxx = ((x - xm) ** 2).sum()
        sxy = ((w - ym[:, None]) * (x - xm)).sum(axis=1)
        slope = sxy / sxx
        if what == "slope":
            return slope
        if what == "resi":
            return w[:, -1] - (slope * x[-1] + (ym - slope * xm))
        syy = ((w - ym[:, None]) ** 2).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return sxy * sxy / (sxx * syy)

    if T >= n:
        win = sliding_window_view(y, n)
        res = stats(win)
        gaps = np.flatnonzero(np.isnan(win).any(axis=1))
        res[gaps] = np.nan                                  # strict: a window with a gap is not full
        if compat:                                          # qlib fits the valid points instead (slow path)
            res[gaps] = [_linreg_sparse(win[i], what) for i in gaps]
        out[n - 1:] = res
    if compat:
        for t in range(min(n - 1, T)):
            out[t] = _linreg_sparse(y[:t + 1], what, n)
    return out


def _linreg_sparse(w: np.ndarray, what: str, window: int | None = None) -> float:
    """One qlib window with missing values: positions of the valid points, x = 1..window at the right edge."""
    k = window or w.size
    pos = np.arange(k - w.size + 1, k + 1, dtype=float)
    ok = ~np.isnan(w)
    if what == "resi" and np.isnan(w[-1]):
        return np.nan
    x, y = pos[ok], w[ok]
    if x.size < 2:
        return np.nan
    xm, ym = x.mean(), y.mean()
    sxx, sxy = ((x - xm) ** 2).sum(), ((x - xm) * (y - ym)).sum()
    slope = sxy / sxx
    if what == "slope":
        return float(slope)
    if what == "resi":
        return float(w[-1] - (slope * k + (ym - slope * xm)))
    syy = ((y - ym) ** 2).sum()
    return float(sxy * sxy / (sxx * syy)) if syy > 0 else np.nan


def _ev(node: Node, data: Data, mode: Mode, cache: dict[str, Frame]) -> Frame | float:
    if isinstance(node, Num):
        return node.value
    if isinstance(node, Field):
        if node.name not in data:
            raise ExprError(f"no field ${node.name}; have {', '.join('$' + k for k in sorted(data))}")
        return data[node.name].astype(float)
    assert isinstance(node, Call)
    key = str(node)
    if key in cache:
        return cache[key]
    op, a = node.op, node.args
    if op in PAIR:
        x, y = _ev(a[0], data, mode, cache), _ev(a[1], data, mode, cache)
        if not isinstance(x, pd.DataFrame) and not isinstance(y, pd.DataFrame):
            raise ExprError(f"{node}: at least one side must reference a field")
        out = PAIR[op](x, y)
    elif op == "If":
        c, x, y = (_ev(z, data, mode, cache) for z in a)
        out = (x if isinstance(x, pd.DataFrame) else c * 0 + x).where(c.astype(bool), y)
    elif op in CROSS:
        out = _ev(a[0], data, mode, cache).rank(axis=1, pct=True)
    else:
        x = _ev(a[0], data, mode, cache)
        if not isinstance(x, pd.DataFrame):
            raise ExprError(f"{node}: its input must reference a field")
        out = _unary(node, x, data, mode, cache)
    cache[key] = out
    return out


def _unary(node: Call, x: Frame, data: Data, mode: Mode, cache: dict[str, Frame]) -> Frame:
    op = node.op
    if op in ELEM:
        return ELEM[op](x)
    if op in PAIR_ROLL:
        y = _ev(node.args[1], data, mode, cache)
        n = int(_window(node, 2))
        if n == 0:
            raise ExprError(f"{node}: an expanding window depends on where the data starts; refused")
        rx, ry = x.rolling(n, min_periods=mode.minp(n)), y.rolling(n, min_periods=mode.minp(n))
        out = rx.corr(y) if op == "Corr" else rx.cov(y)
        if op == "Corr":   # qlib: a (near-)constant side gives NaN, not a spurious +-1
            flat = np.isclose(rx.std(), 0, atol=2e-05) | np.isclose(ry.std(), 0, atol=2e-05)
            out = out.mask(flat)
        return out
    n_raw = _window(node, 1)
    if op == "EMA" and 0 < n_raw < 1:
        return x.ewm(alpha=n_raw, min_periods=1 if mode.compat else int(math.ceil(1 / n_raw))).mean()
    n = int(n_raw)
    if n == 0:
        if op == "Ref" and mode.compat:
            return x.iloc[[0]].reindex(x.index, method="ffill") if len(x) else x
        raise ExprError(f"{node}: N = 0 depends on where the data starts (expanding / first value); refused")
    if op == "Ref":
        return x.shift(n)
    if op == "Delta":
        return x - x.shift(n)
    if op in ROLL_PANDAS:
        return getattr(x.rolling(n, min_periods=mode.minp(n)), ROLL_PANDAS[op])()
    if op == "Quantile":
        return x.rolling(n, min_periods=mode.minp(n)).quantile(node.args[2].value)
    if op == "Rank":
        return x.rolling(n, min_periods=mode.minp(n)).rank(pct=True)
    if op == "IdxMax":
        return _apply(x, n, mode, lambda w: w.argmax() + 1)
    if op == "IdxMin":
        return _apply(x, n, mode, lambda w: w.argmin() + 1)
    if op == "Mad":
        return _apply(x, n, mode, lambda w: np.mean(np.abs(w[~np.isnan(w)] - w[~np.isnan(w)].mean())))
    if op == "WMA":
        def wma(w: np.ndarray) -> float:
            wt = np.arange(1, len(w) + 1, dtype=float)
            return float(np.nanmean(wt / wt.sum() * w))
        return _apply(x, n, mode, wma)
    if op == "EMA":
        return x.ewm(span=n, min_periods=mode.minp(n)).mean()
    if op in ("Slope", "Rsquare", "Resi"):
        what = {"Slope": "slope", "Rsquare": "rsquare", "Resi": "resi"}[op]
        return _per_column(x, lambda y: _linreg(y, n, mode.compat, what))
    raise ExprError(f"operator {op} has no evaluator")  # pragma: no cover - ARITY and this list agree


def evaluate(expr: str | Node, data: Data, *, compat: bool = False) -> Frame:
    """Evaluate on (date x ticker) frames that share one index and one set of columns.

    The value on date t uses rows up to and including t, and nothing after: every operator is a backward window
    or a lag >= 0. ``test_expr.py`` checks this by truncation for every operator.
    """
    node = parse(expr) if isinstance(expr, str) else expr
    frames = list(data.values())
    if frames and not all(f.index.equals(frames[0].index) and list(f.columns) == list(frames[0].columns)
                          for f in frames):
        raise ExprError("every field must share one date index and one set of tickers")
    out = _ev(node, data, Mode(compat), {})
    if not isinstance(out, pd.DataFrame):
        raise ExprError(f"{node} is a constant; a factor must reference a field")
    return out.replace([np.inf, -np.inf], np.nan) if not compat else out

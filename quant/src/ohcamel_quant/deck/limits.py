"""The Flight Deck's limits: the viewer's policy, evaluated against the book.

Limits are policy, not market data, so they have defaults and the page lets a
viewer edit them. Every threshold is given as a DECIMAL fraction -- of equity
for the weight kinds and the drawdown, of notional for the money kinds -- and
the money kinds are evaluated in dollars, so the targeting computer's readout
reads in the unit a desk would say out loud.

An evaluation has the OCaml engine's wire shape (``lib/server.ml``
``json_of_breach``): ``name, scope, unit, observed, threshold, excess,
breached, utilisation``, so one layout function draws all three of the deck's
sources. A limit whose input could not be computed is returned in
``unevaluated`` with the reason -- never evaluated against a stand-in.

Pure: no I/O. The router supplies the facts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

Kind = Literal["gross", "net", "name", "var", "es", "drawdown", "day_loss"]
KINDS: tuple[str, ...] = ("gross", "net", "name", "var", "es", "drawdown", "day_loss")
MONEY_KINDS = frozenset({"var", "es", "day_loss"})


@dataclass(frozen=True)
class Limit:
    name: str
    kind: str
    threshold: float          # decimal fraction (of equity, or of notional for money kinds)
    ticker: str | None = None  # only for kind "name": one holding instead of the largest

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "kind": self.kind, "threshold": self.threshold}
        if self.ticker:
            d["ticker"] = self.ticker
        return d


DEFAULT_LIMITS: tuple[Limit, ...] = (
    Limit("gross-cap", "gross", 1.50),
    Limit("net-cap", "net", 1.10),
    Limit("name-cap", "name", 0.45),
    Limit("var-cap", "var", 0.02),
    Limit("es-cap", "es", 0.03),
    Limit("dd-cap", "drawdown", 0.15),
    Limit("day-loss", "day_loss", 0.015),
)


@dataclass
class Facts:
    """What the book is right now. ``None`` means "could not be computed", and
    ``reasons[key]`` says why."""

    notional: float
    live_weights: dict[str, float]
    var: float | None = None        # 1-day VaR, fraction of equity
    es: float | None = None
    drawdown: float | None = None   # >= 0, fraction below the peak
    day_pnl: float | None = None    # fraction of equity, signed
    reasons: dict[str, str] = field(default_factory=dict)


def _breach(limit: Limit, scope: str, unit: str, observed: float, threshold: float) -> dict[str, Any]:
    return {
        "name": limit.name, "kind": limit.kind, "scope": scope, "unit": unit,
        "observed": observed, "threshold": threshold, "excess": observed - threshold,
        "breached": observed > threshold, "utilisation": observed / threshold,
    }


def evaluate(limits: list[Limit] | tuple[Limit, ...], facts: Facts) -> tuple[list[dict], list[dict]]:
    """Evaluate each limit. Returns ``(evaluated, unevaluated)``, both in the
    order given; an unevaluated entry is ``{name, kind, reason}``."""
    done: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    w = facts.live_weights

    def skip(lim: Limit, reason: str) -> None:
        skipped.append({"name": lim.name, "kind": lim.kind, "reason": reason})

    for lim in limits:
        if lim.kind not in KINDS:
            raise ValueError(f"unknown limit kind {lim.kind!r}; one of {', '.join(KINDS)}")
        if not (math.isfinite(lim.threshold) and lim.threshold > 0):
            raise ValueError(f"limit {lim.name!r}: threshold must be a positive number")

        if lim.kind == "gross":
            done.append(_breach(lim, "portfolio", "fraction", sum(abs(x) for x in w.values()), lim.threshold))
        elif lim.kind == "net":
            done.append(_breach(lim, "portfolio", "fraction", abs(sum(w.values())), lim.threshold))
        elif lim.kind == "name":
            if lim.ticker:
                t = lim.ticker.upper()
                if t not in w:
                    skip(lim, f"{t} is not in the book")
                    continue
                done.append(_breach(lim, f"name:{t}", "fraction", abs(w[t]), lim.threshold))
            else:
                if not w:
                    skip(lim, "the book holds nothing")
                    continue
                t = max(w, key=lambda k: (abs(w[k]), k))
                done.append(_breach(lim, f"largest name:{t}", "fraction", abs(w[t]), lim.threshold))
        elif lim.kind == "drawdown":
            if facts.drawdown is None:
                skip(lim, facts.reasons.get("drawdown", "drawdown could not be computed"))
                continue
            done.append(_breach(lim, "portfolio", "fraction", facts.drawdown, lim.threshold))
        else:  # the money kinds, in dollars
            value = {"var": facts.var, "es": facts.es,
                     "day_loss": None if facts.day_pnl is None else max(0.0, -facts.day_pnl)}[lim.kind]
            if value is None:
                skip(lim, facts.reasons.get(lim.kind, f"{lim.kind} could not be computed"))
                continue
            done.append(_breach(lim, "portfolio", "money", value * facts.notional,
                                lim.threshold * facts.notional))
    return done, skipped

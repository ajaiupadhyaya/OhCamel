"""What a page says before its JavaScript runs.

The site is a client-rendered app, and FastAPI answers every page with the same
``index.html``, so a crawler or a link preview would see an empty ``#root`` under
one title. This module fills that shell per path: the page's title, description,
canonical URL and preview tags in ``<head>``, and a short plain description of the
page inside ``#root``, which React replaces when it mounts.

The copy states what a page is and never a figure. A number belongs to the page,
which reads it from data; one typed here would go stale in a shell nobody looks at.

``ROUTES`` mirrors ``quant/web/src/lib/routes.ts``, and test_seo.py holds the two
equal, so a page added there fails until it has a description here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from xml.sax.saxutils import escape as xml_escape

SITE = "OhCamel Quant"
PREVIEW_WIDTH = 1200
PREVIEW_HEIGHT = 630
PREVIEW_ALT = "The machine: OhCamel's dependency graph, rings narrowing from the world's data to one book."

ABOUT = (
    "OhCamel is a quantitative finance system. The public site is a research workstation on real "
    "market data: risk, portfolio construction, backtesting, options, rates and company fundamentals, "
    "with scheduled research products computed overnight. Behind it, a reactive risk engine written "
    "in OCaml on Jane Street's Incremental recomputes only what a market tick touches, and runs a "
    "paper-trading desk."
)


@dataclass(frozen=True)
class Route:
    path: str
    title: str
    description: str
    to: str = ""

    @property
    def link(self) -> str:
        return self.to or self.path


ROUTES: tuple[Route, ...] = (
    Route("/", "The machine",
          "OhCamel drawn as its own dependency graph: the world's data, the warehouse, the day's jobs, "
          "the products and the strategies, narrowing to one book."),
    Route("/tape", "The Tape",
          "The day on one sheet: indices, the yield curve, the regime, the risk atlas, the strategy farm "
          "and data freshness."),
    Route("/markets", "Markets",
          "Indices, sectors, rates, credit, commodities and volatility, read from real market data with "
          "each figure's source."),
    Route("/risk", "Risk",
          "Value at risk and expected shortfall across models, Euler decomposition, coverage backtests, "
          "GARCH, liquidity and a Monte Carlo atlas."),
    Route("/portfolio", "Portfolio Lab",
          "Build a portfolio or import a fund's latest SEC 13F, then read its performance, factor "
          "exposures and stress losses."),
    Route("/optimize", "Optimizer",
          "Mean-variance, risk parity, hierarchical risk parity and Black-Litterman portfolios with "
          "constraints and walk-forward tests."),
    Route("/research", "Strategy Lab",
          "Backtests of strategies from the literature with realistic costs and overfitting diagnostics "
          "that can say no."),
    Route("/options", "Volatility",
          "Option chains, implied volatility smiles, the fitted surface, risk-neutral densities and "
          "payoff analysis."),
    Route("/macro", "Rates & Macro",
          "The Treasury yield curve, its history and factors, recession probabilities and macro regimes "
          "from public data."),
    Route("/company/:ticker", "Company",
          "Financial statements from SEC filings, ratios, quality scores and an interactive discounted "
          "cash flow model.", "/company"),
    Route("/deck", "Flight Deck",
          "The book's limits, risk and data feeds as live instruments, with the book's own numbers kept "
          "private."),
    Route("/system", "System",
          "How the system is run: compute, the engine, methodology and the ledger of what was built and "
          "what was cut."),
    Route("/compute", "Compute",
          "The host, the job queue, the schedule and the kernel benchmarks behind the site's overnight "
          "research."),
    Route("/engine", "Live Engine",
          "The OCaml risk engine on Jane Street's Incremental: what one market tick really recomputes, "
          "measured from the engine itself."),
    Route("/methodology", "Methodology",
          "Every model on the site written down: formulas, assumptions, the choices made and the "
          "references behind them."),
    Route("/ledger", "Ledger",
          "What was built, what was cut, what stays advisory, the known limits and what the owner still "
          "holds."),
    Route("/ticker/:ticker", "Ticker",
          "One name's price history, volume, return statistics and realized volatility, from real daily "
          "bars.", "/ticker/SPY"),
)
ROUTES_BY_PATH = {r.path: r for r in ROUTES}

# A path segment that can be a ticker: letters, digits, the dot of BRK.B, the caret
# of ^VIX and the hyphen and equals of vendor symbols. Anything else is not a page.
_TICKER = re.compile(r"[A-Za-z0-9.^=-]{1,12}")


@dataclass(frozen=True)
class Page:
    route: Route
    path: str
    title: str


def resolve(path: str) -> Page | None:
    """The page at a request path (no leading slash), or None if there is none."""
    # React Router matches case-insensitively, so /Risk is the risk page here too.
    clean = "/" + path.strip("/")
    hit = ROUTES_BY_PATH.get(clean.lower())
    if hit is not None and ":" not in hit.path:
        return Page(hit, clean.lower(), hit.title)
    head, _, tail = clean[1:].partition("/")
    head = head.lower()
    if head not in ("company", "ticker"):
        return None
    route = ROUTES_BY_PATH[f"/{head}/:ticker"]
    if not tail:
        return Page(route, f"/{head}", route.title)
    if not _TICKER.fullmatch(tail):
        return None
    ticker = tail.upper()
    return Page(route, f"/{head}/{ticker}", f"{ticker} · {route.title}")


def _head(page: Page | None, base_url: str) -> str:
    title = f"{page.title} · {SITE}" if page else f"Not found · {SITE}"
    description = page.route.description if page else ABOUT
    t, d = escape(title), escape(description)
    image = escape(f"{base_url}/og.png")
    lines = [f"<title>{t}</title>", f'<meta name="description" content="{d}" />']
    if page is None:
        lines.append('<meta name="robots" content="noindex" />')
        return "\n    ".join(lines)
    url = escape(f"{base_url}{'' if page.path == '/' else page.path}" or base_url)
    alt = escape(PREVIEW_ALT)
    lines += [
        f'<link rel="canonical" href="{url}" />',
        '<meta property="og:type" content="website" />',
        f'<meta property="og:site_name" content="{SITE}" />',
        f'<meta property="og:title" content="{t}" />',
        f'<meta property="og:description" content="{d}" />',
        f'<meta property="og:url" content="{url}" />',
        f'<meta property="og:image" content="{image}" />',
        f'<meta property="og:image:width" content="{PREVIEW_WIDTH}" />',
        f'<meta property="og:image:height" content="{PREVIEW_HEIGHT}" />',
        f'<meta property="og:image:alt" content="{alt}" />',
        '<meta name="twitter:card" content="summary_large_image" />',
        f'<meta name="twitter:title" content="{t}" />',
        f'<meta name="twitter:description" content="{d}" />',
        f'<meta name="twitter:image" content="{image}" />',
    ]
    return "\n    ".join(lines)


def _body(page: Page | None) -> str:
    heading = escape(page.title) if page else "Page not found"
    lede = escape(page.route.description) if page else "There is no page at this address."
    links = "".join(
        f'<li><a href="{escape(r.link)}">{escape(r.title)}</a></li>' for r in ROUTES
    )
    return (
        '<main class="oc-static">'
        f"<p>{SITE}</p><h1>{heading}</h1><p>{lede}</p><p>{escape(ABOUT)}</p>"
        f'<nav aria-label="Pages"><ul>{links}</ul></nav>'
        "</main>"
    )


def _fill(html: str, region: str, content: str) -> str:
    # A function, not a replacement string: copy never reaches re's escapes.
    return re.sub(rf"<!--seo:{region}-->.*?<!--/seo:{region}-->", lambda _: content, html, count=1,
                  flags=re.S)


def render(shell: str, page: Page | None, base_url: str) -> str:
    """The shell with one page's head and fallback; None is the not-found page."""
    return _fill(_fill(shell, "head", _head(page, base_url)), "body", _body(page))


def sitemap(base_url: str) -> str:
    urls = "".join(
        f"<url><loc>{xml_escape(base_url + ('' if r.link == '/' else r.link))}</loc></url>" for r in ROUTES
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n'
    )

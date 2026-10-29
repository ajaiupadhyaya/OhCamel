"""Build OhCamel's lineage graph from the repo's own config and the public API snapshots.

Every edge is a real dependency: a schedule, a constituent list, an artifact's
provenance, the INC-01 roster, or the example book's limits.

usage: quant/.venv/bin/python graph.py <data_dir> <out.json>
"""
import csv
import json
import math
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

from ohcamel_quant.config import REPO_ROOT
from ohcamel_quant.data.market import TREASURY_SERIES
from ohcamel_quant.jobs.cron import fire_times
from ohcamel_quant.jobs.schedules import load_schedules
from ohcamel_quant.signals.roster import load_roster
from ohcamel_quant.warehouse.ingest import minute, options

D, OUT = sys.argv[1], sys.argv[2]
NY = ZoneInfo("America/New_York")
TAU = 2 * math.pi
Q = REPO_ROOT / "quant" / "src" / "ohcamel_quant"


def load(name):
    with open(f"{D}/{name}") as f:
        return json.load(f)


def tab(name):
    d = load(name)
    return d["data"], len(d["index"])


nodes, index, edges = [], {}, []


def node(id_, nk, label, name, ring, ang, **meta):
    index[id_] = len(nodes)
    nodes.append({"id": id_, "k": nk, "l": label, "n": name, "r": ring, "a": round(ang % TAU, 5), "m": meta})
    return id_


def edge(a, b, kind):
    if a in index and b in index and a != b:
        edges.append([index[a], index[b], kind])


def clock(h):  # hours since midnight -> angle, midnight at the top, clockwise
    return h / 24 * TAU


# ------------------------------------------------------------------ the world: tickers, macro, factors, filers
uni = json.load(open(Q / "universes.json"))
etfs, etf_names, etf_unis = [], {}, {}
for uk, u in uni["universes"].items():
    for m in u["members"]:
        t = m["ticker"]
        if t not in etf_names:
            etfs.append(t)
            etf_names[t] = m["name"]
        etf_unis.setdefault(t, []).append(u["label"])
names = {}
for f in ("sp500.csv", "ndx100.csv"):
    with open(Q / "warehouse" / "constituents" / f) as fh:
        for row in csv.DictReader(r for r in fh if not r.startswith("#")):
            names.setdefault(row["ticker"].strip(), row["name"].strip())
equities = [t for t in names if t not in etf_names]
pack = load("../data.json")
wh_order = pack["wh"]["tickers"]
rank = {t: i for i, t in enumerate(wh_order)}
equities.sort(key=lambda t: (rank.get(t, 10_000), t))
series_cfg = json.load(open(Q / "macro" / "series.json"))["series"]
fred = list(dict.fromkeys([s["id"] for s in series_cfg] + list(TREASURY_SERIES)))
fred_names = {s["id"]: s["name"] for s in series_cfg}
for k, v in TREASURY_SERIES.items():
    fred_names.setdefault(k, f"Treasury yield, {v:g}-year" if v >= 1 else f"Treasury yield, {round(v * 12)}-month")
filers = load("artifacts.json")["filers13f"]["filers"]

sections = [("etf", etfs), ("eq", equities), ("fred", fred), ("ff", ["FF5", "MOM"]), ("13f", [f["cik"] for f in filers])]
GAP = 6
total = sum(len(s) for _, s in sections) + GAP * len(sections)
ang_of, pos = {}, 0
section_span = {}
for key, items in sections:
    a0 = pos
    for t in items:
        ang_of[(key, t)] = (pos + 0.5) / total * TAU + clock(0.6)
        pos += 1
    section_span[key] = [a0 / total * TAU + clock(0.6), pos / total * TAU + clock(0.6)]
    pos += GAP

# closes, returns, correlations for the readouts
closes = load("closes.json")
corr_q = np.frombuffer(__import__("base64").b64decode(pack["corr"]["q"]), dtype=np.int8).reshape(len(wh_order), len(wh_order)) / 127
fresh = load("freshness.json")["datasets"]


def ticker_meta(t):
    m = {"full": names.get(t) or etf_names.get(t)}
    if t in etf_unis:
        m["unis"] = etf_unis[t]
    c = closes.get(t)
    if c:
        px = [x for x in c["c"] if x is not None]
        r = np.diff(np.log(px))
        step = max(1, len(px) // 60)
        m["spark"] = [round(x / px[0], 4) for x in px[::step]]
        m["ret1y"] = round(px[-1] / px[0] - 1, 4)
        m["vol"] = round(float(r.std() * math.sqrt(252)), 4)
        m["last"] = round(px[-1], 2)
    if t in rank:
        row = corr_q[rank[t]]
        top = np.argsort(-row)[1:7]
        m["peers"] = [[wh_order[j], round(float(row[j]), 2)] for j in top]
    return m


for t in etfs:
    node(f"d:{t}", "series", t, etf_names[t], 7, ang_of[("etf", t)], track="daily", **ticker_meta(t))
for t in equities:
    node(f"d:{t}", "series", t, names[t], 7, ang_of[("eq", t)], track="daily", **ticker_meta(t))
    node(f"s:{t}", "series", t, f"{names[t]}: SEC filings", 6.6, ang_of[("eq", t)], track="filings")
for t in fred:
    node(f"f:{t}", "series", t, fred_names.get(t, t), 7, ang_of[("fred", t)], track="macro")
for t, nm in (("FF5", "Fama–French five factors, daily"), ("MOM", "Momentum factor, daily")):
    node(f"ff:{t}", "series", t, nm, 7, ang_of[("ff", t)], track="factors")
for f in filers:
    node(f"13f:{f['cik']}", "series", f["manager"].split()[-1].upper(), f"{f['name']} (13F)", 7, ang_of[("13f", f["cik"])], track="13f", manager=f["manager"])
for t in minute.MINUTE_TICKERS:
    a = ang_of.get(("eq", t)) or ang_of.get(("etf", t))
    if a is not None:
        node(f"m:{t}", "series", t, f"{t}: one-minute bars", 6.25, a, track="minute")
for t in options.OPTION_UNDERLYINGS:
    a = ang_of.get(("eq", t)) or ang_of.get(("etf", t))
    if a is not None:
        node(f"o:{t}", "series", t, f"{t}: option chain", 5.9, a, track="options")

# ------------------------------------------------------------------ the clock: every scheduled job at its time of day
sched = load_schedules()
day = date(2026, 10, 28)
fires, kinds = [], {}
for e in sched.entries:
    ny = lambda z: z.astimezone(NY).hour + z.astimezone(NY).minute / 60
    hrs = [ny(ft) for ft in fire_times(e.parsed, day)]
    if not hrs:  # weekly or monthly: place at its time of day on the day it does fire
        for k in range(1, 32):
            try:
                d2 = date(2026, 11, k)
            except ValueError:
                break
            ft = fire_times(e.parsed, d2)
            if ft:
                hrs = [ny(x) for x in ft]
                break
    when = "daily" if e.parsed.dow_star and e.parsed.dom_star else ("weekdays" if e.parsed.dom_star and len(e.parsed.dows) == 5 else ("monthly" if not e.parsed.dom_star else "weekly"))
    for h in hrs:
        fires.append([e.kind, round(h, 3)])
    k = kinds.setdefault(e.kind, {"fires": [], "cron": [], "heavy": e.heavy, "mem": e.mem_class, "when": set()})
    k["fires"] += hrs
    k["cron"].append(e.cron)
    k["when"].add(when)

PRETTY = {
    "ops.selftest": "Self-test", "ingest.fred": "FRED cache", "ingest.universes": "Universes", "ingest.bars_daily": "Daily bars",
    "ingest.bars_minute": "Minute bars", "ingest.fred_warehouse": "Macro series", "ingest.factors": "Factors", "ingest.sec_facts": "SEC filings",
    "ingest.holdings_13f": "13F holdings", "ingest.option_snapshots": "Option chains", "risk.mc_atlas": "Risk atlas", "risk.mc_intraday": "Intraday risk",
    "vol.forecast_league": "Vol forecast league", "cov.league": "Covariance league", "farm.sweep": "Strategy farm", "vol.surface_history": "Vol surface",
    "models.xs_lgbm": "Cross-section model", "regime.hmm": "Regime model", "ops.data_integrity": "Data integrity", "kronos.shadow": "Kronos shadow",
    "factors.screen": "Factor screen", "signals.daily": "Daily signals", "signals.overlay": "Exposure overlay", "desk.report": "Desk report",
}
for kind, k in kinds.items():
    fs = sorted(k["fires"])
    # a job that fires many times a day sits at the middle of its run of fires
    h = fs[0] if len(fs) == 1 else (fs[0] + fs[-1]) / 2 if fs[-1] - fs[0] < 12 else fs[-1]
    node(f"j:{kind}", "job", PRETTY.get(kind, kind), PRETTY.get(kind, kind), 4.25, clock(h), kind=kind, fires=[round(x, 3) for x in fs],
         cron=k["cron"], heavy=k["heavy"], mem=k["mem"], when=sorted(k["when"]))

# ------------------------------------------------------------------ the feeds, outside everything
FEEDS = [
    ("prices", "Prices", "Yahoo, Alpaca, Stooq", "ingest.bars_daily", [f"d:{t}" for t in etfs + equities], fresh["bars_daily"]),
    ("minute", "Minute bars", "Alpaca (IEX)", "ingest.bars_minute", [f"m:{t}" for t in minute.MINUTE_TICKERS], fresh["bars_minute"]),
    ("cboe", "Option chains", "Cboe, 15-minute delay", "ingest.option_snapshots", [f"o:{t}" for t in options.OPTION_UNDERLYINGS], fresh["option_snapshots"]),
    ("fred", "Rates & macro", "FRED", "ingest.fred_warehouse", [f"f:{t}" for t in fred], fresh["fred"]),
    ("french", "Factors", "Kenneth French", "ingest.factors", ["ff:FF5", "ff:MOM"], fresh["factors"]),
    ("sec", "Filings", "SEC EDGAR", "ingest.sec_facts", [f"s:{t}" for t in equities], fresh["sec_facts"]),
    ("sec13f", "13F holdings", "SEC EDGAR", "ingest.holdings_13f", [f"13f:{f['cik']}" for f in filers], fresh["holdings_13f"]),
]
for key, label, who, job, targets, fr in FEEDS:
    angs = [nodes[index[t]]["a"] for t in targets if t in index]
    a = math.atan2(sum(math.sin(x) for x in angs), sum(math.cos(x) for x in angs))
    if key == "sec":
        a += 0.18
    node(f"feed:{key}", "feed", label, f"{label} from {who}", 8.4, a, who=who, keys=fr["keys"], asof=fr["data_asof"], ok=fr["last_ok_at"], behind=fr["keys_behind"])
    edge(f"feed:{key}", f"j:{job}", "pull")
    for t in targets:
        edge(f"j:{job}", t, "write")
edge("feed:fred", "j:ingest.fred", "pull")

# ------------------------------------------------------------------ products: what each job publishes, and what it read
arts = load("artifacts.json")
PROD = {
    "risk.mc_atlas": ("P1", "Risk atlas"), "vol.forecast_league": ("P2", "Vol forecast league"), "cov.league": ("P3", "Covariance league"),
    "farm.sweep": ("P4", "Strategy farm"), "models.xs_lgbm": ("P5", "Cross-section model"), "regime.hmm": ("P6", "Regime model"),
    "vol.surface_history": ("P7", "Vol surface history"), "kronos.shadow": ("K", "Kronos shadow"), "ops.data_integrity": ("DI", "Data integrity"),
    "risk.mc_intraday": ("P1i", "Intraday risk"), "factors.screen": ("FS", "Factor screen"), "signals.daily": ("SD", "Daily signals"),
    "signals.overlay": ("SO", "Exposure overlay"), "desk.report": ("DR", "Desk report"),
}
for kind, (code, nm) in PROD.items():
    a = arts.get(kind, {})
    jn = nodes[index[f"j:{kind}"]]
    node(f"p:{kind}", "product", code, nm, 3.25, jn["a"], kind=kind, verdict=a.get("verdict"), detail=a.get("detail"), code=a.get("code"),
         asof=a.get("asof"), finished=a.get("finished"), cpu=a.get("cpu"), engine=a.get("engine"), notes=a.get("notes"), art=a.get("id"))
    edge(f"j:{kind}", f"p:{kind}", "publish")
roster = load_roster()
reads = {
    "risk.mc_atlas": [f"d:{t}" for t in arts["risk.mc_atlas"]["members"]],
    "risk.mc_intraday": [f"d:{t}" for t in arts["risk.mc_intraday"]["syms"]] + [f"m:{t}" for t in arts["risk.mc_intraday"]["syms"]],
    "vol.forecast_league": [f"d:{t}" for t in arts["vol.forecast_league"]["syms"]] + [f"m:{t}" for t in minute.MINUTE_TICKERS],
    "cov.league": [f"d:{t}" for t in arts["cov.league"]["syms"]],
    "farm.sweep": sorted({f"d:{s}" for c in roster.cells if c.producer == "signals.daily" for s in c.symbols}),
    "models.xs_lgbm": [f"d:{t}" for t in arts["models.xs_lgbm"]["syms"]],
    "regime.hmm": ["d:SPY", "f:VIXCLS"],
    "vol.surface_history": [f"o:{t}" for t in options.OPTION_UNDERLYINGS],
    "kronos.shadow": [f"d:{t}" for t in arts["kronos.shadow"]["syms"]],
    "ops.data_integrity": [f"d:{t}" for t in etfs + equities],
    "factors.screen": ["ff:FF5", "ff:MOM"],
}
for kind, ids in reads.items():
    for t in ids:
        edge(t, f"j:{kind}", "read")

# ------------------------------------------------------------------ results inside the products
def spread(center, n, width):
    return [center + (i - (n - 1) / 2) * width / max(1, n - 1) for i in range(n)] if n > 1 else [center]


s, n = tab("farm.sweep.cells.json")
lb, ln = tab("farm.sweep.leaderboard.json")
lead = {lb["cell_id"][i]: {k: lb[k][i] for k in ("verdict", "detail", "dsr_farm", "psr", "oos_sharpe_ann", "pbo", "n_combos")} for i in range(ln)}
farm_a = nodes[index["p:farm.sweep"]]["a"]
for (i, a) in zip(range(n), spread(farm_a, n, 0.95)):
    cid, strat, u = s["cell_id"][i], s["strategy"][i], s["universe"][i]
    L = lead.get(cid, {})
    node(f"c:{strat}@{u}", "result", f"{strat}@{u}", f"{strat.replace('_', ' ')} on {u.replace('_', ' ')}", 2.35, a,
         status=s["status"][i], reason=s["reason"][i], combos=s["n_combos"][i], **L)
    edge("p:farm.sweep", f"c:{strat}@{u}", "result")
ss, sn = tab("vol.forecast_league.summary.json")
pa = nodes[index["p:vol.forecast_league"]]["a"]
for i, a in zip(range(sn), spread(pa, sn, 0.22)):
    m = ss["model"][i]
    node(f"lm:{m}", "result", m.upper(), f"{m.upper()} volatility model", 2.35, a, mcs_rate=ss["mcs_rate"][i], mean_rank=ss["mean_rank"][i], names=ss["names"][i])
    edge("p:vol.forecast_league", f"lm:{m}", "result")
cl, cn = tab("cov.league.league.json")
est = {}
for i in range(cn):
    est.setdefault(cl["estimator"][i], {})[cl["universe"][i]] = {"vol": cl["realized_vol"][i], "rank": cl["rank"][i]}
pa = nodes[index["p:cov.league"]]["a"]
for (e, v), a in zip(sorted(est.items(), key=lambda kv: kv[1].get("ndx100", {}).get("rank", 9)), spread(pa, len(est), 0.36)):
    node(f"ce:{e}", "result", e.replace("_", " "), f"{e.replace('_', ' ')} covariance estimator", 2.35, a, **{u: v[u] for u in v})
    edge("p:cov.league", f"ce:{e}", "result")
at, an = tab("risk.mc_atlas.summary.json")
pa = nodes[index["p:risk.mc_atlas"]]["a"]
rows = [i for i in range(an) if at["alpha"][i] == 0.99]
for i, a in zip(rows, spread(pa, len(rows), 0.22)):
    nm = f"{'FHS' if at['method'][i] == 'fhs' else 't-copula'} {at['horizon'][i]}-day"
    node(f"am:{at['method'][i]}:{at['horizon'][i]}", "result", nm, f"{nm} VaR and ES at 99%", 2.35, a,
         var=at["var"][i], es=at["es"][i], var_usd=at["var_usd"][i], es_usd=at["es_usd"][i], paths=at["n_paths"][i])
    edge("p:risk.mc_atlas", f"am:{at['method'][i]}:{at['horizon'][i]}", "result")

# ------------------------------------------------------------------ the desk: the roster, the overlay, the book, its limits
edge("p:farm.sweep", "j:signals.daily", "read")
edge("p:cov.league", "j:signals.overlay", "read")
edge("p:vol.forecast_league", "j:signals.overlay", "read")
node("p:research", "product", "A01", "Research service (EXP-A01)", 3.25, clock(16.9), kind="research.service", verdict="FAIL",
     detail="EXP-A01: BOTH STRATEGIES FAIL A CHARTER GATE", engine="python")
node("book", "book", "THE BOOK", "The paper book at Alpaca", 0, 0, strategies=len(roster.cells), incubation_cap=roster.incubation_cap,
     canary_cap=roster.canary_cap, vol_target=getattr(roster, "vol_target", None), positions=5, kill_armed=True)
strats = roster.cells
for c, a in zip(strats, [i / len(strats) * TAU + clock(19.5) for i in range(len(strats))]):
    node(f"st:{c.slug}", "strategy", c.slug.replace("farm_", "").replace("_", " "), c.slug, 1.45, a, producer=c.producer, strategy=c.strategy,
         universe=c.universe, symbols=list(c.symbols), capital=c.capital_fraction)
    if c.producer == "signals.daily":
        edge(f"c:{c.strategy}@{c.universe}", f"st:{c.slug}", "roster")
        edge("p:signals.daily", f"st:{c.slug}", "signal")
    else:
        edge("p:research", f"st:{c.slug}", "signal")
    edge(f"st:{c.slug}", "book", "trade")
edge("p:signals.overlay", "book", "scale")
edge("book", "j:desk.report", "read")
LIMITS = [("aapl-cap", "AAPL", "$150k gross", ["d:AAPL"]), ("nvda-cap", "NVDA", "$60k gross", ["d:NVDA"]), ("tech-cap", "tech sector", "$260k gross", []),
          ("energy-cap", "energy sector", "$150k gross", []), ("book-cap", "whole book", "$500k gross", []), ("var-cap", "whole book", "VaR $12k", []),
          ("dd-cap", "whole book", "drawdown 2%", [])]
for (nm, scope, what, src), a in zip(LIMITS, [i / len(LIMITS) * TAU + clock(1) for i in range(len(LIMITS))]):
    node(f"lim:{nm}", "limit", nm, f"{nm}: {scope}, {what}", 0.78, a, scope=scope, what=what, trips=nm in ("book-cap", "var-cap", "dd-cap"))
    edge(f"lim:{nm}", "book", "limit")
    for t in src:
        edge(t, f"lim:{nm}", "read")
node("kill", "kill", "KILL SWITCH", "Kill switch: armed", 0.78, clock(13), trips=["book-cap", "var-cap", "dd-cap"])
for nm in ("book-cap", "var-cap", "dd-cap"):
    edge(f"lim:{nm}", "kill", "trip")
edge("kill", "book", "halt")

# ------------------------------------------------------------------ the last day of work, for replay
jobs = load("jobs.200.json")["jobs"]
done = sorted([j for j in jobs if j["started_at"] and j["finished_at"]], key=lambda j: j["started_at"])
end = max(datetime.fromisoformat(j["finished_at"].replace("Z", "+00:00")) for j in done)
day_runs = [j for j in done if (end - datetime.fromisoformat(j["started_at"].replace("Z", "+00:00"))).total_seconds() < 86400]
runs = [{"kind": j["kind"], "start": j["started_at"], "end": j["finished_at"], "cpu": round(j["cpu_seconds"] or 0, 1), "state": j["state"],
         "msg": j.get("message")} for j in day_runs]

# ------------------------------------------------------------------ extras for the readings
extras = {
    "farm": {"trials": load("farm.sweep.trials.json")["rows"], "note": load("farm.sweep.cells.json")["notes"]},
    "xs": pack["xs"], "surface": {"atm30": pack["surface"]["atm30"], "contracts": pack["surface"]["contracts"], "spot": pack["surface"]["spot"]},
    "regime": {"last": pack["regime"]["dates"][-1], "p_high": pack["regime"]["high"][-1], "weeks": len(pack["regime"]["dates"])},
    "kernels": load("kernels.json")["kernels"], "host": {"cpus": 2},
    "sections": section_span, "fires": fires, "snapshot": "2026-10-29T03:15:00Z",
}
out = {"nodes": nodes, "edges": edges, "runs": runs, "x": extras}
with open(OUT, "w") as fh:
    json.dump(out, fh, separators=(",", ":"))
from collections import Counter
print("nodes", len(nodes), Counter(n["k"] for n in nodes))
print("edges", len(edges), Counter(e[2] for e in edges))
print("runs", len(runs), "bytes", len(json.dumps(out, separators=(",", ":"))))
missing = [k for k in PROD if f"j:{k}" not in index]
print("products without a job:", missing)

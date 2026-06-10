#!/usr/bin/env python3
"""
Meridian Alpha Capital — Quant Research Engine
================================================
Generates the full analytics payload consumed by the website.

Two data modes:

  REAL (default)  — daily adjusted closes from Yahoo Finance via data.py:
                    SPY (equity), TLT (bonds), GLD (gold), KO/PEP (pair),
                    PUTW (put-write, used directly by the vol sleeve).
                    Window is bounded by PUTW inception (~Feb 2016).
  SIMULATED       — regime-switching simulation calibrated to realistic
                    historical statistics (used with --simulated, or as
                    automatic fallback when the network is unavailable).

Pipeline (identical in both modes):
  backtests of five strategies (stat-arb pairs, trend, mean reversion,
  vol premium, risk-parity flagship) -> institutional metric suite ->
  10,000-path Monte Carlo -> allocation engine + committee notes.

Pure stdlib. Deterministic Monte Carlo via fixed seed.

Backtested results — on real or simulated data — do not guarantee
future returns. Not investment advice.

Usage:  python3 engine.py                # real data (falls back to sim)
        python3 engine.py --simulated    # force simulation
"""

import argparse
import json
import math
import os
import random

SEED = 20260609
TRADING_DAYS = 252
YEARS = 15
N_DAYS = TRADING_DAYS * YEARS
START_YEAR = 2010
DATES = None          # set in real mode: ISO date per return observation

rng = random.Random(SEED)


# ----------------------------------------------------------------------
# 1. Market simulation: regime-switching multi-asset daily returns
# ----------------------------------------------------------------------

# Regime schedule (start_year_frac, end_year_frac, mu_annual, vol_annual)
# Calibrated to the texture of 2010-2025 US equities.
EQUITY_REGIMES = [
    (0.0, 1.4, 0.13, 0.14),   # 2010-2011 recovery
    (1.4, 1.7, -0.35, 0.28),  # 2011 debt-ceiling shock
    (1.7, 5.5, 0.16, 0.12),   # 2012-2015 QE bull
    (5.5, 5.8, -0.40, 0.27),  # 2015 China deval shock
    (5.8, 8.7, 0.15, 0.11),   # 2016-2018 melt-up
    (8.7, 9.0, -0.55, 0.26),  # 2018 Q4 selloff
    (9.0, 10.1, 0.24, 0.13),  # 2019 rally
    (10.1, 10.28, -2.20, 0.55),# 2020 COVID crash
    (10.28, 12.0, 0.36, 0.20),# 2020-2021 stimulus bull
    (12.0, 13.0, -0.22, 0.22),# 2022 rate-hike bear
    (13.0, 15.0, 0.20, 0.13), # 2023-2025 AI bull
]


def regime_at(day):
    t = day / TRADING_DAYS
    for s, e, mu, vol in EQUITY_REGIMES:
        if s <= t < e:
            return mu, vol
    return 0.10, 0.15


def simulate_market():
    """Daily returns for: equity index, bonds, gold, and a cointegrated
    stock pair (A, B) sharing a common factor."""
    dt = 1.0 / TRADING_DAYS
    eq, bond, gold, sa, sb = [], [], [], [], []
    spread = 0.0          # log-spread of the pair, OU process
    vol_cluster = 1.0     # GARCH-ish volatility clustering multiplier
    prev_z = 0.0
    for d in range(N_DAYS):
        mu, vol = regime_at(d)
        vol_cluster = 0.97 * vol_cluster + 0.03 * (1.0 + 1.2 * (abs(prev_z) - 0.8))
        v = vol * max(min(vol_cluster, 1.5), 0.7)
        z = rng.gauss(0, 1)
        # fat tails: occasional jump
        if rng.random() < 0.008:
            z += rng.gauss(0, 1.8)
        # mild 1-day mean reversion observed in modern index returns
        r_eq = mu * dt + v * math.sqrt(dt) * (z - 0.08 * prev_z)
        prev_z = z
        eq.append(r_eq)

        # bonds: low vol, slightly negative correlation to equity shocks
        r_bd = 0.028 * dt + 0.05 * math.sqrt(dt) * (rng.gauss(0, 1) - 0.25 * z)
        bond.append(r_bd)

        # gold: crisis hedge behaviour
        r_gd = 0.05 * dt + 0.15 * math.sqrt(dt) * (rng.gauss(0, 1) - 0.15 * z) \
            + (0.0009 if v > 0.25 else 0.0)
        gold.append(r_gd)

        # cointegrated pair: both load on the equity factor; their entire
        # relative value is a stationary OU spread -> log(Pa/Pb) mean-
        # reverts, and the stat-arb sleeve harvests the discrepancy.
        new_spread = spread - 0.06 * spread + 0.012 * rng.gauss(0, 1)
        d_spread = new_spread - spread
        spread = new_spread
        common = 1.05 * r_eq
        sa.append(common + 0.5 * d_spread)
        sb.append(common - 0.5 * d_spread)
    return {"equity": eq, "bonds": bond, "gold": gold,
            "stock_a": sa, "stock_b": sb, "pair_spread_kappa": 0.06}


def to_prices(returns, start=100.0):
    p, out = start, []
    for r in returns:
        p *= (1.0 + r)
        out.append(p)
    return out


def load_real_market(years=12):
    """Real daily returns from Yahoo (via data.py). Sets the module
    period globals to match the actual data window."""
    global N_DAYS, YEARS, START_YEAR, DATES
    import data as marketdata
    syms = ["SPY", "TLT", "GLD", "KO", "PEP", "PUTW"]
    dates, px = marketdata.fetch_universe(syms, years=years)

    def rets(s):
        p = px[s]
        return [p[i] / p[i - 1] - 1.0 for i in range(1, len(p))]

    mkt = {"equity": rets("SPY"), "bonds": rets("TLT"), "gold": rets("GLD"),
           "stock_a": rets("KO"), "stock_b": rets("PEP"),
           "putw": rets("PUTW")}
    DATES = dates[1:]                       # one date per return
    N_DAYS = len(mkt["equity"])
    YEARS = N_DAYS / TRADING_DAYS
    START_YEAR = int(DATES[0][:4])
    return mkt


def _year_frac(iso):
    y, m, d = int(iso[:4]), int(iso[5:7]), int(iso[8:10])
    return y + (m - 1) / 12.0 + (d - 1) / 365.0


# ----------------------------------------------------------------------
# 2. Strategies
# ----------------------------------------------------------------------

COST_BPS = 0.0004  # 4bps per unit turnover, round-trip-ish


def sma(series, n, i):
    if i + 1 < n:
        return None
    return sum(series[i - n + 1:i + 1]) / n


def rolling_std(series, n, i):
    if i + 1 < n:
        return None
    w = series[i - n + 1:i + 1]
    m = sum(w) / n
    return math.sqrt(sum((x - m) ** 2 for x in w) / n)


def strat_stat_arb(mkt):
    """Pairs trading: z-score of log spread between cointegrated names.
    Long the cheap leg / short the rich leg when |z| > 1.5, exit at 0.25.
    This is the 'profit from pricing discrepancies' sleeve."""
    pa = to_prices(mkt["stock_a"])
    pb = to_prices(mkt["stock_b"])
    log_spread = [math.log(a / b) for a, b in zip(pa, pb)]
    rets, pos, prev_pos = [], 0.0, 0.0
    look = 60
    for i in range(N_DAYS):
        mu = sma(log_spread, look, i)
        sd = rolling_std(log_spread, look, i)
        if mu is None or sd is None or sd < 1e-9:
            rets.append(0.0)
            continue
        z = (log_spread[i] - mu) / sd
        if pos == 0.0:
            if z > 1.5:
                pos = -1.0   # short spread: short A, long B
            elif z < -1.5:
                pos = 1.0
        elif (pos == 1.0 and z >= -0.25) or (pos == -1.0 and z <= 0.25):
            pos = 0.0
        # apply yesterday's position to today's spread change (no lookahead)
        chg = (mkt["stock_a"][i] - mkt["stock_b"][i])
        gross = prev_pos * chg * 0.6   # 0.6 leverage on spread notional
        cost = abs(pos - prev_pos) * COST_BPS * 2  # two legs
        rets.append(gross - cost)
        prev_pos = pos
    return rets


def strat_trend(mkt):
    """Time-series momentum across equity/bonds/gold: long if price >
    10-month SMA, vol-targeted at 10% per sleeve."""
    sleeves = ["equity", "bonds", "gold"]
    prices = {k: to_prices(mkt[k]) for k in sleeves}
    rets = []
    prev_w = {k: 0.0 for k in sleeves}
    target_sleeve_vol = 0.07  # annualized, per sleeve
    for i in range(N_DAYS):
        day_r = sum(prev_w[k] * mkt[k][i] for k in sleeves)
        turn = 0.0
        if i % 5 == 0:  # rebalance weekly to limit turnover
            for k in sleeves:
                m = sma(prices[k], 105, i)
                sd = rolling_std(mkt[k], 60, i)
                if m is None or sd is None or sd < 1e-9:
                    w = 0.0
                else:
                    ann_vol = sd * math.sqrt(TRADING_DAYS)
                    above = prices[k][i] > m
                    # shorting equity rallies whipsaws; run asymmetric signs
                    sign = 1.0 if above else (-0.5 if k == "equity" else -1.0)
                    w = sign * min(target_sleeve_vol / ann_vol, 2.0)
                    w = max(min(w, 1.2), -1.2)
                turn += abs(w - prev_w[k])
                prev_w[k] = w
        rets.append(day_r - turn * COST_BPS)
    return rets


def strat_mean_rev(mkt):
    """Short-horizon reversal: fade outsized 1-day moves (standardized
    by trailing 20d vol), flat in high-vol crisis regimes."""
    rets, prev_pos = [], 0.0
    for i in range(N_DAYS):
        gross = prev_pos * mkt["equity"][i]
        sd20 = rolling_std(mkt["equity"], 20, i)
        vol60 = rolling_std(mkt["equity"], 60, i)
        if sd20 is None or sd20 < 1e-9 or vol60 is None:
            pos = 0.0
        elif vol60 * math.sqrt(TRADING_DAYS) > 0.30:
            pos = 0.0  # crisis filter
        else:
            z1 = mkt["equity"][i] / sd20  # today's standardized move
            if z1 < -1.3:
                pos = 0.7     # fade the down move
            elif z1 > 1.5:
                pos = -0.35   # fade the up move, half size
            else:
                pos = 0.0
        rets.append(gross - abs(pos - prev_pos) * COST_BPS)
        prev_pos = pos
    return rets


def strat_vol_premium(mkt):
    """Volatility risk premium, gated by trailing realized vol.
    Real mode: holds PUTW (put-write ETF) directly. Simulated mode:
    a carry model with convex crash losses."""
    rets, prev_pos = [], 0.0
    daily_carry = 0.085 / TRADING_DAYS
    putw = mkt.get("putw")
    for i in range(N_DAYS):
        vol60 = rolling_std(mkt["equity"], 60, i)
        ann_vol = (vol60 or 0.01) * math.sqrt(TRADING_DAYS)
        pos = 1.0 if ann_vol < 0.16 else (0.4 if ann_vol < 0.26 else 0.0)
        if putw is not None:
            gross = prev_pos * putw[i]
        else:
            shock = mkt["equity"][i]
            # convex loss when equity gaps down hard
            convex = -8.0 * max(-shock - 0.018, 0.0) ** 1.5
            gross = prev_pos * (daily_carry + 0.25 * shock + convex)
        rets.append(gross - abs(pos - prev_pos) * COST_BPS * 2)
        prev_pos = pos
    return rets


def blend(strats, weights):
    n = len(next(iter(strats.values())))
    out = []
    for i in range(n):
        out.append(sum(weights[k] * strats[k][i] for k in weights))
    return out


# ----------------------------------------------------------------------
# 3. Metrics
# ----------------------------------------------------------------------

RISK_FREE = 0.03


def max_drawdown(curve):
    peak, mdd, trough_i, peak_i, cur_peak_i = curve[0], 0.0, 0, 0, 0
    for i, v in enumerate(curve):
        if v > peak:
            peak, cur_peak_i = v, i
        dd = v / peak - 1.0
        if dd < mdd:
            mdd, trough_i, peak_i = dd, i, cur_peak_i
    return mdd, peak_i, trough_i


def metrics(rets, bench=None):
    n = len(rets)
    curve = to_prices(rets, 1.0)
    total = curve[-1] - 1.0
    yrs = n / TRADING_DAYS
    cagr = curve[-1] ** (1.0 / yrs) - 1.0
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    vol = math.sqrt(var * TRADING_DAYS)
    downside = [min(r, 0.0) for r in rets]
    dvar = sum(d ** 2 for d in downside) / n
    dvol = math.sqrt(dvar * TRADING_DAYS)
    sharpe = (cagr - RISK_FREE) / vol if vol > 0 else 0.0
    sortino = (cagr - RISK_FREE) / dvol if dvol > 0 else 0.0
    mdd, _, _ = max_drawdown(curve)
    calmar = cagr / abs(mdd) if mdd != 0 else 0.0
    wins = sum(1 for r in rets if r > 0)
    active = sum(1 for r in rets if abs(r) > 1e-12)
    hit = wins / active if active else 0.0
    gp = sum(r for r in rets if r > 0)
    gl = -sum(r for r in rets if r < 0)
    pf = gp / gl if gl > 0 else float("inf")
    srt = sorted(rets)
    var95 = srt[int(0.05 * n)]
    var99 = srt[int(0.01 * n)]
    cvar95 = sum(srt[:int(0.05 * n)]) / max(int(0.05 * n), 1)
    sk = (sum((r - mean) ** 3 for r in rets) / n) / (var ** 1.5) if var > 0 else 0.0
    ku = (sum((r - mean) ** 4 for r in rets) / n) / (var ** 2) - 3.0 if var > 0 else 0.0
    beta = alpha = corr = None
    if bench is not None:
        bm = sum(bench) / n
        cov = sum((rets[i] - mean) * (bench[i] - bm) for i in range(n)) / (n - 1)
        bvar = sum((b - bm) ** 2 for b in bench) / (n - 1)
        beta = cov / bvar if bvar > 0 else 0.0
        bcurve = to_prices(bench, 1.0)
        bcagr = bcurve[-1] ** (1.0 / yrs) - 1.0
        alpha = cagr - (RISK_FREE + beta * (bcagr - RISK_FREE))
        bstd = math.sqrt(bvar)
        corr = cov / (math.sqrt(var) * bstd) if var > 0 and bvar > 0 else 0.0
    # monthly aggregation for best/worst month
    monthly = []
    per_month = 21
    for s in range(0, n - per_month + 1, per_month):
        m = 1.0
        for r in rets[s:s + per_month]:
            m *= (1 + r)
        monthly.append(m - 1)
    pos_months = sum(1 for m in monthly if m > 0) / len(monthly)
    return {
        "totalReturn": round(total, 4), "cagr": round(cagr, 4),
        "vol": round(vol, 4), "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2), "maxDrawdown": round(mdd, 4),
        "calmar": round(calmar, 2), "hitRate": round(hit, 4),
        "profitFactor": round(pf, 2) if pf != float("inf") else 99.0,
        "var95": round(var95, 4), "var99": round(var99, 4),
        "cvar95": round(cvar95, 4), "skew": round(sk, 2),
        "kurtosis": round(ku, 2),
        "beta": round(beta, 2) if beta is not None else None,
        "alpha": round(alpha, 4) if alpha is not None else None,
        "corrToBenchmark": round(corr, 2) if corr is not None else None,
        "bestMonth": round(max(monthly), 4), "worstMonth": round(min(monthly), 4),
        "positiveMonths": round(pos_months, 4),
    }


def correlation(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else 0.0


# ----------------------------------------------------------------------
# 4. Monte Carlo: block bootstrap of flagship returns
# ----------------------------------------------------------------------

def monte_carlo(rets, n_paths=10000, years=10, block=21, start_capital=1_000_000):
    horizon = years * TRADING_DAYS
    n = len(rets)
    month_ends = list(range(0, horizon + 1, 21))
    # store percentile fan at monthly points
    sample_paths = []          # a few full paths for the spaghetti layer
    terminals = []
    fan_values = [[] for _ in month_ends]
    mc = random.Random(SEED + 7)
    for p in range(n_paths):
        path_val, vals = 1.0, [1.0]
        i = 0
        while i < horizon:
            s = mc.randrange(0, n - block)
            for r in rets[s:s + block]:
                path_val *= (1 + r)
                vals.append(path_val)
                i += 1
                if i >= horizon:
                    break
        terminals.append(path_val)
        for j, me in enumerate(month_ends):
            fan_values[j].append(vals[min(me, len(vals) - 1)])
        if p < 40:
            sample_paths.append([round(vals[min(me, len(vals) - 1)], 4)
                                 for me in month_ends])
    pct = {}
    for name, q in [("p5", 0.05), ("p25", 0.25), ("p50", 0.50),
                    ("p75", 0.75), ("p95", 0.95)]:
        series = []
        for j in range(len(month_ends)):
            v = sorted(fan_values[j])
            series.append(round(v[int(q * (n_paths - 1))], 4))
        pct[name] = series
    terminals.sort()
    prob_loss = sum(1 for t in terminals if t < 1.0) / n_paths
    prob_double = sum(1 for t in terminals if t >= 2.0) / n_paths
    prob_triple = sum(1 for t in terminals if t >= 3.0) / n_paths
    # terminal wealth histogram (40 bins, clipped at p99)
    lo, hi = terminals[0], terminals[int(0.99 * n_paths)]
    bins = 40
    width = (hi - lo) / bins
    hist = [0] * bins
    for t in terminals:
        idx = min(int((t - lo) / width), bins - 1) if width > 0 else 0
        hist[idx] += 1
    return {
        "startCapital": start_capital,
        "years": years, "paths": n_paths, "blockDays": block,
        "monthsAxis": [round(m / 21) for m in month_ends],
        "percentiles": pct,
        "samplePaths": sample_paths[:25],
        "terminal": {
            "median": round(terminals[n_paths // 2], 3),
            "mean": round(sum(terminals) / n_paths, 3),
            "p5": round(terminals[int(0.05 * n_paths)], 3),
            "p95": round(terminals[int(0.95 * n_paths)], 3),
            "probLoss": round(prob_loss, 4),
            "probDouble": round(prob_double, 4),
            "probTriple": round(prob_triple, 4),
        },
        "histogram": {"binStart": round(lo, 3), "binWidth": round(width, 4),
                      "counts": hist},
    }


# ----------------------------------------------------------------------
# 5. Series helpers (downsample for the web payload)
# ----------------------------------------------------------------------

def weekly_curve(rets):
    curve = to_prices(rets, 1.0)
    pts = [round(curve[i], 4) for i in range(0, N_DAYS, 5)]
    pts.append(round(curve[-1], 4))
    return pts


def weekly_drawdown(rets):
    curve = to_prices(rets, 1.0)
    peak, dd = curve[0], []
    for v in curve:
        peak = max(peak, v)
        dd.append(v / peak - 1.0)
    pts = [round(dd[i], 4) for i in range(0, N_DAYS, 5)]
    pts.append(round(dd[-1], 4))
    return pts


def rolling_sharpe_monthly(rets, window=252):
    out = []
    for i in range(0, N_DAYS, 21):
        if i + 1 < window:
            out.append(None)
            continue
        w = rets[i - window + 1:i + 1]
        m = sum(w) / window
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / (window - 1))
        ann_r = (1 + m) ** TRADING_DAYS - 1
        ann_v = sd * math.sqrt(TRADING_DAYS)
        out.append(round((ann_r - RISK_FREE) / ann_v, 2) if ann_v > 0 else None)
    return out


def yearly_returns(rets):
    """Calendar-year compounding when real dates exist, else 252-day
    blocks. Must stay aligned with year_labels()."""
    if DATES is not None:
        by_year = {}
        for i, r in enumerate(rets):
            by_year.setdefault(int(DATES[i][:4]), []).append(r)
        out = []
        for y in sorted(by_year):
            v = 1.0
            for r in by_year[y]:
                v *= (1 + r)
            out.append(round(v - 1, 4))
        return out
    out = []
    for y in range(int(YEARS)):
        seg = rets[y * TRADING_DAYS:(y + 1) * TRADING_DAYS]
        v = 1.0
        for r in seg:
            v *= (1 + r)
        out.append(round(v - 1, 4))
    return out


def year_labels():
    if DATES is not None:
        return sorted({int(d[:4]) for d in DATES})
    return [START_YEAR + i for i in range(int(YEARS))]


def week_axis():
    """Year-fraction labels for weekly points."""
    if DATES is not None:
        pts = [round(_year_frac(DATES[i]), 2) for i in range(0, N_DAYS, 5)]
        pts.append(round(_year_frac(DATES[-1]), 2))
        return pts
    pts = [round(START_YEAR + i / TRADING_DAYS, 2) for i in range(0, N_DAYS, 5)]
    pts.append(round(START_YEAR + YEARS, 2))
    return pts


# ----------------------------------------------------------------------
# 6. Allocation engine & recommendations
# ----------------------------------------------------------------------

MIN_SLEEVE_SHARPE = 0.15  # committee floor: no capital to non-performing sleeves


def allocation(strat_rets, strat_metrics):
    """Capital only goes to sleeves clearing the Sharpe floor; survivors
    get inverse-vol weights tilted by Sharpe, capped to 10-40% per
    sleeve, renormalized. Excluded sleeves carry weight 0 (kept in the
    payload so the book is auditable)."""
    raw, excluded = {}, []
    for k, m in strat_metrics.items():
        if k in ("flagship", "benchmark"):
            continue
        if m["sharpe"] < MIN_SLEEVE_SHARPE:
            excluded.append(k)
            continue
        iv = 1.0 / max(m["vol"], 0.02)
        raw[k] = iv * max(m["sharpe"], 0.1)
    if not raw:  # degenerate case: everything failed; equal-weight all
        raw = {k: 1.0 for k in strat_metrics
               if k not in ("flagship", "benchmark")}
        excluded = []
    s = sum(raw.values())
    w = {k: v / s for k, v in raw.items()}
    for _ in range(8):  # iterative cap-and-renormalize
        clipped = {k: max(min(v, 0.40), 0.10) for k, v in w.items()}
        s = sum(clipped.values())
        w = {k: v / s for k, v in clipped.items()}
    out = {k: round(v, 3) for k, v in w.items()}
    for k in excluded:
        out[k] = 0.0
    return out


SLEEVE_NOTES = {
    "statArb": ("Statistical Arbitrage",
                "trades pricing discrepancies between cointegrated securities "
                "(KO/PEP spread, z-score bands)"),
    "trend": ("Trend Following",
              "time-series momentum across equities, rates and gold"),
    "meanRev": ("Mean Reversion",
                "fades outsized one-day index moves under a crisis-vol filter"),
    "volPrem": ("Volatility Premium",
                "vol-gated put-write carry (PUTW)"),
}


def build_recommendations(all_metrics, weights, mc, fm, bm):
    """Committee notes generated from the actual backtest — sleeves that
    earned capital get sized; sleeves that failed get decommissioned."""
    recs = []
    funded = [k for k, w in weights.items() if w > 0]
    cut = [k for k, w in weights.items() if w == 0]

    for k in sorted(funded, key=lambda k: -weights[k]):
        m = all_metrics[k]
        name, desc = SLEEVE_NOTES[k]
        recs.append({
            "title": f"Fund {name} at {weights[k]:.0%}",
            "tag": "CORE ALLOCATION" if weights[k] >= 0.3 else "DIVERSIFIER",
            "body": f"The sleeve — {desc} — cleared the committee's Sharpe floor "
                    f"({MIN_SLEEVE_SHARPE:.2f}) with {m['sharpe']:.2f} on the "
                    f"tested window: {m['cagr']:.1%} CAGR at {m['vol']:.1%} vol, "
                    f"max drawdown {m['maxDrawdown']:.1%}, correlation to the "
                    f"benchmark {m['corrToBenchmark']:.2f}.",
        })
    for k in cut:
        m = all_metrics[k]
        name, desc = SLEEVE_NOTES[k]
        recs.append({
            "title": f"Decommission {name} pending refit",
            "tag": "RESEARCH ACTION",
            "body": f"On the tested window the sleeve — {desc} — failed the "
                    f"Sharpe floor ({m['sharpe']:.2f} vs {MIN_SLEEVE_SHARPE:.2f} "
                    f"required; {m['cagr']:.1%} CAGR, max drawdown "
                    f"{m['maxDrawdown']:.1%}). Zero capital until the engine is "
                    f"re-parameterized and revalidated out-of-sample — paying "
                    f"for conviction with realized losses is how funds die.",
        })

    recs.append({
        "title": "Run the flagship at ~10% target volatility",
        "tag": "PORTFOLIO CONSTRUCTION",
        "body": f"The blended book delivers {fm['cagr']:.1%} CAGR at "
                f"{fm['vol']:.1%} vol (Sharpe {fm['sharpe']:.2f}, max drawdown "
                f"{fm['maxDrawdown']:.1%}) vs the benchmark's {bm['cagr']:.1%} "
                f"at {bm['vol']:.1%} (Sharpe {bm['sharpe']:.2f}, max drawdown "
                f"{bm['maxDrawdown']:.1%}). Beta {fm['beta']:.2f}: this is an "
                f"absolute-return diversifier, not an equity substitute — in a "
                f"raging bull market it will lag buy-and-hold by design.",
    })
    t = mc["terminal"]
    recs.append({
        "title": "Present Monte Carlo with the left tail attached",
        "tag": "INVESTOR RELATIONS",
        "body": f"Across {mc['paths']:,} bootstrapped 10-year paths the median "
                f"outcome is {t['median']:.2f}x capital; the 5th percentile is "
                f"{t['p5']:.2f}x and the probability of losing money over the "
                f"horizon is {t['probLoss']:.1%}. Probability of at least "
                f"doubling: {t['probDouble']:.0%}. Quote p5 alongside the "
                f"median, always.",
    })
    recs.append({
        "title": "Walk-forward revalidation is mandatory",
        "tag": "RISK COMMITTEE",
        "body": "Sleeve edges decay: re-run this engine quarterly on fresh data "
                "and let the Sharpe floor reallocate capital mechanically. "
                "Kill-switch at -12% from peak NAV stays latched until a human "
                "reviews the book. Past performance — real or simulated — does "
                "not guarantee future results.",
    })
    return recs


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Meridian research engine")
    ap.add_argument("--simulated", action="store_true",
                    help="force the calibrated simulation (no network)")
    ap.add_argument("--refresh", action="store_true",
                    help="bypass the market-data cache")
    args = ap.parse_args()

    simulated = args.simulated
    if not simulated:
        try:
            mkt = load_real_market()
            print(f"Real market data: {DATES[0]} → {DATES[-1]} "
                  f"({N_DAYS} trading days)")
        except Exception as e:
            print(f"WARNING: real data unavailable ({e}); "
                  f"falling back to simulation.")
            simulated = True
    if simulated:
        mkt = simulate_market()
    bench = mkt["equity"]

    strats = {
        "statArb": strat_stat_arb(mkt),
        "trend": strat_trend(mkt),
        "meanRev": strat_mean_rev(mkt),
        "volPrem": strat_vol_premium(mkt),
    }

    pre_metrics = {k: metrics(v, bench) for k, v in strats.items()}
    weights = allocation(strats, pre_metrics)
    flagship = blend(strats, weights)
    # modest leverage on the blend to target ~10% vol like a real fund;
    # committee caps gross leverage at 1.5x
    flag_m = metrics(flagship, bench)
    lev = min(0.10 / max(flag_m["vol"], 0.01), 1.5)
    flagship = [r * lev for r in flagship]

    strats["flagship"] = flagship
    all_rets = dict(strats)
    all_rets["benchmark"] = bench

    all_metrics = {k: metrics(v, bench) for k, v in all_rets.items()}

    names = ["statArb", "trend", "meanRev", "volPrem", "flagship", "benchmark"]
    corr_matrix = [[round(correlation(all_rets[a], all_rets[b]), 2)
                    for b in names] for a in names]

    mc = monte_carlo(flagship)

    fm = all_metrics["flagship"]
    bm = all_metrics["benchmark"]
    recommendations = build_recommendations(all_metrics, weights, mc, fm, bm)

    if simulated:
        disclaimer = ("All market data is simulated, calibrated to realistic "
                      "historical statistics. Backtested and Monte Carlo results "
                      "are hypothetical and do not guarantee future returns. "
                      "Not investment advice.")
        source = "Calibrated regime-switching simulation"
    else:
        disclaimer = ("Backtests use real daily adjusted closes (Yahoo Finance: "
                      "SPY, TLT, GLD, KO, PEP, PUTW; %s to %s). Backtested and "
                      "Monte Carlo results are hypothetical, benefit from "
                      "hindsight, and do not guarantee future returns. "
                      "Not investment advice." % (DATES[0], DATES[-1]))
        source = "Yahoo Finance daily adjusted closes"

    payload = {
        "meta": {
            "fund": "Meridian Alpha Capital",
            "generated": "engine.py (seed %d)" % SEED,
            "periodYears": round(YEARS, 1),
            "startYear": START_YEAR,
            "riskFree": RISK_FREE,
            "simulated": simulated,
            "dataSource": source,
            "periodStart": DATES[0] if DATES else str(START_YEAR),
            "periodEnd": DATES[-1] if DATES else str(START_YEAR + int(YEARS)),
            "disclaimer": disclaimer,
        },
        "strategies": {
            "statArb": {"label": "Statistical Arbitrage",
                        "desc": "Market-neutral pairs trading on cointegrated securities — harvests pricing discrepancies."},
            "trend": {"label": "Trend Following",
                      "desc": "Multi-asset time-series momentum across equities, rates and gold."},
            "meanRev": {"label": "Mean Reversion",
                        "desc": "Z-score band trading on the equity index with a crisis-vol filter."},
            "volPrem": {"label": "Volatility Premium",
                        "desc": "Regime-filtered short-volatility carry."},
            "flagship": {"label": "Meridian Flagship",
                         "desc": "Risk-parity blend of all four sleeves at ~11% target vol."},
            "benchmark": {"label": "Equity Benchmark",
                          "desc": "Buy & hold equity index (simulated S&P-like)."},
        },
        "weights": weights,
        "metrics": all_metrics,
        "weekAxis": week_axis(),
        "equityCurves": {k: weekly_curve(v) for k, v in all_rets.items()},
        "drawdowns": {"flagship": weekly_drawdown(flagship),
                      "benchmark": weekly_drawdown(bench)},
        "rollingSharpe": {"flagship": rolling_sharpe_monthly(flagship),
                          "benchmark": rolling_sharpe_monthly(bench)},
        "yearlyReturns": {"flagship": yearly_returns(flagship),
                          "benchmark": yearly_returns(bench)},
        "years": year_labels(),
        "correlation": {"names": names, "matrix": corr_matrix},
        "monteCarlo": mc,
        "recommendations": recommendations,
    }

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "data.js")
    with open(out_path, "w") as f:
        f.write("// Generated by quant/engine.py — do not edit by hand.\n")
        f.write("window.FUND_DATA = ")
        json.dump(payload, f, separators=(",", ":"))
        f.write(";\n")

    # console summary
    print("=== Meridian Alpha Capital — backtest summary (%.1fy, %s) ===" % (YEARS, "simulated" if simulated else "real data"))
    for k in names:
        m = all_metrics[k]
        print(f"{k:>10}: CAGR {m['cagr']:7.2%}  Vol {m['vol']:6.2%}  "
              f"Sharpe {m['sharpe']:5.2f}  MaxDD {m['maxDrawdown']:7.2%}  "
              f"Sortino {m['sortino']:5.2f}")
    print(f"\nWeights: {weights}")
    t = mc["terminal"]
    print(f"Monte Carlo 10y: median {t['median']}x  p5 {t['p5']}x  "
          f"p95 {t['p95']}x  P(loss) {t['probLoss']:.1%}")
    print(f"\nWrote {os.path.normpath(out_path)} "
          f"({os.path.getsize(out_path)//1024} KB)")


if __name__ == "__main__":
    main()

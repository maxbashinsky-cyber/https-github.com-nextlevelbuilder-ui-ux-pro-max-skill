#!/usr/bin/env python3
"""
Meridian Alpha Capital — Quant Research Engine
================================================
Generates the full analytics payload consumed by the website:

  1. Regime-switching market simulation calibrated to realistic
     historical equity/rates/commodity statistics (2010-2025 era:
     bull regimes, the 2015 / 2018Q4 / 2020 / 2022 drawdown regimes).
  2. Backtests of five strategies:
       - Statistical Arbitrage (pairs / discrepancy trading)
       - Systematic Trend Following (multi-asset time-series momentum)
       - Mean Reversion (z-score band trading on equity index)
       - Volatility Risk Premium (regime-filtered short vol)
       - Meridian Flagship (risk-parity multi-strategy blend)
     plus a Buy & Hold benchmark.
  3. Institutional metric suite: CAGR, vol, Sharpe, Sortino, Calmar,
     max drawdown, alpha/beta, VaR/CVaR, skew, kurtosis, hit rate.
  4. Monte Carlo: 10,000-path block bootstrap, 10-year horizon.
  5. Allocation engine: inverse-vol risk-weighted model portfolio
     and investment-committee style recommendations.

Pure stdlib (no numpy required). Deterministic via fixed seed.

IMPORTANT: All market data here is SIMULATED (calibrated to realistic
historical statistics). Backtests on simulated or historical data do
not guarantee future results. This is a research/demo artifact, not
investment advice.

Usage:  python3 engine.py          # writes ../data.js
"""

import json
import math
import os
import random

SEED = 20260609
TRADING_DAYS = 252
YEARS = 15
N_DAYS = TRADING_DAYS * YEARS
START_YEAR = 2010

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
    """Volatility risk premium: collect a steady carry (like short
    variance / covered strangles) that loses convexly in vol spikes.
    Regime filter cuts exposure when trailing vol is elevated."""
    rets, prev_pos = [], 0.0
    daily_carry = 0.085 / TRADING_DAYS
    for i in range(N_DAYS):
        vol60 = rolling_std(mkt["equity"], 60, i)
        ann_vol = (vol60 or 0.01) * math.sqrt(TRADING_DAYS)
        pos = 1.0 if ann_vol < 0.16 else (0.4 if ann_vol < 0.26 else 0.0)
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
    out = []
    for y in range(YEARS):
        seg = rets[y * TRADING_DAYS:(y + 1) * TRADING_DAYS]
        v = 1.0
        for r in seg:
            v *= (1 + r)
        out.append(round(v - 1, 4))
    return out


def week_axis():
    """Year-fraction labels for weekly points."""
    pts = [round(START_YEAR + i / TRADING_DAYS, 2) for i in range(0, N_DAYS, 5)]
    pts.append(round(START_YEAR + YEARS, 2))
    return pts


# ----------------------------------------------------------------------
# 6. Allocation engine & recommendations
# ----------------------------------------------------------------------

def allocation(strat_rets, strat_metrics):
    """Inverse-volatility weights tilted by Sharpe, capped to 10-40%
    per sleeve (committee diversification mandate), renormalized."""
    raw = {}
    for k, m in strat_metrics.items():
        if k in ("flagship", "benchmark"):
            continue
        iv = 1.0 / max(m["vol"], 0.02)
        tilt = max(m["sharpe"], 0.1)
        raw[k] = iv * tilt
    s = sum(raw.values())
    w = {k: v / s for k, v in raw.items()}
    for _ in range(8):  # iterative cap-and-renormalize
        clipped = {k: max(min(v, 0.40), 0.10) for k, v in w.items()}
        s = sum(clipped.values())
        w = {k: v / s for k, v in clipped.items()}
    return {k: round(v, 3) for k, v in w.items()}


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
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
    recommendations = [
        {
            "title": "Anchor the book with Statistical Arbitrage",
            "tag": "CORE ALLOCATION",
            "body": f"The stat-arb sleeve trades pricing discrepancies between "
                    f"cointegrated securities and runs near-zero correlation to equities "
                    f"({all_metrics['statArb']['corrToBenchmark']:.2f}). With a "
                    f"{all_metrics['statArb']['sharpe']:.2f} Sharpe and "
                    f"{all_metrics['statArb']['maxDrawdown']:.1%} max drawdown, it is the "
                    f"highest-conviction source of uncorrelated alpha. Recommended weight: "
                    f"{weights['statArb']:.0%}.",
        },
        {
            "title": "Trend following as the crisis hedge",
            "tag": "DIVERSIFIER",
            "body": f"Time-series momentum across equities, rates and gold historically "
                    f"profits in extended drawdowns (long bonds/gold, reduced equity). "
                    f"Sleeve Sharpe {all_metrics['trend']['sharpe']:.2f}, correlation to "
                    f"benchmark {all_metrics['trend']['corrToBenchmark']:.2f}. It cushions "
                    f"the left tail that hurts the vol-premium sleeve. Weight: "
                    f"{weights['trend']:.0%}.",
        },
        {
            "title": "Size volatility premium conservatively",
            "tag": "RISK CONTROL",
            "body": f"Short-vol carry shows the classic profile: high hit rate "
                    f"({all_metrics['volPrem']['hitRate']:.0%} positive days) with negative "
                    f"skew ({all_metrics['volPrem']['skew']:.1f}). The 60-day realized-vol "
                    f"filter cut 2020-style convex losses, but committee policy caps the "
                    f"sleeve at {weights['volPrem']:.0%} and mandates the trend sleeve as "
                    f"an offset.",
        },
        {
            "title": "Run the flagship at ~11% target volatility",
            "tag": "PORTFOLIO CONSTRUCTION",
            "body": f"The risk-parity blend of all four sleeves delivers "
                    f"{fm['cagr']:.1%} CAGR at {fm['vol']:.1%} vol (Sharpe {fm['sharpe']:.2f}) "
                    f"vs the benchmark's {bm['cagr']:.1%} at {bm['vol']:.1%} "
                    f"(Sharpe {bm['sharpe']:.2f}), with max drawdown {fm['maxDrawdown']:.1%} "
                    f"vs {bm['maxDrawdown']:.1%}. Beta of {fm['beta']:.2f} keeps the fund "
                    f"saleable as a true absolute-return product.",
        },
        {
            "title": "Monte Carlo supports a 10-year lockup pitch",
            "tag": "INVESTOR RELATIONS",
            "body": f"Across {mc['paths']:,} bootstrapped 10-year paths, the median "
                    f"outcome is {mc['terminal']['median']:.1f}x capital; the 5th percentile "
                    f"is {mc['terminal']['p5']:.2f}x and the probability of losing money "
                    f"over the horizon is {mc['terminal']['probLoss']:.1%}. Probability of "
                    f"at least doubling: {mc['terminal']['probDouble']:.0%}. Use the fan "
                    f"chart in LP decks; always present p5 alongside the median.",
        },
        {
            "title": "Watch capacity and regime drift",
            "tag": "RISK COMMITTEE",
            "body": "Backtests are simulation-calibrated; live slippage on the stat-arb "
                    "sleeve grows with AUM, and the mean-reversion edge decays in trending "
                    "tapes. Mandate: quarterly walk-forward revalidation, kill-switch at "
                    "2x historical sleeve drawdown, and a 20% cash buffer for margin "
                    "spikes. Past performance — simulated or live — does not guarantee "
                    "future results.",
        },
    ]

    payload = {
        "meta": {
            "fund": "Meridian Alpha Capital",
            "generated": "engine.py (seed %d)" % SEED,
            "periodYears": YEARS,
            "startYear": START_YEAR,
            "riskFree": RISK_FREE,
            "simulated": True,
            "disclaimer": "All market data is simulated, calibrated to realistic "
                          "historical statistics. Backtested and Monte Carlo results "
                          "are hypothetical and do not guarantee future returns. "
                          "Not investment advice.",
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
        "years": [START_YEAR + i for i in range(YEARS)],
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
    print("=== Meridian Alpha Capital — backtest summary (15y simulated) ===")
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

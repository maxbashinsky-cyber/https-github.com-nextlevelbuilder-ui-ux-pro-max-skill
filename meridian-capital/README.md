# Meridian Alpha Capital — Systematic Multi-Strategy Hedge Fund (Demo)

A complete hedge fund, built end-to-end: the fund's investor-facing site
(sales funnel inspired by [vanquish.so](https://www.vanquish.so)), a full
quant analytics suite, and the research engine that generates every number
on both pages.

## Contents

| File | Purpose |
|------|---------|
| `index.html` | Investor-facing fund site — hero + allocation-deck capture, investment philosophy, strategy sleeves, performance vs benchmark, Monte Carlo teaser, fund terms (share classes, fees, liquidity), net-of-fees investor calculator, subscription process, disclaimers |
| `dashboard.html` | Analytics suite — equity curves, annual returns, rolling Sharpe, Monte Carlo fan chart + terminal-wealth histogram, drawdowns, full metric table, correlation matrix, model allocation, committee recommendations |
| `data.js` | Generated payload (`window.FUND_DATA`) consumed by both pages |
| `quant/engine.py` | Research engine that produces `data.js` (pure stdlib, deterministic) |

Open `index.html` in a browser (Chart.js + Google Fonts load from CDN).

## The quant engine

`python3 quant/engine.py` regenerates everything:

1. **Market simulation** — 15 years of daily returns for an equity index,
   bonds, gold, and a cointegrated stock pair. Regime-switching drift/vol
   calibrated to the 2010–2025 texture (2011, 2015, 2018Q4, 2020, 2022
   drawdown regimes), volatility clustering, fat-tailed jumps, and mild 1-day
   index mean reversion.
2. **Backtests** (net of 4bps/turnover costs, no lookahead):
   - **Statistical Arbitrage** — pairs trading on the cointegrated spread
     (the "pricing discrepancy" sleeve); z-score entry ±1.5σ, exit 0.25σ
   - **Trend Following** — multi-asset time-series momentum, 105d SMA,
     7% per-sleeve vol target, weekly rebalance
   - **Mean Reversion** — short-horizon reversal of outsized 1-day moves,
     flat when 60d realized vol exceeds 30% (crisis filter)
   - **Volatility Premium** — regime-filtered short-vol carry with convex
     crash losses modeled explicitly
   - **Meridian Flagship** — inverse-vol, Sharpe-tilted blend (weights capped
     10–40% per sleeve) at a 1.5× gross leverage cap, ~10% vol target
3. **Metrics** — CAGR, vol, Sharpe, Sortino, Calmar, max drawdown, hit rate,
   profit factor, VaR/CVaR (95/99), skew, kurtosis, alpha/beta/correlation
   vs benchmark, best/worst month, positive-month ratio.
4. **Monte Carlo** — 10,000 paths × 10 years, 21-day block bootstrap
   (preserves vol clustering), percentile fan, terminal-wealth distribution,
   P(loss)/P(2×)/P(3×).
5. **Allocation engine** — produces the model weights and the investment-
   committee recommendation cards shown on the dashboard.

Latest run (seed `20260609`):

| Strategy | CAGR | Vol | Sharpe | Max DD |
|----------|-----:|----:|-------:|-------:|
| Statistical Arbitrage | 13.2% | 7.9% | 1.30 | -10.2% |
| Trend Following | 6.5% | 11.5% | 0.30 | -24.8% |
| Mean Reversion | 2.8% | 3.9% | -0.06 | -10.2% |
| Volatility Premium | 6.9% | 4.9% | 0.79 | -15.3% |
| **Meridian Flagship** | **13.8%** | **6.1%** | **1.77** | **-10.0%** |
| Equity benchmark | 6.7% | 17.0% | 0.22 | -45.6% |

Monte Carlo (10y, $1M start): median **$3.68M**, p5 $2.78M, p95 $4.79M.

## Important disclaimer

All market data is **simulated** (calibrated to realistic historical
statistics — this environment has no market-data feed). Backtested and Monte
Carlo results are **hypothetical**, are prepared with the benefit of
hindsight, and do not guarantee future returns. Live results differ due to
slippage, capacity, financing and regime change. Nothing here is investment
advice or an offer of securities. To use real data, replace
`simulate_market()` with a loader for actual daily price history — every
strategy, metric and projection downstream works unchanged.

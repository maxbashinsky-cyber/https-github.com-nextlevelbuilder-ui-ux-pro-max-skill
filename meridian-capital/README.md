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
| `console.html` | **Operator console** — your NAV, P&L, positions vs targets, sleeve signals, order log, risk panel (kill switch, leverage, vol), and the paper→live runbook |
| `quant/trader.py` | **Automated trading daemon** — computes signals daily and rebalances the book; paper mode locally, Alpaca paper/live via API keys |
| `quant/broker.py` | Broker layer — `PaperBroker` (local simulated fills) and `AlpacaBroker` (stdlib REST adapter, paper & live endpoints) |
| `live/` | Session state (`state.json`) and console payload (`console-data.js`) written by the daemon |

Open `index.html` in a browser (Chart.js + Google Fonts load from CDN).

## Real market data

`quant/data.py` pulls daily adjusted closes from Yahoo Finance (stdlib
only, on-disk cache in `live/marketdata/`). Both the research engine and
the trader use it by default:

- `python3 quant/engine.py` — backtests on **real history** (SPY, TLT,
  GLD, KO, PEP, PUTW; window bounded by PUTW inception, Feb 2016).
  Falls back to the calibrated simulation if the network is down;
  `--simulated` forces it, `--refresh` busts the cache.
- The allocator applies a **Sharpe floor (0.15)**: sleeves that lost
  money on real data (currently the KO/PEP pair and the 1-day reversal)
  are decommissioned to 0% until refit; trend and PUTW carry split the
  book. The dashboard's committee notes state this honestly.

## Running the fund yourself (deposit → automated trading)

The daemon trades the ETF book — SPY/TLT/GLD (trend), SPY (reversal),
KO/PEP (stat-arb pair), PUTW (vol-gated put-write) — and **adopts the
research engine's latest weights from `data.js` at startup**, so capital
follows the walk-forward revalidation mechanically.

```bash
# 1. Paper-trade on REAL market data (default — no keys, no money risk):
cd meridian-capital/quant
python3 trader.py --mode paper-real --deposit 250000 --days 130
# → open ../console.html to monitor NAV, positions, signals, orders
# (--mode paper = fully offline simulated prices)

# 2. Real broker, fake money — free Alpaca paper keys:
export APCA_API_KEY_ID=...  APCA_API_SECRET_KEY=...
python3 trader.py --mode alpaca-paper

# 3. Automate it (daily rebalance, 15min before the close):
# 45 15 * * 1-5  cd .../quant && python3 trader.py --mode alpaca-paper >> ../live/trader.log 2>&1
```

Risk overlay enforced before any order: 35% per-position cap, 1.3×
gross cap, and a **kill switch** that flattens the book at -12% from
peak NAV and stays latched until you delete `live/state.json`.

## Adding real money

Real capital can only enter through **your own brokerage account** —
no code in this repo (or anywhere) can skip the regulated steps:

1. **Open an Alpaca brokerage account** at alpaca.markets (individual,
   KYC — minutes, free). Fund it by ACH/wire from your bank.
2. **Generate LIVE API keys** (different from paper keys) and put them
   in `meridian-capital/live/credentials.env` (gitignored — never
   commit keys):
   ```
   APCA_API_KEY_ID=AK...
   APCA_API_SECRET_KEY=...
   ```
3. **Run the daemon on the machine you trust** (laptop, VPS):
   ```bash
   MERIDIAN_CONFIRM_LIVE=yes python3 trader.py --mode alpaca-live
   ```
   The extra env var is a deliberate speed bump; without it the daemon
   refuses live endpoints. Same cron line as paper, mode swapped.
4. **Ramp like a professional**: ≥4 weeks of `alpaca-paper` first,
   start live at 10–20% of intended size, scale only while live
   tracking error vs paper stays small.

Hard truth, in writing: the real-data backtest says this book made
~9% CAGR at ~10% vol (Sharpe 0.6) with a -10% max drawdown over
2016–2026 — *less* than buy-and-hold SPY in that bull decade, with a
third of the drawdown. Nothing here guarantees future profits; the
edge must be re-earned quarterly via revalidation.

## The quant engine

`python3 quant/engine.py` regenerates everything:

1. **Market data** — real daily adjusted closes by default (Yahoo via
   `quant/data.py`: SPY, TLT, GLD, KO, PEP, PUTW, ~10.3 years). With
   `--simulated` (or as network fallback): 15 years of regime-switching
   simulation calibrated to the 2010–2025 texture, with volatility
   clustering and fat-tailed jumps.
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

Latest run on **real data** (2016-02-25 → 2026-06-08):

| Strategy | CAGR | Vol | Sharpe | Max DD | Weight |
|----------|-----:|----:|-------:|-------:|-------:|
| Trend Following | 8.3% | 12.8% | 0.42 | -16.0% | 50% |
| Volatility Premium (PUTW) | 6.2% | 8.2% | 0.39 | -12.2% | 50% |
| Statistical Arbitrage (KO/PEP) | -2.4% | 7.3% | -0.74 | -30.0% | 0% — decommissioned |
| Mean Reversion | -0.5% | 4.5% | -0.79 | -20.2% | 0% — decommissioned |
| **Meridian Flagship** | **9.0%** | **10.0%** | **0.60** | **-10.3%** | — |
| SPY benchmark | 15.9% | 17.8% | 0.72 | -33.7% | — |

Monte Carlo (10y, $1M start): median **$2.36M**, p5 $1.48M, p95 $3.77M,
P(loss) 0.2%.

## Important disclaimer

Backtested and Monte Carlo results — on real or simulated data — are
**hypothetical**, are prepared with the benefit of hindsight, and do not
guarantee future returns. Live results differ due to slippage, capacity,
financing and regime change. Nothing here is investment advice or an
offer of securities.

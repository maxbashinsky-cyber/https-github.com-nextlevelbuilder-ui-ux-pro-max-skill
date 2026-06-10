#!/usr/bin/env python3
"""
Meridian Alpha Capital — Automated trading daemon
=================================================
Runs the backtested multi-strategy book on a real, ETF-implementable
universe. You deposit capital (paper) or fund a brokerage account
(Alpaca); the daemon computes signals and rebalances automatically.

Universe
--------
  SPY   equity index        (trend + short-horizon reversal sleeves)
  TLT   long treasuries     (trend sleeve)
  GLD   gold                (trend sleeve)
  KO/PEP cointegrated pair  (statistical-arbitrage sleeve)
  PUTW  put-write ETF       (volatility-premium sleeve, vol-gated)

Sleeve capital weights come from the 15-year backtest (quant/engine.py):
statArb 40% · volPrem 40% · trend 10% · meanRev 10%, gross capped at 1.3x.

Risk controls (enforced every run, before any order)
----------------------------------------------------
  * Kill switch: if NAV drops 12% from its peak, the book is flattened
    and the daemon refuses to re-enter until you delete live/state.json
    (a deliberate human-in-the-loop reset).
  * Per-position cap 35% of equity; minimum ticket $100 to avoid churn.
  * Live mode requires MERIDIAN_CONFIRM_LIVE=yes (see broker.py).

Usage
-----
  # local paper simulation: deposit $250k, run 130 trading days
  python3 trader.py --mode paper --deposit 250000 --days 130

  # one real rebalance against Alpaca PAPER account (run via cron daily)
  python3 trader.py --mode alpaca-paper

  # live (only after weeks of validated paper trading)
  MERIDIAN_CONFIRM_LIVE=yes python3 trader.py --mode alpaca-live

Cron example (3:45pm ET weekdays, 15min before close):
  45 15 * * 1-5  cd /path/to/meridian-capital/quant && python3 trader.py --mode alpaca-paper >> ../live/trader.log 2>&1

Every run writes live/state.json (persistent NAV/order history) and
live/console-data.js — open console.html to monitor the book.

DISCLAIMER: backtested edge does not guarantee live alpha. Start in
paper mode, size up slowly, keep the kill switch on.
"""

import argparse
import datetime
import json
import math
import os
import random

from broker import PaperBroker, AlpacaBroker

# ---------------------------------------------------------------- config
CONFIG = {
    "sleeveWeights": {"statArb": 0.40, "trend": 0.10,
                      "meanRev": 0.10, "volPrem": 0.40},
    "grossCap": 1.3,          # max sum of |weights|
    "positionCap": 0.35,      # max |weight| per ticker
    "killSwitchDD": 0.12,     # flatten at -12% from peak NAV
    "minTicket": 100.0,       # skip orders smaller than this ($)
    "trendVolTarget": 0.07,   # per trend sleeve, annualized
    "historyDays": 280,
}
TICKERS = ["SPY", "TLT", "GLD", "KO", "PEP", "PUTW"]
TD = 252
LIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "live")


# ---------------------------------------------------------------- signals
def _std(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _rets(prices):
    return [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]


def compute_targets(hist):
    """hist: {symbol: [closes oldest->today]} -> ({symbol: weight}, signals)"""
    W = CONFIG["sleeveWeights"]
    tgt = {s: 0.0 for s in TICKERS}
    signals = {}

    # --- trend sleeve: 105d SMA, vol targeted, asymmetric short on SPY ---
    trend_detail = {}
    for sym in ("SPY", "TLT", "GLD"):
        p = hist[sym]
        if len(p) < 110:
            continue
        sma105 = sum(p[-105:]) / 105
        r = _rets(p)[-60:]
        ann_vol = _std(r) * math.sqrt(TD)
        above = p[-1] > sma105
        sign = 1.0 if above else (-0.5 if sym == "SPY" else -1.0)
        w = sign * min(CONFIG["trendVolTarget"] / max(ann_vol, 1e-6), 2.0)
        w = max(min(w, 1.2), -1.2)
        tgt[sym] += w * W["trend"]
        trend_detail[sym] = {"above_sma105": above, "annVol": round(ann_vol, 3),
                             "sleevePos": round(w, 2)}
    signals["trend"] = trend_detail

    # --- mean reversion on SPY: fade outsized 1-day move, crisis filter ---
    p = hist["SPY"]
    r = _rets(p)
    sd20 = _std(r[-20:])
    vol60 = _std(r[-60:]) * math.sqrt(TD)
    z1 = (r[-1] / sd20) if sd20 > 1e-9 else 0.0
    if vol60 > 0.30:
        mr_pos = 0.0
    elif z1 < -1.3:
        mr_pos = 0.7
    elif z1 > 1.5:
        mr_pos = -0.35
    else:
        mr_pos = 0.0
    tgt["SPY"] += mr_pos * W["meanRev"]
    signals["meanRev"] = {"z1": round(z1, 2), "vol60": round(vol60, 3),
                          "sleevePos": mr_pos}

    # --- stat arb: z-score of log(KO/PEP), 60d window ---
    ko, pep = hist["KO"], hist["PEP"]
    n = min(len(ko), len(pep))
    spread = [math.log(ko[i] / pep[i]) for i in range(n - 60, n)]
    mu, sd = sum(spread) / 60, _std(spread)
    z = (spread[-1] - mu) / sd if sd > 1e-9 else 0.0
    if z > 1.5:
        pair_pos = -1.0       # short KO, long PEP
    elif z < -1.5:
        pair_pos = 1.0
    elif abs(z) < 0.25:
        pair_pos = 0.0
    else:
        pair_pos = None       # hold previous (handled by caller via state)
    leg = 0.6                 # leverage on spread notional (per backtest)
    signals["statArb"] = {"z": round(z, 2), "pos": pair_pos, "leg": leg}

    # --- vol premium: PUTW allocation gated by SPY realized vol ---
    vp_pos = 1.0 if vol60 < 0.16 else (0.4 if vol60 < 0.26 else 0.0)
    tgt["PUTW"] += vp_pos * W["volPrem"]
    signals["volPrem"] = {"spyVol60": round(vol60, 3), "sleevePos": vp_pos}

    return tgt, signals


def apply_pair(tgt, pair_pos, prev_pair_pos):
    pos = prev_pair_pos if pair_pos is None else pair_pos
    leg = 0.6 * CONFIG["sleeveWeights"]["statArb"] / 0.4  # scale w/ sleeve wt
    tgt["KO"] += pos * leg
    tgt["PEP"] += -pos * leg
    return pos


def risk_overlay(tgt, nav, peak):
    """Position caps, gross cap, kill switch. Returns (tgt, killed)."""
    if peak > 0 and nav < peak * (1.0 - CONFIG["killSwitchDD"]):
        return {s: 0.0 for s in tgt}, True
    for s in tgt:
        tgt[s] = max(min(tgt[s], CONFIG["positionCap"]), -CONFIG["positionCap"])
    gross = sum(abs(w) for w in tgt.values())
    if gross > CONFIG["grossCap"]:
        scale = CONFIG["grossCap"] / gross
        tgt = {s: w * scale for s, w in tgt.items()}
    return tgt, False


# ---------------------------------------------------------------- state
def load_state():
    path = os.path.join(LIVE_DIR, "state.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"navHistory": [], "orders": [], "deposits": [],
            "pairPos": 0.0, "peakNav": 0.0, "killed": False}


def save_state(state):
    os.makedirs(LIVE_DIR, exist_ok=True)
    with open(os.path.join(LIVE_DIR, "state.json"), "w") as f:
        json.dump(state, f, indent=1)


def write_console(state, broker, tgt, signals, mode, killed):
    equity = broker.get_equity()
    cash = broker.get_cash()
    positions = []
    for sym, qty in sorted(broker.get_positions().items()):
        px = broker.get_price(sym)
        positions.append({"symbol": sym, "qty": round(qty, 2),
                          "price": round(px, 2),
                          "value": round(qty * px, 2),
                          "weight": round(qty * px / equity, 4) if equity else 0,
                          "target": round(tgt.get(sym, 0.0), 4)})
    navs = [p["nav"] for p in state["navHistory"]]
    peak = max(navs) if navs else equity
    dd = equity / peak - 1.0 if peak > 0 else 0.0
    rets = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs))]
    vol20 = _std(rets[-20:]) * math.sqrt(TD) if len(rets) >= 5 else 0.0
    deposited = sum(d["amount"] for d in state["deposits"])
    payload = {
        "meta": {"fund": "Meridian Alpha Capital", "mode": mode,
                 "updated": state["navHistory"][-1]["date"] if state["navHistory"] else None,
                 "config": CONFIG},
        "account": {"nav": round(equity, 2), "cash": round(cash, 2),
                    "deposited": round(deposited, 2),
                    "pnl": round(equity - deposited, 2),
                    "pnlPct": round(equity / deposited - 1, 4) if deposited else 0,
                    "gross": round(sum(abs(p["value"]) for p in positions) / equity, 3) if equity else 0,
                    "todayPnl": round(navs[-1] - navs[-2], 2) if len(navs) >= 2 else 0.0},
        "risk": {"drawdown": round(dd, 4), "peakNav": round(peak, 2),
                 "vol20": round(vol20, 4),
                 "killSwitchAt": round(-CONFIG["killSwitchDD"], 2),
                 "killed": killed},
        "positions": positions,
        "targets": {s: round(w, 4) for s, w in tgt.items()},
        "signals": signals,
        "navHistory": state["navHistory"][-260:],
        "orders": state["orders"][-60:][::-1],
    }
    os.makedirs(LIVE_DIR, exist_ok=True)
    with open(os.path.join(LIVE_DIR, "console-data.js"), "w") as f:
        f.write("// Generated by quant/trader.py — do not edit.\n")
        f.write("window.LIVE_DATA = ")
        json.dump(payload, f, separators=(",", ":"))
        f.write(";\n")


# ---------------------------------------------------------------- rebalance
def rebalance(broker, state, mode, label):
    hist = {s: broker.get_history(s, CONFIG["historyDays"]) for s in TICKERS}
    tgt, signals = compute_targets(hist)
    state["pairPos"] = apply_pair(tgt, signals["statArb"]["pos"], state["pairPos"])

    equity = broker.get_equity()
    state["peakNav"] = max(state.get("peakNav", 0.0), equity)
    tgt, killed = risk_overlay(tgt, equity, state["peakNav"])
    if killed and not state.get("killed"):
        print(f"[{label}] KILL SWITCH: NAV {equity:,.0f} breached "
              f"-{CONFIG['killSwitchDD']:.0%} from peak {state['peakNav']:,.0f}. Flattening.")
    state["killed"] = state.get("killed") or killed
    if state["killed"]:
        tgt = {s: 0.0 for s in tgt}

    held = broker.get_positions()
    for sym in TICKERS:
        px = broker.get_price(sym)
        want_qty = tgt.get(sym, 0.0) * equity / px
        delta = want_qty - held.get(sym, 0.0)
        if abs(delta * px) < CONFIG["minTicket"]:
            continue
        fill = broker.submit_order(sym, delta)
        if fill:
            fill["date"] = label
            state["orders"].append(fill)
    return tgt, signals, state["killed"]


def mark(state, broker, label):
    state["navHistory"].append({"date": label,
                                "nav": round(broker.get_equity(), 2),
                                "cash": round(broker.get_cash(), 2)})


# ---------------------------------------------------------------- paper sim
def simulated_prices(seed=20260610, days=600):
    """Deterministic correlated daily closes for the universe."""
    r = random.Random(seed)
    dt = 1.0 / TD
    out = {s: [100.0] for s in TICKERS}
    spread = 0.0
    for d in range(days):
        z = r.gauss(0, 1)
        if r.random() < 0.008:
            z += r.gauss(0, 1.8)
        # mild regime wave so trend/vol filters have something to chew on
        regime = 0.10 + 0.06 * math.sin(d / 90.0)
        vol = 0.15 + 0.06 * math.sin(d / 130.0 + 2)
        r_spy = regime * dt + vol * math.sqrt(dt) * z
        r_tlt = 0.03 * dt + 0.11 * math.sqrt(dt) * (r.gauss(0, 1) - 0.25 * z)
        r_gld = 0.05 * dt + 0.14 * math.sqrt(dt) * (r.gauss(0, 1) - 0.10 * z)
        new_spread = spread - 0.06 * spread + 0.012 * r.gauss(0, 1)
        ds, spread = new_spread - spread, new_spread
        r_ko = 0.6 * r_spy + 0.5 * ds + 0.10 * math.sqrt(dt) * 0  # pure pair
        r_pep = 0.6 * r_spy - 0.5 * ds
        crash = -8.0 * max(-r_spy - 0.018, 0.0) ** 1.5
        r_putw = 0.085 * dt + 0.25 * r_spy + crash
        for sym, ret in (("SPY", r_spy), ("TLT", r_tlt), ("GLD", r_gld),
                         ("KO", r_ko), ("PEP", r_pep), ("PUTW", r_putw)):
            out[sym].append(out[sym][-1] * (1 + ret))
    return out


def run_paper(deposit, days, seed):
    prices = simulated_prices(seed=seed, days=CONFIG["historyDays"] + days + 20)
    broker = PaperBroker(prices, history_days=CONFIG["historyDays"])
    state = load_state()
    if deposit > 0:
        broker.deposit(deposit)
        state["deposits"].append({"date": "day-0", "amount": deposit})
    # restore prior paper session? paper restarts fresh each run for clarity
    state["navHistory"], state["orders"] = [], []
    state["pairPos"], state["peakNav"], state["killed"] = 0.0, 0.0, False

    tgt, signals, killed = {}, {}, False
    for d in range(days):
        label = f"day-{d + 1}"
        tgt, signals, killed = rebalance(broker, state, "paper", label)
        if not broker.advance_day():
            break
        mark(state, broker, label)
    save_state(state)
    write_console(state, broker, tgt, signals, "paper", killed)
    nav = broker.get_equity()
    dep = sum(d["amount"] for d in state["deposits"])
    print(f"Paper session: {days} trading days · deposited ${dep:,.0f} · "
          f"final NAV ${nav:,.0f} ({nav / dep - 1:+.2%}) · "
          f"orders {len(state['orders'])} · killed={killed}")
    print(f"Open console.html to monitor. State in live/state.json.")


def run_broker(live):
    broker = AlpacaBroker(live=live)
    state = load_state()
    if state.get("killed"):
        print("Kill switch latched from a prior run. Review the book, then "
              "delete live/state.json to re-arm. No orders sent.")
        return
    label = datetime.date.today().isoformat()
    tgt, signals, killed = rebalance(broker, state, "alpaca", label)
    mark(state, broker, label)
    save_state(state)
    write_console(state, broker, tgt, signals,
                  "alpaca-live" if live else "alpaca-paper", killed)
    print(f"[{label}] rebalanced. NAV ${broker.get_equity():,.0f}. "
          f"Orders this run: {sum(1 for o in state['orders'] if o.get('date') == label)}.")


def main():
    ap = argparse.ArgumentParser(description="Meridian automated trader")
    ap.add_argument("--mode", default="paper",
                    choices=["paper", "alpaca-paper", "alpaca-live"])
    ap.add_argument("--deposit", type=float, default=0.0,
                    help="cash to deposit (paper mode)")
    ap.add_argument("--days", type=int, default=130,
                    help="trading days to simulate (paper mode)")
    ap.add_argument("--seed", type=int, default=20260610)
    args = ap.parse_args()
    if args.mode == "paper":
        run_paper(args.deposit or 250000.0, args.days, args.seed)
    else:
        run_broker(live=(args.mode == "alpaca-live"))


if __name__ == "__main__":
    main()

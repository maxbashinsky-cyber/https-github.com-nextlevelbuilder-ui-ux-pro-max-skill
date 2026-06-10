#!/usr/bin/env python3
"""
Meridian Alpha Capital — Broker adapter layer
=============================================
One interface, two implementations:

  PaperBroker   — fully local simulated execution (no network, no keys).
                  Fills market orders at the current bar close ± slippage,
                  tracks cash / positions / equity, supports deposits.

  AlpacaBroker  — REST adapter for Alpaca (paper or live) using only the
                  Python stdlib. Requires env vars:
                      APCA_API_KEY_ID, APCA_API_SECRET_KEY
                  Paper endpoint is the default; pass live=True only after
                  you have validated weeks of paper trading.

Both expose:
    get_history(symbol, days)  -> list[float] daily closes (oldest first)
    get_price(symbol)          -> float latest close
    get_positions()            -> {symbol: qty}
    get_cash()                 -> float
    get_equity()               -> float (cash + market value)
    submit_order(symbol, qty)  -> fill dict (positive qty = buy)
    deposit(amount)            -> None (paper only; live deposits happen
                                  at the broker)

SAFETY: AlpacaBroker refuses to start in live mode unless the env var
MERIDIAN_CONFIRM_LIVE=yes is also set.
"""

import json
import os
import time
import urllib.request
import urllib.error


class PaperBroker:
    """Local simulator. `prices` is {symbol: [daily closes, oldest first]};
    a cursor index advances one bar per trading day."""

    SLIPPAGE_BPS = 5  # charged on every fill, both sides

    def __init__(self, prices, start_cash=0.0, history_days=300):
        self.prices = prices
        self.cursor = history_days  # today = index into each price list
        self.cash = float(start_cash)
        self.positions = {}
        self.fills = []

    # ---- market data ----
    def get_history(self, symbol, days):
        s = self.prices[symbol]
        lo = max(0, self.cursor - days + 1)
        return s[lo:self.cursor + 1]

    def get_price(self, symbol):
        return self.prices[symbol][self.cursor]

    def advance_day(self):
        self.cursor += 1
        last = min(len(s) for s in self.prices.values())
        return self.cursor < last

    # ---- account ----
    def get_positions(self):
        return {k: v for k, v in self.positions.items() if abs(v) > 1e-9}

    def get_cash(self):
        return self.cash

    def get_equity(self):
        mv = sum(q * self.get_price(s) for s, q in self.positions.items())
        return self.cash + mv

    def deposit(self, amount):
        self.cash += float(amount)

    # ---- execution ----
    def submit_order(self, symbol, qty):
        if abs(qty) < 1e-9:
            return None
        px = self.get_price(symbol)
        slip = px * self.SLIPPAGE_BPS / 1e4
        fill_px = px + slip if qty > 0 else px - slip
        self.cash -= qty * fill_px
        self.positions[symbol] = self.positions.get(symbol, 0.0) + qty
        fill = {"symbol": symbol, "qty": round(qty, 4),
                "price": round(fill_px, 4), "day": self.cursor,
                "status": "filled", "venue": "paper"}
        self.fills.append(fill)
        return fill


class AlpacaBroker:
    """Minimal stdlib REST adapter for Alpaca Trading + Data APIs.
    Network calls only happen on the machine where you run it."""

    PAPER_URL = "https://paper-api.alpaca.markets"
    LIVE_URL = "https://api.alpaca.markets"
    DATA_URL = "https://data.alpaca.markets"

    def __init__(self, live=False):
        self._load_env_file()
        self.key = os.environ.get("APCA_API_KEY_ID")
        self.secret = os.environ.get("APCA_API_SECRET_KEY")
        if not self.key or not self.secret:
            raise RuntimeError(
                "Set APCA_API_KEY_ID and APCA_API_SECRET_KEY "
                "(create free keys at alpaca.markets — start with PAPER keys).")
        if live and os.environ.get("MERIDIAN_CONFIRM_LIVE") != "yes":
            raise RuntimeError(
                "Refusing to trade a LIVE account: set MERIDIAN_CONFIRM_LIVE=yes "
                "only after validating paper performance.")
        self.base = self.LIVE_URL if live else self.PAPER_URL

    @staticmethod
    def _load_env_file():
        """Optional live/credentials.env (gitignored): KEY=value lines.
        Real env vars always win."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "live", "credentials.env")
        if not os.path.exists(path):
            return
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

    def _req(self, base, path, method="GET", body=None):
        req = urllib.request.Request(
            base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"APCA-API-KEY-ID": self.key,
                     "APCA-API-SECRET-KEY": self.secret,
                     "Content-Type": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Alpaca {e.code}: {e.read().decode()[:300]}")
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Network error talking to Alpaca: {e}")

    # ---- market data ----
    def get_history(self, symbol, days):
        out = self._req(self.DATA_URL,
                        f"/v2/stocks/{symbol}/bars?timeframe=1Day"
                        f"&limit={days}&adjustment=split&feed=iex")
        return [b["c"] for b in out.get("bars", [])]

    def get_price(self, symbol):
        out = self._req(self.DATA_URL,
                        f"/v2/stocks/{symbol}/trades/latest?feed=iex")
        return out["trade"]["p"]

    # ---- account ----
    def get_positions(self):
        out = self._req(self.base, "/v2/positions")
        return {p["symbol"]: float(p["qty"]) for p in out}

    def get_cash(self):
        return float(self._req(self.base, "/v2/account")["cash"])

    def get_equity(self):
        return float(self._req(self.base, "/v2/account")["equity"])

    def deposit(self, amount):
        raise RuntimeError("Fund the account at alpaca.markets; "
                           "deposits cannot be made via the trading API.")

    # ---- execution ----
    def submit_order(self, symbol, qty):
        if abs(qty) < 1e-9:
            return None
        out = self._req(self.base, "/v2/orders", "POST", {
            "symbol": symbol, "qty": str(abs(round(qty, 4))),
            "side": "buy" if qty > 0 else "sell",
            "type": "market", "time_in_force": "day"})
        return {"symbol": symbol, "qty": round(qty, 4),
                "price": None, "day": out.get("submitted_at"),
                "status": out.get("status", "submitted"), "venue": self.base}

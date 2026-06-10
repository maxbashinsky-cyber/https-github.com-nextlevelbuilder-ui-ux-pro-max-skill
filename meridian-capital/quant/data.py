#!/usr/bin/env python3
"""
Meridian Alpha Capital — Real market data loader
================================================
Pulls daily adjusted closes from Yahoo Finance's public chart API
(stdlib only, polite: one request per symbol, on-disk cache).

    from data import fetch_history, fetch_universe
    dates, closes = fetch_history("SPY", years=12)
    aligned = fetch_universe(["SPY","TLT","GLD","KO","PEP","PUTW"], years=12)

Cache lives in live/marketdata/<SYMBOL>.json and is refreshed when
older than MAX_CACHE_AGE_HOURS (or pass refresh=True).
"""

import json
import math
import os
import time
import urllib.request
import urllib.error

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "live", "marketdata")
MAX_CACHE_AGE_HOURS = 12
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _cache_path(symbol):
    return os.path.join(CACHE_DIR, symbol.upper() + ".json")


def _load_cache(symbol):
    p = _cache_path(symbol)
    if not os.path.exists(p):
        return None
    age_h = (time.time() - os.path.getmtime(p)) / 3600.0
    if age_h > MAX_CACHE_AGE_HOURS:
        return None
    with open(p) as f:
        return json.load(f)


def _save_cache(symbol, payload):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(symbol), "w") as f:
        json.dump(payload, f)


def fetch_history(symbol, years=12, refresh=False):
    """Returns (dates, adj_closes) — ISO date strings and floats,
    oldest first, missing bars dropped."""
    if not refresh:
        cached = _load_cache(symbol)
        if cached:
            return cached["dates"], cached["closes"]

    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range={years}y&interval=1d&events=div%2Csplit")
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    last_err = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = json.loads(r.read().decode())
            break
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            last_err = e
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"Could not fetch {symbol} from Yahoo: {last_err}")

    res = raw["chart"]["result"][0]
    ts = res["timestamp"]
    quote = res["indicators"]["quote"][0]["close"]
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose", quote)
    dates, closes = [], []
    for t, c in zip(ts, adj):
        if c is None or (isinstance(c, float) and math.isnan(c)):
            continue
        dates.append(time.strftime("%Y-%m-%d", time.gmtime(t)))
        closes.append(round(float(c), 4))
    if len(closes) < 200:
        raise RuntimeError(f"{symbol}: only {len(closes)} bars returned")
    _save_cache(symbol, {"dates": dates, "closes": closes,
                         "fetched": time.strftime("%Y-%m-%d %H:%M UTC",
                                                  time.gmtime())})
    time.sleep(1.0)  # be polite between symbols
    return dates, closes


def fetch_universe(symbols, years=12, refresh=False):
    """Fetch all symbols and align on common trading dates.
    Returns (dates, {symbol: closes}) — same length everywhere."""
    series = {}
    for s in symbols:
        d, c = fetch_history(s, years=years, refresh=refresh)
        series[s] = dict(zip(d, c))
    common = set.intersection(*(set(v) for v in series.values()))
    dates = sorted(common)
    return dates, {s: [series[s][d] for d in dates] for s in symbols}


if __name__ == "__main__":
    syms = ["SPY", "TLT", "GLD", "KO", "PEP", "PUTW"]
    dates, px = fetch_universe(syms)
    print(f"Aligned {len(dates)} common trading days: "
          f"{dates[0]} → {dates[-1]}")
    for s in syms:
        print(f"  {s:>5}: first {px[s][0]:>9.2f}   last {px[s][-1]:>9.2f}")

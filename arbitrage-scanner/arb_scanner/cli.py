"""Command-line entry point for the arbitrage scanner.

Examples:
    python -m arb_scanner --mock                 # offline demo
    python -m arb_scanner                         # live scan (needs ODDS_API_KEY)
    python -m arb_scanner --bankroll 500 --min-margin 0.01
    python -m arb_scanner --sports soccer_epl,basketball_nba --dry-run
"""

import argparse
import sys

from .config import Config
from .notify import build_notifiers, dispatch
from .providers import ProviderError, build_provider
from .report import as_text
from .scanner import scan_events


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="arb_scanner",
        description="Scan sportsbooks for arbitrage (sure-bet) opportunities and notify.",
    )
    p.add_argument("--mock", action="store_true",
                   help="Use built-in demo data instead of the live API (no key needed).")
    p.add_argument("--bankroll", type=float, default=None,
                   help="Total stake to split across legs (default from env or 1000).")
    p.add_argument("--min-margin", type=float, default=None,
                   help="Minimum profit margin to report, e.g. 0.01 for 1%% (default 0.5%%).")
    p.add_argument("--sports", default=None,
                   help="Comma-separated sport keys (default: all active sports).")
    p.add_argument("--markets", default=None,
                   help="Comma-separated markets, e.g. h2h,totals (default h2h).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the report but don't send remote notifications.")
    p.add_argument("--env", default=".env", help="Path to a .env file (optional).")
    return p


def run(argv=None):
    args = build_arg_parser().parse_args(argv)

    config = Config.from_env(args.env)
    if args.mock:
        config.use_mock = True
    if args.bankroll is not None:
        config.bankroll = args.bankroll
    if args.min_margin is not None:
        config.min_margin = args.min_margin
    if args.sports is not None:
        config.sport_keys = [s.strip() for s in args.sports.split(",") if s.strip()]
    if args.markets is not None:
        config.markets = args.markets

    print("→ Fetching odds...")
    try:
        provider = build_provider(config)
        events = provider.fetch_events(config.sport_keys or None)
    except ProviderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    remaining = getattr(provider, "requests_remaining", None)
    print(f"→ Scanning {len(events)} events"
          + (f" (API requests remaining: {remaining})" if remaining else ""))

    opportunities = scan_events(
        events,
        bankroll=config.bankroll,
        min_margin=config.min_margin,
        markets=config.market_list if config.markets else None,
    )

    print()
    print(as_text(opportunities, bankroll=config.bankroll))
    print()

    if args.dry_run:
        print("(dry-run: skipping remote notifications)")
        return 0

    # Only ping remote channels when there's something worth seeing.
    if not opportunities:
        print("Nothing to notify. Done.")
        return 0

    notifiers = build_notifiers(config)
    remote = [n for n in notifiers if n.name != "console"]
    if not remote:
        print("No remote notification channels configured (console only).")
        return 0

    results = dispatch(remote, opportunities)
    for name, ok, err in results:
        print(f"  {name}: {'sent' if ok else 'FAILED — ' + (err or '')}")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

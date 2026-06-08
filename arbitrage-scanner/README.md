# Arbitrage Scanner

A self-contained tool (inspired by [oddpool.com](https://oddpool.com)) that scans
odds across many sportsbooks, finds **price gaps that arbitrage out into a
guaranteed profit ("sure bets")**, and sends you a **daily notification** with the
stakes to place and a link to each book.

> **What is a betting arbitrage?** Different books price the same event slightly
> differently. When the best available price for *every* outcome implies a total
> probability below 100%, you can stake all outcomes (at different books) so that
> *whatever happens* you come out ahead. The shortfall below 100% is your margin.

- **Zero dependencies** — pure Python 3 standard library (matches this repo's philosophy).
- **Data source:** [The Odds API](https://the-odds-api.com) aggregates dozens of
  books, so we never scrape individual sportsbooks (fragile + against their ToS).
- **Notifications:** Telegram, Discord, Slack, or console — any combination.
- **Daily automation:** ships with a GitHub Actions workflow on a cron schedule.

## Quick start

```bash
cd arbitrage-scanner

# 1. Try it offline with built-in demo data (no API key needed):
python3 -m arb_scanner --mock

# 2. Go live: get a free key at https://the-odds-api.com
cp .env.example .env        # then edit ODDS_API_KEY (+ optional notifiers)
python3 -m arb_scanner

# Useful flags:
python3 -m arb_scanner --bankroll 500 --min-margin 0.01          # only 1%+ edges
python3 -m arb_scanner --sports soccer_epl,basketball_nba         # specific sports
python3 -m arb_scanner --markets h2h,totals --dry-run            # preview, no send
```

### Example output

```
Found 1 arbitrage opportunity (best 1.48% margin).

1. [EPL] Arsenal vs Chelsea — Moneyline (H2H)
   Margin 1.48%  |  profit ~14.84 on 1000 bankroll  |  starts Tue 09 Jun 16:00 UTC
     • Arsenal: stake 451.04 @ 2.25 on DraftKings (returns 1014.84)
     • Chelsea: stake 267.06 @ 3.80 on FanDuel   (returns 1014.83)
     • Draw:    stake 281.90 @ 3.60 on DraftKings (returns 1014.84)
   link: https://sportsbook.draftkings.com
```

Stake `1000`, get back `~1014.84` no matter who wins → a locked-in `~1.48%`.

## How it works

```
providers.py   fetch normalized events from The Odds API (or MockProvider)
     │
scanner.py     for each event+market, take the BEST price per outcome across books
     │
arbitrage.py   implied_prob = Σ 1/odds.  If < 1.0 → arbitrage.
     │            margin = 1/implied_prob − 1 ;  stake_i = bankroll·(1/odds_i)/implied_prob
     │
report.py      format as text / markdown (with stakes + clickable book links)
     │
notify.py      push to Telegram / Discord / Slack (console always prints)
```

The maths lives in `arb_scanner/arbitrage.py` and is fully unit-tested.

## Configuration

All settings come from environment variables (or a `.env` file). See
[`.env.example`](.env.example) for the full list. Key ones:

| Variable | Default | Meaning |
|---|---|---|
| `ODDS_API_KEY` | — | Free key from the-odds-api.com |
| `ODDS_REGIONS` | `us,uk,eu,au` | Which books to include |
| `ODDS_MARKETS` | `h2h` | `h2h`, `spreads`, `totals` |
| `BANKROLL` | `1000` | Stake split across legs |
| `MIN_MARGIN` | `0.005` | Ignore edges below 0.5% (stale-price noise) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | Telegram channel |
| `DISCORD_WEBHOOK_URL` | — | Discord channel |
| `SLACK_WEBHOOK_URL` | — | Slack channel |

## Daily notifications

### Option A — GitHub Actions (recommended, free)

[`.github/workflows/daily-scan.yml`](.github/workflows/daily-scan.yml) runs the
scanner every day at 13:00 UTC. Add your secrets under
**Settings → Secrets and variables → Actions**:

- Secrets: `ODDS_API_KEY`, and any of `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
  `DISCORD_WEBHOOK_URL`, `SLACK_WEBHOOK_URL`.
- Variables (optional): `BANKROLL`, `MIN_MARGIN`, `ODDS_REGIONS`, `SPORT_KEYS`.

Trigger a test run anytime from the **Actions** tab → *Daily arbitrage scan* →
*Run workflow*.

### Option B — cron on any server

```cron
0 13 * * *  cd /path/to/arbitrage-scanner && /usr/bin/python3 -m arb_scanner >> scan.log 2>&1
```

## Tests

```bash
python3 tests/test_arbitrage.py     # no test framework required
```

## Notes & caveats

- **Odds move fast.** By the time you place all legs, a price may have shifted.
  Treat reported margins as a snapshot; verify on the book before staking.
- **Free API tier is limited** (500 req/month). Scanning all active sports once a
  day stays comfortably within it; restrict with `SPORT_KEYS` if needed.
- **Account limits.** Books may restrict accounts that arb heavily — this is a
  research/education tool. Bet responsibly and within the law in your jurisdiction.
- The Odds API doesn't expose deep links to a specific bet slip, so notifications
  link to each book's homepage plus the exact matchup and stakes to enter.

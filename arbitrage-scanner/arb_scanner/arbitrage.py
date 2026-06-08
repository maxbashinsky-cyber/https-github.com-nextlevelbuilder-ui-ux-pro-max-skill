"""Core arbitrage math.

An arbitrage ("sure bet") exists when you can cover every outcome of an event
by betting at the best price offered across different bookmakers, such that the
total implied probability is below 100%. The shortfall is your guaranteed margin.

All odds here are *decimal* odds (e.g. 2.50 means you get 2.5x your stake back,
including the stake). This is the format The Odds API returns and the easiest to
reason about: implied probability = 1 / decimal_odds.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class BestPrice:
    """The best available price for a single outcome of an event."""

    outcome: str          # e.g. "Real Madrid", "Draw", "Over 2.5"
    odds: float           # best decimal odds found
    bookmaker: str        # human title of the book offering it, e.g. "Pinnacle"
    bookmaker_key: str    # machine key, e.g. "pinnacle"


@dataclass
class Leg:
    """One leg of an arbitrage: where and how much to bet on an outcome."""

    outcome: str
    odds: float
    bookmaker: str
    bookmaker_key: str
    stake: float          # rounded stake for the configured bankroll
    payout: float         # stake * odds (what this leg returns if it wins)


@dataclass
class Opportunity:
    """A detected arbitrage opportunity for one event/market."""

    event_id: str
    sport_key: str
    sport_title: str
    match: str                       # "Home vs Away"
    market: str                      # e.g. "h2h" (moneyline), "totals"
    commence_time: Optional[datetime]
    margin: float                    # guaranteed profit fraction, e.g. 0.032 = 3.2%
    implied_prob: float              # sum of 1/odds across the chosen legs (< 1.0)
    legs: list = field(default_factory=list)
    event_url: Optional[str] = None  # link a human can click to inspect/place bets

    @property
    def margin_pct(self) -> float:
        return self.margin * 100.0

    @property
    def num_books(self) -> int:
        return len({leg.bookmaker_key for leg in self.legs})


def implied_probability(best_prices) -> float:
    """Sum of inverse odds. < 1.0 means a guaranteed-profit arbitrage exists."""
    return sum(1.0 / bp.odds for bp in best_prices)


def best_prices_for_market(outcome_quotes):
    """Reduce many bookmaker quotes to the single best price per outcome.

    `outcome_quotes` is an iterable of (outcome, odds, bookmaker_title,
    bookmaker_key). Returns a dict outcome -> BestPrice keeping the highest odds
    (best for the bettor) for each outcome.
    """
    best = {}
    for outcome, odds, book_title, book_key in outcome_quotes:
        if odds is None or odds <= 1.0:
            continue  # invalid / non-sensible price
        current = best.get(outcome)
        if current is None or odds > current.odds:
            best[outcome] = BestPrice(outcome, float(odds), book_title, book_key)
    return best


def allocate_stakes(best_prices, bankroll, round_to=0.01):
    """Split `bankroll` across outcomes so every result returns roughly the same.

    stake_i = bankroll * (1/odds_i) / implied_prob
    """
    arb = implied_probability(best_prices)
    legs = []
    for bp in best_prices:
        raw_stake = bankroll * (1.0 / bp.odds) / arb
        stake = round(raw_stake / round_to) * round_to
        legs.append(
            Leg(
                outcome=bp.outcome,
                odds=bp.odds,
                bookmaker=bp.bookmaker,
                bookmaker_key=bp.bookmaker_key,
                stake=round(stake, 2),
                payout=round(stake * bp.odds, 2),
            )
        )
    return legs


def find_arbitrage_in_market(
    *,
    event_id,
    sport_key,
    sport_title,
    match,
    market,
    commence_time,
    outcome_quotes,
    bankroll,
    min_margin=0.0,
    event_url=None,
):
    """Return an Opportunity if the market's best prices arb out, else None.

    A valid arbitrage needs at least 2 distinct outcomes, each with a price.
    """
    best = best_prices_for_market(outcome_quotes)
    if len(best) < 2:
        return None

    best_prices = list(best.values())
    arb = implied_probability(best_prices)
    if arb <= 0:
        return None

    margin = (1.0 / arb) - 1.0
    if arb >= 1.0 or margin < min_margin:
        return None  # no edge, or below the user's threshold

    legs = allocate_stakes(best_prices, bankroll)
    return Opportunity(
        event_id=event_id,
        sport_key=sport_key,
        sport_title=sport_title,
        match=match,
        market=market,
        commence_time=commence_time,
        margin=margin,
        implied_prob=arb,
        legs=legs,
        event_url=event_url,
    )

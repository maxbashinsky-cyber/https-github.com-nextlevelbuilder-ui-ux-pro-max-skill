"""Turn normalized events into ranked arbitrage opportunities."""

from datetime import datetime, timezone

from .arbitrage import find_arbitrage_in_market

# Homepages for the books we link to in notifications. The Odds API doesn't
# expose deep links to a specific bet slip, so we link to the book's site (where
# the user logs in and finds the event) plus the matchup text. Unknown keys fall
# back to a web search for the book name.
BOOKMAKER_URLS = {
    "pinnacle": "https://www.pinnacle.com",
    "draftkings": "https://sportsbook.draftkings.com",
    "fanduel": "https://sportsbook.fanduel.com",
    "betmgm": "https://sports.betmgm.com",
    "caesars": "https://www.caesars.com/sportsbook-and-casino",
    "williamhill_us": "https://www.williamhill.com",
    "betfair": "https://www.betfair.com",
    "bet365": "https://www.bet365.com",
    "unibet": "https://www.unibet.com",
    "pointsbetus": "https://www.pointsbet.com",
    "betonlineag": "https://www.betonline.ag",
    "bovada": "https://www.bovada.lv",
    "betrivers": "https://www.betrivers.com",
    "williamhill": "https://www.williamhill.com",
    "matchbook": "https://www.matchbook.com",
    "ladbrokes_uk": "https://sports.ladbrokes.com",
    "skybet": "https://m.skybet.com",
    "paddypower": "https://www.paddypower.com",
}

MARKET_LABELS = {
    "h2h": "Moneyline (H2H)",
    "spreads": "Point spread",
    "totals": "Totals (O/U)",
    "outrights": "Outrights",
}


def bookmaker_url(book_key, book_title):
    if book_key in BOOKMAKER_URLS:
        return BOOKMAKER_URLS[book_key]
    query = (book_title or book_key or "sportsbook").replace(" ", "+")
    return f"https://www.google.com/search?q={query}+sportsbook"


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _outcome_quotes(event, market_key):
    """Yield (outcome, odds, book_title, book_key) for one market across books."""
    for book in event.get("bookmakers", []):
        book_key = book.get("key", "")
        book_title = book.get("title", book_key)
        for market in book.get("markets", []):
            if market.get("key") != market_key:
                continue
            for outcome in market.get("outcomes", []):
                name = outcome.get("name")
                price = outcome.get("price")
                # Totals/spreads carry a "point"; keep it in the outcome label so
                # we don't arb "Over 2.5" against "Over 3.5" as if equivalent.
                point = outcome.get("point")
                if point is not None:
                    name = f"{name} {point}"
                if name is not None and price is not None:
                    yield (name, price, book_title, book_key)


def _markets_in_event(event):
    keys = []
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            k = market.get("key")
            if k and k not in keys:
                keys.append(k)
    return keys


def scan_events(events, *, bankroll, min_margin=0.0, markets=None):
    """Return Opportunities sorted by margin (best first)."""
    opportunities = []
    for event in events:
        match = f"{event.get('home_team', '?')} vs {event.get('away_team', '?')}"
        commence = _parse_time(event.get("commence_time"))
        target_markets = markets or _markets_in_event(event)
        for market_key in target_markets:
            quotes = list(_outcome_quotes(event, market_key))
            if not quotes:
                continue
            opp = find_arbitrage_in_market(
                event_id=event.get("id", ""),
                sport_key=event.get("sport_key", ""),
                sport_title=event.get("sport_title", ""),
                match=match,
                market=MARKET_LABELS.get(market_key, market_key),
                commence_time=commence,
                outcome_quotes=quotes,
                bankroll=bankroll,
                min_margin=min_margin,
            )
            if opp:
                # Attach a clickable link to the best book in the arb.
                top_leg = max(opp.legs, key=lambda leg: leg.stake)
                opp.event_url = bookmaker_url(top_leg.bookmaker_key, top_leg.bookmaker)
                opportunities.append(opp)

    opportunities.sort(key=lambda o: o.margin, reverse=True)
    return opportunities

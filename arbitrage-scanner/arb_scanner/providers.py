"""Odds data providers.

We don't scrape individual sportsbooks (fragile, often against their ToS, and a
maintenance nightmare). Instead we consume an aggregator that already collects
prices from dozens of books. The default is The Odds API (the-odds-api.com),
which has a generous free tier and a stable JSON schema.

Each provider yields a normalized list of "events", where every event is a dict:

    {
      "id": str,
      "sport_key": str,
      "sport_title": str,
      "commence_time": str (ISO-8601),
      "home_team": str,
      "away_team": str,
      "bookmakers": [
        {"key": str, "title": str,
         "markets": [{"key": str, "outcomes": [{"name": str, "price": float}]}]}
      ]
    }

The scanner only depends on that shape, so swapping providers is trivial.
"""

import json
import urllib.error
import urllib.parse
import urllib.request


class ProviderError(RuntimeError):
    pass


class TheOddsAPIProvider:
    """Fetch odds from https://the-odds-api.com (v4)."""

    BASE = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key, regions="us,uk,eu,au", markets="h2h", timeout=20):
        if not api_key:
            raise ProviderError(
                "Missing API key. Set ODDS_API_KEY (get a free one at "
                "https://the-odds-api.com)."
            )
        self.api_key = api_key
        self.regions = regions
        self.markets = markets
        self.timeout = timeout
        # API quota counters, populated from response headers after each call.
        self.requests_remaining = None
        self.requests_used = None

    def _get(self, path, params):
        params = {**params, "apiKey": self.api_key}
        url = f"{self.BASE}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "arb-scanner/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self.requests_remaining = resp.headers.get("x-requests-remaining")
                self.requests_used = resp.headers.get("x-requests-used")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise ProviderError(f"HTTP {exc.code} from The Odds API: {body}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"Network error reaching The Odds API: {exc}") from exc

    def list_sports(self, all_sports=False):
        params = {"all": "true"} if all_sports else {}
        return self._get("/sports", params)

    def active_sport_keys(self):
        """Sport keys that currently have games on offer."""
        return [s["key"] for s in self.list_sports() if s.get("active")]

    def odds_for_sport(self, sport_key, odds_format="decimal"):
        return self._get(
            f"/sports/{sport_key}/odds",
            {
                "regions": self.regions,
                "markets": self.markets,
                "oddsFormat": odds_format,
                "dateFormat": "iso",
            },
        )

    def fetch_events(self, sport_keys=None):
        """Return normalized events for the given sports (or all active ones)."""
        if not sport_keys:
            sport_keys = self.active_sport_keys()
        events = []
        for key in sport_keys:
            try:
                events.extend(self.odds_for_sport(key))
            except ProviderError as exc:
                # One bad sport key shouldn't abort the whole scan.
                print(f"  ! skipping {key}: {exc}")
        return events


class MockProvider:
    """Deterministic offline provider so the tool runs with no API key.

    Includes one event that arbs out and one that doesn't, useful for demos,
    tests, and `--dry-run` previews of the notification format.
    """

    requests_remaining = "unlimited (mock)"
    requests_used = "0"

    def fetch_events(self, sport_keys=None):
        return [
            {
                "id": "mock-arb-001",
                "sport_key": "soccer_epl",
                "sport_title": "EPL",
                "commence_time": "2026-06-09T16:00:00Z",
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "bookmakers": [
                    {"key": "pinnacle", "title": "Pinnacle", "markets": [
                        {"key": "h2h", "outcomes": [
                            {"name": "Arsenal", "price": 2.10},
                            {"name": "Chelsea", "price": 3.40},
                            {"name": "Draw", "price": 3.30},
                        ]},
                    ]},
                    {"key": "draftkings", "title": "DraftKings", "markets": [
                        {"key": "h2h", "outcomes": [
                            {"name": "Arsenal", "price": 2.25},
                            {"name": "Chelsea", "price": 3.10},
                            {"name": "Draw", "price": 3.60},
                        ]},
                    ]},
                    {"key": "fanduel", "title": "FanDuel", "markets": [
                        {"key": "h2h", "outcomes": [
                            {"name": "Arsenal", "price": 2.05},
                            {"name": "Chelsea", "price": 3.80},
                            {"name": "Draw", "price": 3.45},
                        ]},
                    ]},
                ],
            },
            {
                "id": "mock-noarb-002",
                "sport_key": "basketball_nba",
                "sport_title": "NBA",
                "commence_time": "2026-06-09T23:30:00Z",
                "home_team": "Lakers",
                "away_team": "Celtics",
                "bookmakers": [
                    {"key": "pinnacle", "title": "Pinnacle", "markets": [
                        {"key": "h2h", "outcomes": [
                            {"name": "Lakers", "price": 1.90},
                            {"name": "Celtics", "price": 1.95},
                        ]},
                    ]},
                    {"key": "betmgm", "title": "BetMGM", "markets": [
                        {"key": "h2h", "outcomes": [
                            {"name": "Lakers", "price": 1.88},
                            {"name": "Celtics", "price": 1.98},
                        ]},
                    ]},
                ],
            },
        ]


def build_provider(config):
    if config.use_mock:
        return MockProvider()
    return TheOddsAPIProvider(
        api_key=config.odds_api_key,
        regions=config.regions,
        markets=config.markets,
    )

"""Configuration loaded from environment variables (and an optional .env file)."""

import os
from dataclasses import dataclass, field


def _load_dotenv(path=".env"):
    """Minimal .env loader (KEY=VALUE per line). No external dependency."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _env_float(name, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_list(name, default):
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Config:
    # Data source
    odds_api_key: str = ""
    use_mock: bool = False
    regions: str = "us,uk,eu,au"
    markets: str = "h2h"                      # comma list for the API request
    sport_keys: list = field(default_factory=list)  # empty = all active sports

    # Arbitrage filters
    bankroll: float = 1000.0
    min_margin: float = 0.005                 # 0.5% — ignore noise / stale prices

    # Notifications (any combination; empty = console only)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""
    slack_webhook_url: str = ""

    @classmethod
    def from_env(cls, dotenv_path=".env"):
        _load_dotenv(dotenv_path)
        return cls(
            odds_api_key=os.environ.get("ODDS_API_KEY", ""),
            use_mock=os.environ.get("USE_MOCK", "").lower() in ("1", "true", "yes"),
            regions=os.environ.get("ODDS_REGIONS", "us,uk,eu,au"),
            markets=os.environ.get("ODDS_MARKETS", "h2h"),
            sport_keys=_env_list("SPORT_KEYS", []),
            bankroll=_env_float("BANKROLL", 1000.0),
            min_margin=_env_float("MIN_MARGIN", 0.005),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", ""),
            slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL", ""),
        )

    @property
    def market_list(self):
        return [m.strip() for m in self.markets.split(",") if m.strip()]

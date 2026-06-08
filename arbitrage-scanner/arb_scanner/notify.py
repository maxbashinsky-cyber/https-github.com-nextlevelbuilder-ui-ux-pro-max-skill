"""Notification channels: Telegram, Discord, Slack, and console.

All transports use the stdlib (urllib) so the tool stays dependency-free. Each
notifier is best-effort: a failure on one channel is reported but never aborts
the scan or the other channels.
"""

import json
import urllib.error
import urllib.request

from . import report


def _post_json(url, payload, timeout=15):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json",
                                 "User-Agent": "arb-scanner/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


class ConsoleNotifier:
    name = "console"

    def __init__(self, bankroll):
        self.bankroll = bankroll

    def send(self, opportunities):
        print(report.as_text(opportunities, bankroll=self.bankroll))
        return True


class TelegramNotifier:
    name = "telegram"

    def __init__(self, bot_token, chat_id, bankroll):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.bankroll = bankroll

    def send(self, opportunities):
        text = report.as_markdown(opportunities, bankroll=self.bankroll, limit=10)
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        _post_json(url, {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        })
        return True


class DiscordNotifier:
    name = "discord"

    def __init__(self, webhook_url, bankroll):
        self.webhook_url = webhook_url
        self.bankroll = bankroll

    def send(self, opportunities):
        text = report.as_markdown(opportunities, bankroll=self.bankroll, limit=10)
        # Discord caps message content at 2000 chars.
        _post_json(self.webhook_url, {"content": text[:1990]})
        return True


class SlackNotifier:
    name = "slack"

    def __init__(self, webhook_url, bankroll):
        self.webhook_url = webhook_url
        self.bankroll = bankroll

    def send(self, opportunities):
        text = report.as_text(opportunities, bankroll=self.bankroll, limit=10)
        _post_json(self.webhook_url, {"text": text})
        return True


def build_notifiers(config):
    """Pick channels from config. Always includes console as a fallback."""
    notifiers = [ConsoleNotifier(config.bankroll)]
    if config.telegram_bot_token and config.telegram_chat_id:
        notifiers.append(TelegramNotifier(
            config.telegram_bot_token, config.telegram_chat_id, config.bankroll))
    if config.discord_webhook_url:
        notifiers.append(DiscordNotifier(config.discord_webhook_url, config.bankroll))
    if config.slack_webhook_url:
        notifiers.append(SlackNotifier(config.slack_webhook_url, config.bankroll))
    return notifiers


def dispatch(notifiers, opportunities):
    """Send to every channel; return list of (name, ok, error)."""
    results = []
    for notifier in notifiers:
        try:
            notifier.send(opportunities)
            results.append((notifier.name, True, None))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            results.append((notifier.name, False, str(exc)))
    return results

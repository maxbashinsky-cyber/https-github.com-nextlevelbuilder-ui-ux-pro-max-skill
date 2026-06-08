"""Format opportunities for humans (plain text, markdown, Slack blocks)."""

from datetime import datetime, timezone


def _fmt_time(dt):
    if not dt:
        return "TBD"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%a %d %b %H:%M UTC")


def headline(opportunities):
    n = len(opportunities)
    if n == 0:
        return "No arbitrage opportunities found in the current scan."
    best = opportunities[0].margin_pct
    return f"Found {n} arbitrage {'opportunity' if n == 1 else 'opportunities'} (best {best:.2f}% margin)."


def as_text(opportunities, *, bankroll, limit=None):
    """Plain-text report (used for console + email-ish channels)."""
    lines = [headline(opportunities)]
    shown = opportunities if limit is None else opportunities[:limit]
    for i, opp in enumerate(shown, 1):
        profit = bankroll * opp.margin
        lines.append("")
        lines.append(f"{i}. [{opp.sport_title}] {opp.match} — {opp.market}")
        lines.append(
            f"   Margin {opp.margin_pct:.2f}%  |  profit ~{profit:.2f} on {bankroll:.0f} bankroll  |  starts {_fmt_time(opp.commence_time)}"
        )
        for leg in opp.legs:
            lines.append(
                f"     • {leg.outcome}: stake {leg.stake:.2f} @ {leg.odds:.2f} on {leg.bookmaker} "
                f"(returns {leg.payout:.2f})"
            )
        if opp.event_url:
            lines.append(f"   link: {opp.event_url}")
    if limit is not None and len(opportunities) > limit:
        lines.append("")
        lines.append(f"...and {len(opportunities) - limit} more.")
    return "\n".join(lines)


def as_markdown(opportunities, *, bankroll, limit=None):
    """Markdown report (Discord / Telegram render this nicely)."""
    lines = [f"*{headline(opportunities)}*"]
    shown = opportunities if limit is None else opportunities[:limit]
    for i, opp in enumerate(shown, 1):
        profit = bankroll * opp.margin
        link = f" — [book]({opp.event_url})" if opp.event_url else ""
        lines.append("")
        lines.append(
            f"*{i}. {opp.match}* ({opp.sport_title} · {opp.market}){link}"
        )
        lines.append(
            f"Margin *{opp.margin_pct:.2f}%* · profit ~{profit:.2f}/{bankroll:.0f} · {_fmt_time(opp.commence_time)}"
        )
        for leg in opp.legs:
            lines.append(
                f"  • {leg.outcome}: `{leg.stake:.2f}` @ {leg.odds:.2f} — {leg.bookmaker}"
            )
    if limit is not None and len(opportunities) > limit:
        lines.append("")
        lines.append(f"_…and {len(opportunities) - limit} more._")
    return "\n".join(lines)

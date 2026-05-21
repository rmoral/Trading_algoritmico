"""Set the active trading asset for the bot.

The bot trades a single operator-chosen ticker per session and idles
when none is selected. The web app is the normal way to choose it;
this script is the headless equivalent for paper-testing before the
dashboard is in the loop.

Refused while a position is open (the bot must be flat to switch
assets) — same rule the web app enforces.

Pass `--earnings` to mark the asset as inside an earnings blackout
window; the risk manager then refuses entries on it.

Usage:
    uv run python scripts/set_active_asset.py AAPL
    uv run python scripts/set_active_asset.py AAPL --earnings
"""

from __future__ import annotations

import asyncio
import sys

from tradingbot.logging_setup import configure_logging, get_logger
from tradingbot.persistence.database import create_engine, create_session_factory
from tradingbot.persistence.repositories import PositionsRepository
from tradingbot.settings import LogFormat, get_settings
from tradingbot_api.asset_service import AssetChangeBlockedError, set_active_asset


async def main() -> int:
    args = sys.argv[1:]
    is_earnings_window = "--earnings" in args
    positional = [a for a in args if a != "--earnings"]
    if len(positional) != 1 or not positional[0].strip():
        print(
            "usage: uv run python scripts/set_active_asset.py SYMBOL [--earnings]"
        )
        return 1
    symbol = positional[0].strip().upper()
    if len(symbol) > 16 or not symbol.isalnum():
        print(f"invalid symbol: {symbol!r}")
        return 1

    settings = get_settings()
    configure_logging(
        settings.model_copy(update={"log_format": LogFormat.CONSOLE})
    )
    log = get_logger("set_active_asset")

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    positions_repo = PositionsRepository(factory)
    try:
        async with factory() as session:
            try:
                selection = await set_active_asset(
                    session,
                    symbol=symbol,
                    actor="cli",
                    positions_repo=positions_repo,
                    is_earnings_window=is_earnings_window,
                )
            except AssetChangeBlockedError as exc:
                log.error("active_asset_change_blocked", error=str(exc))
                return 1
            await session.commit()
            log.info(
                "active_asset_set",
                symbol=selection.symbol,
                is_earnings_window=selection.is_earnings_window,
            )
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

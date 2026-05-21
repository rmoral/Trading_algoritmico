"""Set the active trading asset for the bot.

The bot trades a single operator-chosen ticker per session and idles
when none is selected. The web app is the normal way to choose it;
this script is the headless equivalent for paper-testing before the
dashboard is in the loop.

Refused while a position is open (the bot must be flat to switch
assets) — same rule the web app enforces.

Usage:
    uv run python scripts/set_active_asset.py AAPL
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
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("usage: uv run python scripts/set_active_asset.py SYMBOL")
        return 1
    symbol = sys.argv[1].strip().upper()
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
                )
            except AssetChangeBlockedError as exc:
                log.error("active_asset_change_blocked", error=str(exc))
                return 1
            await session.commit()
            log.info("active_asset_set", symbol=selection.symbol)
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

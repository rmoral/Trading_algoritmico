"""Bot entry point.

Wires settings -> logging -> kill switch -> metrics server -> IBKR
connector + Telegram bot, runs the connector and the bot concurrently
until a SIGTERM or SIGINT, then drains cleanly.
"""

from __future__ import annotations

import asyncio
import signal
import sys

from tradingbot.connector import IBClient
from tradingbot.logging_setup import configure_logging, get_logger
from tradingbot.monitoring import KillSwitch, TelegramBot, TelegramHandlers
from tradingbot.monitoring.metrics import start_metrics_server
from tradingbot.persistence.database import create_engine, create_session_factory
from tradingbot.persistence.repositories import PnLRepository, PositionsRepository
from tradingbot.settings import get_settings


async def amain() -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger(__name__)
    log.info(
        "bot_startup",
        is_live=settings.is_live,
        ibkr_host=settings.ibkr_host,
        ibkr_port=settings.ibkr_port,
        kill_switch_at_startup=settings.kill_switch,
    )

    kill_switch = KillSwitch(initially_tripped=settings.kill_switch)
    start_metrics_server()

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    positions_repo = PositionsRepository(session_factory)
    pnl_repo = PnLRepository(session_factory)

    ib_client = IBClient(settings)

    telegram_bot: TelegramBot | None = None
    if settings.telegram_bot_token.get_secret_value():
        handlers = TelegramHandlers(
            ib_client=ib_client,
            kill_switch=kill_switch,
            positions=positions_repo,
            pnl=pnl_repo,
        )
        telegram_bot = TelegramBot(settings, handlers)
        log.info("telegram_bot_enabled")
    else:
        log.warning("telegram_bot_disabled_no_token")

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    ib_task = asyncio.create_task(ib_client.run(), name="ib_client")
    tg_task: asyncio.Task[None] | None = None
    if telegram_bot is not None:
        tg_task = asyncio.create_task(telegram_bot.start(), name="telegram_bot")

    await shutdown.wait()
    log.info("shutdown_signaled")

    ib_client.stop()
    if telegram_bot is not None:
        await telegram_bot.stop()

    await ib_task
    if tg_task is not None and not tg_task.done():
        await tg_task

    await engine.dispose()
    log.info("shutdown_complete")
    return 0


def main() -> int:
    """Synchronous entry point used by the console script."""
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())

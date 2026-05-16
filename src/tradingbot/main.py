"""Bot entry point.

Wires settings -> logging -> kill switch -> metrics server -> IBKR
connector + Telegram bot, runs the connector and the bot concurrently
until a SIGTERM or SIGINT, then drains cleanly.
"""

from __future__ import annotations

import asyncio
import signal
import sys

from redis.asyncio import Redis

from tradingbot.connector import AccountStateLogger, IBClient
from tradingbot.logging_setup import configure_logging, get_logger
from tradingbot.monitoring import KillSwitch, TelegramBot, TelegramHandlers
from tradingbot.monitoring.metrics import start_metrics_server
from tradingbot.persistence.database import create_engine, create_session_factory
from tradingbot.persistence.repositories import PnLRepository, PositionsRepository
from tradingbot.settings import get_settings
from tradingbot.state import (
    BotStateBroadcaster,
    BotStatePublisher,
    KillSwitchListener,
)


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
    account_logger = AccountStateLogger(ib_client)

    redis = Redis.from_url(settings.redis_url)
    state_publisher = BotStatePublisher(redis)
    state_broadcaster = BotStateBroadcaster(ib_client, kill_switch, state_publisher)
    kill_listener = KillSwitchListener(redis, kill_switch)

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
    account_task = asyncio.create_task(account_logger.run(), name="account_state_logger")
    broadcaster_task = asyncio.create_task(
        state_broadcaster.run(), name="bot_state_broadcaster"
    )
    kill_listener_task = asyncio.create_task(
        kill_listener.run(), name="kill_switch_listener"
    )
    tg_task: asyncio.Task[None] | None = None
    if telegram_bot is not None:
        tg_task = asyncio.create_task(telegram_bot.start(), name="telegram_bot")

    await shutdown.wait()
    log.info("shutdown_signaled")

    ib_client.stop()
    account_logger.stop()
    state_broadcaster.stop()
    kill_listener.stop()
    if telegram_bot is not None:
        await telegram_bot.stop()

    await ib_task
    await account_task
    await broadcaster_task
    await kill_listener_task
    if tg_task is not None and not tg_task.done():
        await tg_task

    await redis.aclose()  # type: ignore[attr-defined]
    await engine.dispose()
    log.info("shutdown_complete")
    return 0


def main() -> int:
    """Synchronous entry point used by the console script."""
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())

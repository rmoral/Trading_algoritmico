"""Bot entry point.

Wires settings -> logging -> kill switch -> metrics server -> IBKR
connector + market data feed + discovery strategy engine + Telegram
bot, runs them concurrently until a SIGTERM or SIGINT, then drains
cleanly.

The discovery chain is:

    IBKRBarSource -> MarketDataService -> SRDetector
                  -> StrategyEngine -> OrderRouter -> IBKRBracketSubmitter

`MarketDataSupervisor` keeps the feed subscribed to whatever asset the
operator selected in the web app. With no asset selected the engine
simply idles — exactly the Phase 1 smoke behaviour.
"""

from __future__ import annotations

import asyncio
import signal
import sys

from redis.asyncio import Redis

from tradingbot.config_loader import load_runtime_config
from tradingbot.connector import AccountStateLogger, IBClient
from tradingbot.data.ibkr_bar_source import IBKRBarSource
from tradingbot.data.market_data import MarketDataService
from tradingbot.data.market_data_supervisor import MarketDataSupervisor
from tradingbot.data.sr_detector import SRDetector
from tradingbot.execution.entry_timeout import EntryTimeoutWatcher
from tradingbot.execution.eod_flatten import EndOfDayFlattener
from tradingbot.execution.equity_monitor import EquityMonitor
from tradingbot.execution.equity_tracker import EquityTracker
from tradingbot.execution.fill_handler import FillHandler
from tradingbot.execution.ibkr_fill_stream import IBKRFillStream
from tradingbot.execution.ibkr_submitter import IBKRBracketSubmitter
from tradingbot.execution.market_clock import MarketClock
from tradingbot.execution.order_rate import OrderRateCounter
from tradingbot.execution.order_router import OrderRouter
from tradingbot.logging_setup import configure_logging, get_logger
from tradingbot.monitoring import KillSwitch, TelegramBot, TelegramHandlers
from tradingbot.monitoring.metrics import start_metrics_server
from tradingbot.persistence.database import create_engine, create_session_factory
from tradingbot.persistence.repositories import (
    ActiveAssetRepository,
    BarRepository,
    FillRepository,
    OrderRepository,
    PnLRepository,
    PositionsRepository,
    SignalRepository,
    SRLevelRepository,
)
from tradingbot.reconciliation import Reconciler
from tradingbot.risk import RiskManager
from tradingbot.settings import get_settings
from tradingbot.state import (
    BotStateBroadcaster,
    BotStatePublisher,
    KillSwitchListener,
)
from tradingbot.strategy.engine import RiskContextBuilder, StrategyEngine


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
    bar_repo = BarRepository(session_factory)
    sr_level_repo = SRLevelRepository(session_factory)
    signal_repo = SignalRepository(session_factory)
    active_asset_repo = ActiveAssetRepository(session_factory)
    order_repo = OrderRepository(session_factory)
    fill_repo = FillRepository(session_factory)

    runtime_config = await load_runtime_config(session_factory)
    log.info("runtime_config_loaded", version=runtime_config.version)

    redis = Redis.from_url(settings.redis_url)
    state_publisher = BotStatePublisher(redis)
    order_rate_counter = OrderRateCounter(redis)
    equity_tracker = EquityTracker(redis)

    # Forward reference so the listener can reach the broadcaster
    # built right after.
    broadcaster_holder: dict[str, BotStateBroadcaster] = {}
    first_connected = asyncio.Event()

    async def _on_connection_change(connected: bool) -> None:
        if connected:
            first_connected.set()
        await broadcaster_holder["b"].publish_now()

    ib_client = IBClient(settings, on_connection_change=_on_connection_change)
    state_broadcaster = BotStateBroadcaster(ib_client, kill_switch, state_publisher)
    broadcaster_holder["b"] = state_broadcaster
    account_logger = AccountStateLogger(ib_client)
    equity_monitor = EquityMonitor(
        ib_client=ib_client, equity_tracker=equity_tracker
    )
    kill_listener = KillSwitchListener(redis, kill_switch)

    # ----- discovery chain (CAPA 2 -> 4 -> 3) -----
    risk_manager = RiskManager(runtime_config.risk_limits)
    bar_source = IBKRBarSource(ib_client)
    market_data = MarketDataService(bar_source, bar_repo)
    market_data_supervisor = MarketDataSupervisor(
        active_asset_repo, market_data, connected=ib_client.is_connected
    )
    sr_detector = SRDetector(market_data, sr_level_repo, runtime_config.sr_config)
    bracket_submitter = IBKRBracketSubmitter(ib_client)
    order_router = OrderRouter(
        bracket_submitter,
        risk_manager,
        session_factory,
        positions_repo,
        order_rate_counter=order_rate_counter,
    )
    fill_handler = FillHandler(
        positions_repo=positions_repo,
        order_repo=order_repo,
        fill_repo=fill_repo,
        pnl_repo=pnl_repo,
    )
    fill_stream = IBKRFillStream(ib_client, fill_handler)
    fill_stream.start()
    market_clock = MarketClock()
    eod_flattener = EndOfDayFlattener(
        positions_repo=positions_repo,
        order_repo=order_repo,
        executor=bracket_submitter,
        market_clock=market_clock,
        force_flatten_minutes=runtime_config.force_flatten_before_close_minutes,
    )
    entry_timeout_watcher = EntryTimeoutWatcher(
        positions_repo=positions_repo,
        order_repo=order_repo,
        canceller=bracket_submitter,
        timeout_seconds=runtime_config.entry_limit_cancel_seconds,
    )
    risk_context_builder = RiskContextBuilder(
        kill_switch,
        positions_repo,
        pnl_repo,
        order_rate_counter=order_rate_counter,
        equity_tracker=equity_tracker,
        active_asset_repo=active_asset_repo,
    )
    strategy_engine = StrategyEngine(
        active_asset_repo=active_asset_repo,
        positions_repo=positions_repo,
        signal_repo=signal_repo,
        market_data=market_data,
        sr_detector=sr_detector,
        order_router=order_router,
        risk_context=risk_context_builder,
        config=runtime_config.engine_config,
        market_clock=market_clock,
    )
    reconciler = Reconciler(
        ib_client=ib_client,
        positions_repo=positions_repo,
        session_factory=session_factory,
        kill_switch=kill_switch,
    )

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

    # Infrastructure tasks: connector, monitoring, feed. None of these
    # act on positions, so they start before reconciliation.
    ib_task = asyncio.create_task(ib_client.run(), name="ib_client")
    account_task = asyncio.create_task(account_logger.run(), name="account_state_logger")
    equity_task = asyncio.create_task(
        equity_monitor.run(), name="equity_monitor"
    )
    broadcaster_task = asyncio.create_task(
        state_broadcaster.run(), name="bot_state_broadcaster"
    )
    kill_listener_task = asyncio.create_task(
        kill_listener.run(), name="kill_switch_listener"
    )
    market_data_task = asyncio.create_task(
        market_data_supervisor.run(), name="market_data_supervisor"
    )
    tg_task: asyncio.Task[None] | None = None
    if telegram_bot is not None:
        tg_task = asyncio.create_task(telegram_bot.start(), name="telegram_bot")

    # Gate the trading tasks on a clean startup reconciliation against
    # IBKR (CLAUDE.md §2 principle 7). Wait for the first connection
    # first — reconciliation needs IBKR's real position/order view.
    strategy_task: asyncio.Task[None] | None = None
    eod_task: asyncio.Task[None] | None = None
    entry_timeout_task: asyncio.Task[None] | None = None
    connect_waiter = asyncio.create_task(first_connected.wait())
    shutdown_waiter = asyncio.create_task(shutdown.wait())
    _, pending = await asyncio.wait(
        {connect_waiter, shutdown_waiter}, return_when=asyncio.FIRST_COMPLETED
    )
    for waiter in pending:
        waiter.cancel()

    if not shutdown.is_set():
        reconciliation = await reconciler.reconcile_startup()
        if reconciliation.clean:
            strategy_task = asyncio.create_task(
                strategy_engine.run(), name="strategy_engine"
            )
            eod_task = asyncio.create_task(
                eod_flattener.run(), name="eod_flattener"
            )
            entry_timeout_task = asyncio.create_task(
                entry_timeout_watcher.run(), name="entry_timeout_watcher"
            )
            log.info("trading_tasks_started")
        else:
            log.critical(
                "trading_disabled_reconciliation_mismatch",
                discrepancies=list(reconciliation.discrepancies),
            )

    await shutdown.wait()
    log.info("shutdown_signaled")

    # Stop the engine first so no new orders are routed during drain,
    # then the feed, then the connector and the rest.
    strategy_engine.stop()
    eod_flattener.stop()
    entry_timeout_watcher.stop()
    market_data_supervisor.stop()
    fill_stream.stop()
    ib_client.stop()
    account_logger.stop()
    equity_monitor.stop()
    state_broadcaster.stop()
    kill_listener.stop()
    if telegram_bot is not None:
        await telegram_bot.stop()

    for trading_task in (strategy_task, eod_task, entry_timeout_task):
        if trading_task is not None:
            await trading_task
    await market_data_task
    await market_data.stop()
    await ib_task
    await account_task
    await equity_task
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

"""Read-only reconciliation report: the bot's DB state vs IBKR.

Connects to IB Gateway, pulls the account's positions and working
orders, reads the bot's open position from Postgres, and prints any
discrepancies. Submits nothing and changes nothing — safe to run at
any time.

The bot runs the same check (`classify_reconciliation`) at startup;
this script lets the operator inspect the same comparison on demand,
e.g. after `scripts/flatten_all.py` or a manual TWS intervention.

Usage:
    uv run python scripts/reconcile.py

Exit code: 0 when the views agree, 2 when they diverge.
"""

from __future__ import annotations

import asyncio
import sys

from tradingbot.connector import IBClient
from tradingbot.logging_setup import configure_logging, get_logger
from tradingbot.persistence.database import create_engine, create_session_factory
from tradingbot.persistence.repositories import PositionsRepository
from tradingbot.reconciliation import classify_reconciliation
from tradingbot.settings import LogFormat, get_settings

_SETTLE_SECONDS: float = 2.0


async def main() -> int:
    settings = get_settings()
    configure_logging(
        settings.model_copy(update={"log_format": LogFormat.CONSOLE})
    )
    log = get_logger("reconcile")

    client = IBClient(
        settings, heartbeat_seconds=1.0, connect_timeout_seconds=10.0
    )
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    positions_repo = PositionsRepository(factory)

    try:
        await client.connect()
        if not client.is_connected():
            log.error("connect_failed_or_stopped")
            return 1
        await asyncio.sleep(_SETTLE_SECONDS)  # let ib_insync settle

        broker_positions = await client.get_positions()
        broker_orders = await client.get_open_orders()
        db_position = await positions_repo.get_open_position()

        log.info(
            "db_state",
            open_position=(
                None
                if db_position is None
                else {
                    "symbol": db_position.symbol,
                    "side": db_position.side,
                    "qty": str(db_position.qty),
                    "state": db_position.state,
                }
            ),
        )
        for position in broker_positions:
            log.info(
                "broker_position",
                symbol=position.symbol,
                quantity=str(position.quantity),
                avg_cost=str(position.avg_cost),
            )
        for order in broker_orders:
            log.info(
                "broker_open_order",
                ib_order_id=order.ib_order_id,
                symbol=order.symbol,
                action=order.action,
                quantity=str(order.quantity),
            )

        discrepancies = classify_reconciliation(
            db_position, broker_positions, broker_orders
        )
        if not discrepancies:
            log.info("reconciliation_clean")
            return 0
        for issue in discrepancies:
            log.error("discrepancy", detail=issue)
        return 2
    finally:
        client.disconnect()
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""Emergency liquidation: cancel every working order, flatten every position.

A break-glass tool for the operator. Connects to IB Gateway, cancels
all working orders, and submits a `MarketOrder` to close every open
position — the kill-switch emergency-liquidation path sanctioned by
CLAUDE.md §2 principle 6.

Runs as a dry run by default: it prints what it WOULD do and exits.
Pass `--confirm` to actually send the cancellations and market orders.

This operates purely at the broker level and does not touch Postgres.
After running it the bot's DB view will be stale; the next bot start
(or `scripts/reconcile.py`) will flag the divergence — resolve the DB
before restarting the bot for trading.

Usage:
    uv run python scripts/flatten_all.py            # dry run
    uv run python scripts/flatten_all.py --confirm  # execute
"""

from __future__ import annotations

import asyncio
import sys

from tradingbot.connector import IBClient
from tradingbot.execution.ibkr_submitter import IBKRBracketSubmitter
from tradingbot.logging_setup import configure_logging, get_logger
from tradingbot.persistence.enums import OrderSide
from tradingbot.settings import LogFormat, get_settings

_SETTLE_SECONDS: float = 2.0
_SEND_GRACE_SECONDS: float = 3.0


async def main() -> int:
    confirm = "--confirm" in sys.argv[1:]

    settings = get_settings()
    configure_logging(
        settings.model_copy(update={"log_format": LogFormat.CONSOLE})
    )
    log = get_logger("flatten_all")

    client = IBClient(
        settings, heartbeat_seconds=1.0, connect_timeout_seconds=10.0
    )
    try:
        await client.connect()
        if not client.is_connected():
            log.error("connect_failed_or_stopped")
            return 1
        await asyncio.sleep(_SETTLE_SECONDS)  # let ib_insync settle

        positions = await client.get_positions()
        orders = await client.get_open_orders()
        if not positions and not orders:
            log.info("nothing_to_flatten")
            return 0

        for order in orders:
            log.info(
                "plan_cancel_order",
                ib_order_id=order.ib_order_id,
                symbol=order.symbol,
            )
        for position in positions:
            exit_side = (
                OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
            )
            log.info(
                "plan_flatten_position",
                symbol=position.symbol,
                quantity=str(abs(position.quantity)),
                exit_side=exit_side.value,
            )

        if not confirm:
            log.warning(
                "dry_run", note="re-run with --confirm to execute the plan"
            )
            return 0

        submitter = IBKRBracketSubmitter(client)
        for order in orders:
            await submitter.cancel_order(order.ib_order_id)
        for position in positions:
            exit_side = (
                OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
            )
            await submitter.submit_market_order(
                position.symbol, exit_side, abs(position.quantity)
            )
        # Give ib_insync time to push the orders before disconnecting.
        await asyncio.sleep(_SEND_GRACE_SECONDS)
        log.info(
            "flatten_all_submitted",
            cancelled_orders=len(orders),
            flattened_positions=len(positions),
        )
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

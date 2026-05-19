"""First-contact smoke test for Phase 0.

Connects to IB Gateway on the paper port, dumps the account summary,
and exits. NO orders are submitted, NO bars subscribed. Run this as
the last step of `docs/phase-0-setup.md` to verify the Gateway +
market data + account permissions are correctly wired.

Usage:
    uv run python scripts/smoke_ibkr.py

Env vars (loaded from `.env`):
    IBKR_HOST       (default 127.0.0.1)
    IBKR_PORT       (default 4002 — paper)
    IBKR_CLIENT_ID  (default 1)
    IBKR_ACCOUNT    (informational; logged but not required to connect)
"""

from __future__ import annotations

import asyncio
import sys

from tradingbot.connector import IBClient
from tradingbot.logging_setup import configure_logging, get_logger
from tradingbot.settings import LogFormat, get_settings


async def main() -> int:
    settings = get_settings()
    # Force console output: this script is interactive.
    settings_console = settings.model_copy(update={"log_format": LogFormat.CONSOLE})
    configure_logging(settings_console)
    log = get_logger("smoke_ibkr")

    log.info(
        "connecting",
        host=settings.ibkr_host,
        port=settings.ibkr_port,
        client_id=settings.ibkr_client_id,
        is_live=settings.is_live,
    )

    client = IBClient(settings, heartbeat_seconds=1.0, connect_timeout_seconds=10.0)
    await client.connect()
    if not client.is_connected():
        log.error("connect_failed_or_stopped")
        return 1

    # Give ib_insync a moment to populate accountSummary().
    await asyncio.sleep(2.0)
    summary = client.get_account_summary()

    if not summary:
        log.warning(
            "account_summary_empty",
            note=(
                "Gateway connected but no account summary cached yet — "
                "either market data is not subscribed (see docs/phase-0-setup.md §0.2) "
                "or the API permissions are not signed."
            ),
        )
    else:
        for tag in ("NetLiquidation", "BuyingPower", "TotalCashValue"):
            value = summary.get(tag, "(missing)")
            log.info("account_field", tag=tag, value=value)

    client.disconnect()
    log.info("disconnected_cleanly")
    return 0 if summary else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""Telegram bot scaffold (CAPA 6).

Two layers:
- `TelegramHandlers`: pure async functions that take the bot's state
  (kill switch, ib client, repositories) and return the text to reply
  with. Fully testable without any Telegram dependency.
- `TelegramBot`: thin wrapper that wires the handlers into
  `python-telegram-bot` command handlers and enforces chat-id
  authorization.

Commands in Phase 1: /status, /positions, /pnl, /kill (with `confirm`
as a required arg to actually trip).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from tradingbot.logging_setup import get_logger
from tradingbot.monitoring.kill_switch import KillSwitch
from tradingbot.settings import Settings

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

    from tradingbot.connector.ib_client import IBClient


@runtime_checkable
class PositionsReader(Protocol):
    """Minimal read API the /positions command depends on."""

    async def get_open_position(self) -> _OpenPositionView | None: ...


@runtime_checkable
class PnLReader(Protocol):
    """Minimal read API the /pnl command depends on."""

    async def get_pnl_for_date(self, day: date) -> _PnLDailyView | None: ...


class _OpenPositionView(Protocol):
    symbol: str
    side: str
    qty: Decimal
    avg_entry_price: Decimal
    state: str


class _PnLDailyView(Protocol):
    date: date
    gross_pnl: Decimal
    commissions: Decimal
    net_pnl: Decimal
    n_trades: int
    n_wins: int
    n_losses: int


class TelegramHandlers:
    """Pure logic for each command. Returns the reply text."""

    def __init__(
        self,
        *,
        ib_client: IBClient,
        kill_switch: KillSwitch,
        positions: PositionsReader,
        pnl: PnLReader,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ib = ib_client
        self._kill = kill_switch
        self._positions = positions
        self._pnl = pnl
        self._clock = clock

    async def status(self, *args: str) -> str:
        del args
        connected = self._ib.is_connected()
        kill_state = "TRIPPED" if self._kill.is_tripped() else "idle"
        kill_reason = f" ({self._kill.reason})" if self._kill.is_tripped() else ""
        return (
            f"IB Gateway: {'connected' if connected else 'DOWN'}\n"
            f"Kill switch: {kill_state}{kill_reason}"
        )

    async def positions(self, *args: str) -> str:
        del args
        pos = await self._positions.get_open_position()
        if pos is None:
            return "No open position."
        return (
            f"{pos.symbol} {pos.side} qty={pos.qty} "
            f"avg_entry={pos.avg_entry_price} state={pos.state}"
        )

    async def pnl(self, *args: str) -> str:
        del args
        today = self._clock().date()
        record = await self._pnl.get_pnl_for_date(today)
        if record is None:
            return f"No P&L recorded yet for {today.isoformat()}."
        return (
            f"P&L {record.date.isoformat()}\n"
            f"  Net:     {record.net_pnl}\n"
            f"  Gross:   {record.gross_pnl}\n"
            f"  Commiss: {record.commissions}\n"
            f"  Trades:  {record.n_trades} (W {record.n_wins} / L {record.n_losses})"
        )

    async def kill(self, *args: str) -> str:
        """Two-step trip flow: `/kill` prompts; `/kill confirm` actually trips."""
        if self._kill.is_tripped():
            return f"Kill switch already TRIPPED ({self._kill.reason})."
        if args and args[0].lower() == "confirm":
            self._kill.trip("telegram")
            return "Kill switch TRIPPED. New signals will be refused."
        return "To trip the kill switch send: /kill confirm"


class TelegramBot:
    """Thin python-telegram-bot wrapper.

    Reads the bot token from settings, builds an `Application`, and
    binds the four Phase 1 commands. All messages from chat IDs other
    than `settings.telegram_chat_id` are silently dropped.
    """

    def __init__(self, settings: Settings, handlers: TelegramHandlers) -> None:
        from telegram.ext import Application, CommandHandler

        self._settings = settings
        self._handlers = handlers
        self._log = get_logger(__name__)

        token = settings.telegram_bot_token.get_secret_value()
        self._app = Application.builder().token(token).build()
        self._app.add_handler(CommandHandler("status", self._wrap(handlers.status)))
        self._app.add_handler(CommandHandler("positions", self._wrap(handlers.positions)))
        self._app.add_handler(CommandHandler("pnl", self._wrap(handlers.pnl)))
        self._app.add_handler(CommandHandler("kill", self._wrap(handlers.kill)))

    def _wrap(
        self, fn: Callable[..., Awaitable[str]]
    ) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]:
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            chat = update.effective_chat
            if chat is None or not self._authorized(chat.id):
                self._log.warning(
                    "telegram_unauthorized",
                    chat_id=chat.id if chat else None,
                    command=update.effective_message.text if update.effective_message else None,
                )
                return
            args: list[str] = list(context.args or [])
            text = await fn(*args)
            if update.effective_message:
                await update.effective_message.reply_text(text)

        return handler

    def _authorized(self, chat_id: int) -> bool:
        return str(chat_id) == self._settings.telegram_chat_id

    async def start(self) -> None:
        await self._app.initialize()
        await self._app.start()
        if self._app.updater is not None:
            await self._app.updater.start_polling()

    async def stop(self) -> None:
        if self._app.updater is not None and self._app.updater.running:
            await self._app.updater.stop()
        if self._app.running:
            await self._app.stop()
        await self._app.shutdown()

"""Redis-backed cross-process state.

The bot and the API run as separate processes. They share live state
(bot connection, kill switch, ...) through Redis keys defined in this
package. See CLAUDE.md §3 ("Redis 7+: live position cache, rate
limiting, bot<->API pub/sub").
"""

from tradingbot.state.bot_status import (
    CONNECTION_KEY,
    CONNECTION_TTL_SECONDS,
    KILL_REQUEST_KEY,
    KILL_SWITCH_KEY,
    BotStateBroadcaster,
    BotStatePublisher,
    BotStateReader,
    KillSwitchListener,
    KillSwitchState,
)

__all__ = [
    "CONNECTION_KEY",
    "CONNECTION_TTL_SECONDS",
    "KILL_REQUEST_KEY",
    "KILL_SWITCH_KEY",
    "BotStateBroadcaster",
    "BotStatePublisher",
    "BotStateReader",
    "KillSwitchListener",
    "KillSwitchState",
]

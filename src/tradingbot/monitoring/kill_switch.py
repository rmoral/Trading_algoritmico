"""Process-wide kill switch (CLAUDE.md §2 principle 9).

Once tripped, the order router refuses every new signal. Resetting
requires manual operator action (env, Telegram, web app). Persistence
to Redis / DB will be wired in once the API process exists; for now
the switch is in-process state.
"""

from __future__ import annotations

from prometheus_client import Gauge

from tradingbot.logging_setup import get_logger

KILL_SWITCH_STATE: Gauge = Gauge(
    "tradingbot_kill_switch_state",
    "1 if the kill switch is currently tripped, 0 otherwise.",
)


class KillSwitch:
    """Sacred safety toggle. Wired to Telegram /kill, the web app's red button, and the env var."""

    def __init__(self, *, initially_tripped: bool = False) -> None:
        self._tripped: bool = initially_tripped
        self._reason: str | None = "startup" if initially_tripped else None
        self._log = get_logger(__name__)
        KILL_SWITCH_STATE.set(1 if initially_tripped else 0)
        if initially_tripped:
            self._log.warning("kill_switch_tripped_at_startup", reason=self._reason)

    def is_tripped(self) -> bool:
        return self._tripped

    @property
    def reason(self) -> str | None:
        return self._reason

    def trip(self, reason: str) -> None:
        """Trip the switch. Idempotent."""
        if self._tripped:
            self._log.info("kill_switch_already_tripped", original_reason=self._reason)
            return
        self._tripped = True
        self._reason = reason
        KILL_SWITCH_STATE.set(1)
        self._log.warning("kill_switch_tripped", reason=reason)

    def reset(self, actor: str) -> None:
        """Clear the trip. Logs the actor for audit."""
        if not self._tripped:
            return
        self._log.warning("kill_switch_reset", actor=actor, previous_reason=self._reason)
        self._tripped = False
        self._reason = None
        KILL_SWITCH_STATE.set(0)

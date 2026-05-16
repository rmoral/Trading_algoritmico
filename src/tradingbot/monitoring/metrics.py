"""Prometheus HTTP exporter.

The bot exposes its `prometheus_client` registry over HTTP on a
configurable port (default 9100). Prometheus inside docker-compose
scrapes `host.docker.internal:9100`.
"""

from __future__ import annotations

from prometheus_client import start_http_server

from tradingbot.logging_setup import get_logger

DEFAULT_METRICS_PORT: int = 9100

_started: bool = False


def start_metrics_server(port: int = DEFAULT_METRICS_PORT) -> None:
    """Start the Prometheus exporter. Idempotent: second call is a no-op."""
    global _started
    if _started:
        return
    start_http_server(port)
    _started = True
    get_logger(__name__).info("metrics_server_started", port=port)


def _reset_for_tests() -> None:
    """Test-only: clear the module-global started flag."""
    global _started
    _started = False

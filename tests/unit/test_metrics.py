"""Unit tests for the Prometheus exporter helper."""

from __future__ import annotations

import socket
import urllib.request

import pytest

from tradingbot.monitoring import metrics as metrics_module
from tradingbot.monitoring.metrics import start_metrics_server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(autouse=True)
def _reset_metrics_state() -> None:
    """Each test starts with a fresh `_started` flag."""
    metrics_module._reset_for_tests()


def test_start_metrics_server_serves_metrics_endpoint() -> None:
    port = _free_port()
    start_metrics_server(port=port)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as resp:
        body = resp.read().decode()
    assert "tradingbot_" in body  # at least one of our metrics is registered


def test_start_metrics_server_is_idempotent() -> None:
    port = _free_port()
    start_metrics_server(port=port)
    # A second call with the same port would otherwise raise OSError
    # because the port is already bound; idempotence guards against it.
    start_metrics_server(port=port)

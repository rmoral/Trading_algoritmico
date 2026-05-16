"""Structured logging configuration via `structlog`.

Single public entry point: `configure_logging(settings)`. Call this once
at process startup before any module logs. Output is JSON when
`log_format=json` (default, production), and human-readable when
`log_format=console` (development).

Standard-library logging is routed through structlog so that
third-party libraries (`ib_insync`, `sqlalchemy`, etc.) share the same
formatter and level.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.types import Processor

from tradingbot.settings import LogFormat, Settings


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging for the process.

    Idempotent: calling twice replaces the previous handler. Always call
    once before the first log statement.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        timestamper,
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format == LogFormat.JSON
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.value)

    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Args:
        name: Optional dotted module path. Convention: pass `__name__`.

    Returns:
        A `BoundLogger` that emits through the configured formatter.
    """
    return structlog.stdlib.get_logger(name)

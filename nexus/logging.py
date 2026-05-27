"""Structured logging for Nexus.

Nexus uses :mod:`structlog` for two reasons:

1. **Structured key-value events** flow naturally into the JSON-lines
   progress format the CLI emits in non-TTY mode (see Phase 5). Building
   on a kwargs-based logger from day one means the pipeline never has to
   stringify-and-reparse log records to expose them to the interface
   layer.
2. **Configurable rendering**: human-friendly coloured output for TTYs,
   JSON for log collectors, plain text for CI logs. structlog wraps a
   stdlib logger so this is one line of configuration to switch.

This module provides a single :func:`configure_logging` entry point and
a :func:`get_logger` helper. Library code should call ``get_logger`` and
treat the result as a structlog-bound logger:

    log = get_logger(__name__)
    log.info("graph_built", node_count=120, edge_count=384)

Library code MUST NOT call :func:`configure_logging` itself - that is
the application's job (the CLI/MCP entry point in Phase 5, or a test
fixture). The default behaviour for an unconfigured Nexus is "do
nothing"; structlog will silently swallow log calls until configured.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog

LogFormat = Literal["console", "json"]


def configure_logging(
    *,
    level: int = logging.INFO,
    fmt: LogFormat = "console",
) -> None:
    """Initialise structlog and the underlying stdlib logger.

    Call this exactly once from the application's entry point. Calling
    it more than once is harmless but the last call wins.

    Args:
        level: stdlib log level (use ``logging.DEBUG`` for verbose).
        fmt: ``"console"`` for human-friendly coloured output (default,
            for TTY use) or ``"json"`` for one-event-per-line structured
            output suitable for log collectors and CI.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        level=level,
        stream=sys.stderr,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to a name (typically the module ``__name__``).

    The returned logger is a structlog-bound logger; pass keyword arguments
    to log calls (``log.info("event", key=value)``) rather than f-strings,
    so structured collectors can index them.
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]

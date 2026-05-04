"""Tests for nexus.logging."""

from __future__ import annotations

import logging

from nexus.logging import configure_logging, get_logger


def test_get_logger_returns_logger() -> None:
    logger = get_logger("nexus.tests")

    # structlog returns a bound logger; it should at least expose the
    # standard log methods we use.
    assert hasattr(logger, "info")
    assert hasattr(logger, "warning")
    assert hasattr(logger, "error")
    assert hasattr(logger, "debug")


def test_configure_logging_console_format() -> None:
    # Configuration is a one-shot side effect; this test only verifies
    # the call doesn't raise. The output format is exercised by the CLI
    # snapshot tests in Phase 5.
    configure_logging(level=logging.INFO, fmt="console")

    logger = get_logger("nexus.tests")
    logger.info("smoke", key="value")


def test_configure_logging_json_format() -> None:
    configure_logging(level=logging.DEBUG, fmt="json")

    logger = get_logger("nexus.tests")
    logger.info("smoke", key="value")

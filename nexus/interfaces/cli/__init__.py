"""Click-based command-line interface.

The CLI is a thin adapter - every command parses arguments, builds a
:class:`~nexus.interfaces.cli.context.CliContext`, and delegates to
the pure engine or pipeline. No business logic lives here.

The public entry point is :func:`nexus.interfaces.cli.main.main`,
registered as the ``nexus`` console script in ``pyproject.toml``.
"""

from nexus.interfaces.cli.main import main

__all__ = ["main"]

"""Rendering helpers for CLI output.

Every command in the CLI eventually hands a Pydantic model or a plain
dict to :func:`render`, which decides whether to emit pretty
rich-formatted output or machine-readable JSON based on the resolved
display format on the :class:`~nexus.interfaces.cli.context.CliContext`.

Design notes
============

* JSON output goes through ``model_dump(mode='json', by_alias=True)``
  so field aliases declared on Pydantic output models (``method_name``
  aliased to ``method`` to stay JSON-pretty) are honoured.
* Pretty output uses Rich's :class:`Console` and degrades gracefully
  when colour is disabled. Tables are built for list-shaped payloads;
  scalar / nested payloads fall through to a syntax-highlighted JSON
  block — good enough for v1.
* Nothing in this module opens files. Callers pass a
  :class:`rich.console.Console` (or let the helper default to
  ``Console(file=sys.stdout)``) so tests can capture output.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from rich.console import Console
from rich.json import JSON
from rich.syntax import Syntax

from nexus.interfaces.cli.context import OutputFormat

if TYPE_CHECKING:
    from nexus.interfaces.cli.context import CliContext


def render(
    ctx: CliContext,
    payload: BaseModel | dict[str, Any] | list[Any] | str,
    *,
    console: Console | None = None,
) -> None:
    """Print ``payload`` to stdout in the context's chosen format.

    Args:
        ctx: CLI context controlling format / colour.
        payload: Pydantic model, dict, list, or plain string.
        console: Optional Rich console override. Tests inject a
            capturing console here; production callers leave it
            ``None`` so the helper writes to stdout.
    """
    console = console or _make_console(ctx)
    fmt = ctx.resolved_format()

    if isinstance(payload, str):
        # Plain strings bypass format resolution — the caller has
        # already decided on the shape.
        console.print(payload)
        return

    data = _to_jsonable(payload)

    if fmt == OutputFormat.JSON:
        # ``print`` rather than ``console.print`` so piping the CLI
        # output to ``jq`` never picks up stray ANSI codes even if
        # Rich decided the stream was a TTY.
        sys.stdout.write(json.dumps(data, indent=2, sort_keys=False))
        sys.stdout.write("\n")
        sys.stdout.flush()
        return

    # Pretty path: hand Rich a syntax-highlighted JSON block. When
    # colour is disabled this falls back to unhighlighted JSON.
    if ctx.use_color():
        console.print(JSON.from_data(data))
    else:
        console.print(
            Syntax(
                json.dumps(data, indent=2, sort_keys=False),
                "json",
                theme="ansi_light",
                background_color="default",
            ),
        )


def print_error(ctx: CliContext, message: str, *, hint: str | None = None) -> None:
    """Print a formatted error message to stderr.

    Goes to stderr so callers can pipe stdout to a tool without
    mixing errors into the data stream. Honours ``--no-color``.
    """
    console = Console(file=sys.stderr, no_color=not ctx.use_color())
    if ctx.use_color():
        console.print(f"[bold red]error:[/bold red] {message}")
    else:
        console.print(f"error: {message}")
    if hint is not None:
        if ctx.use_color():
            console.print(f"[dim]hint: {hint}[/dim]")
        else:
            console.print(f"hint: {hint}")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _to_jsonable(payload: BaseModel | dict[str, Any] | list[Any]) -> Any:
    """Coerce the payload into a JSON-serialisable shape."""
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json", by_alias=True)
    return payload


def _make_console(ctx: CliContext) -> Console:
    """Build a Rich console sized for the current terminal."""
    return Console(
        file=sys.stdout,
        no_color=not ctx.use_color(),
        # Force a max width of 120 columns so piped output from CI
        # logs doesn't wrap at whatever the default was. The terminal
        # auto-detects width when running interactively anyway.
        width=120 if not sys.stdout.isatty() else None,
    )

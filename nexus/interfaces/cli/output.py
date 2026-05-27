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
  block - good enough for v1.
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

from nexus.core.query.attribution import build_attribution, render_attribution_footer
from nexus.interfaces.cli.context import OutputFormat

if TYPE_CHECKING:
    from nexus.adapters.storage.project_storage import ProjectMeta
    from nexus.interfaces.cli.context import CliContext


def render(
    ctx: CliContext,
    payload: BaseModel | dict[str, Any] | list[Any] | str,
    *,
    console: Console | None = None,
    meta: ProjectMeta | None = None,
) -> None:
    """Print ``payload`` to stdout in the context's chosen format.

    When *meta* is supplied and ``meta.kind == "package"``, the rendered
    output is augmented with attribution data:

    * **JSON format** - a top-level ``"package"`` key is added to the
      serialised dict. The tool's own ``output_model`` is never mutated;
      the key is injected into the intermediate JSON dict only.
    * **Pretty format** - a footer line is appended after the highlighted
      JSON block (``Indexed from vendor/name@version ...``).

    Project-kind output (or calls where *meta* is ``None``) is unchanged.

    Args:
        ctx: CLI context controlling format / colour.
        payload: Pydantic model, dict, list, or plain string.
        console: Optional Rich console override. Tests inject a
            capturing console here; production callers leave it
            ``None`` so the helper writes to stdout.
        meta: Optional project metadata. When supplied and
            ``meta.kind == "package"``, attribution is appended.
    """
    console = console or _make_console(ctx)
    fmt = ctx.resolved_format()

    if isinstance(payload, str):
        # Plain strings bypass format resolution - the caller has
        # already decided on the shape.
        console.print(payload)
        return

    data = _to_jsonable(payload)

    if fmt == OutputFormat.JSON:
        # Inject the attribution block into the serialised dict when the
        # project is a package. The tool's output_model is NOT touched -
        # we work on the plain dict that _to_jsonable already produced.
        if meta is not None:
            attribution = build_attribution(meta)
            if attribution is not None and isinstance(data, dict):
                data = {**data, "package": attribution}

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

    # Append the attribution footer for package-kind projects. The
    # footer is printed after the JSON block so the machine-readable
    # content comes first and the human-readable credit follows.
    if meta is not None:
        footer = render_attribution_footer(meta)
        if footer:
            console.print()
            console.print(footer)


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

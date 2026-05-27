"""Click root group for the ``nexus`` command-line interface.

The root group is deliberately thin - it does exactly three things:

1. Define the global flags (``--storage-root``, ``--slug``,
   ``--format``, ``--color``/``--no-color``, ``--verbose``,
   ``--yes``) that every subcommand honours.
2. Build a :class:`~nexus.interfaces.cli.context.CliContext` and
   stash it on Click's context object so subcommands can retrieve
   it via ``@click.pass_obj``.
3. Register every subgroup (``query``, ``ask``, …) via
   :func:`_register_subcommands`.

Every subcommand lives in its own module under
``nexus.interfaces.cli.commands`` and is registered here rather than
via plugin discovery - the CLI tree is part of the v1.0 contract and
the registration list is the single source of truth.
"""

from __future__ import annotations

import logging
from pathlib import Path

import click

from nexus.interfaces.cli.context import (
    DEFAULT_ROOT,
    DEFAULT_SLUG,
    CliContext,
    OutputFormat,
)
from nexus.logging import configure_logging
from nexus.version import __version__


@click.group(
    name="nexus",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-V", "--version", prog_name="nexus")
@click.option(
    "--storage-root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Override the root directory for ~/.nexus. Useful in tests and "
        "when running multiple isolated indexes on the same machine."
    ),
)
@click.option(
    "--slug",
    "project_slug",
    default=None,
    help="Project slug under <storage-root>/projects/<slug>/.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice([OutputFormat.AUTO, OutputFormat.JSON, OutputFormat.PRETTY]),
    default=OutputFormat.AUTO,
    help="Output format. 'auto' picks pretty on a TTY and JSON when piped.",
)
@click.option(
    "--color/--no-color",
    default=None,
    help="Force or disable colour output. Default: enabled on a TTY.",
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Verbose logging.")
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Answer yes to every confirmation prompt (non-interactive mode).",
)
@click.pass_context
def main(
    click_ctx: click.Context,
    storage_root: Path | None,
    project_slug: str | None,
    output_format: str,
    color: bool | None,
    verbose: bool,
    yes: bool,
) -> None:
    """Nexus - Laravel code intelligence for AI agents."""
    # Route structured logs to stderr so stdout stays a clean data
    # channel callers can pipe into jq and friends. ``--verbose``
    # raises the level from WARNING (default) to DEBUG.
    configure_logging(
        level=logging.DEBUG if verbose else logging.WARNING,
        fmt="console",
    )

    ctx = CliContext(
        storage_root=storage_root or DEFAULT_ROOT,
        project_slug=project_slug or DEFAULT_SLUG,
        output_format=output_format,
        color=color,
        verbose=verbose,
        yes=yes,
    )
    click_ctx.obj = ctx
    click_ctx.call_on_close(ctx.close)


def _register_subcommands() -> None:
    """Attach every subcommand group to the root.

    Kept as a function (rather than module-level statements) so a
    partial import of this module during test collection doesn't
    eagerly pull in every subcommand's dependencies. Called once at
    module import time below.
    """
    from nexus.interfaces.cli.commands import (  # noqa: PLC0415
        ask,
        cache,
        doctor,
        hooks,
        index,
        init,
        mcp,
        profile,
        query,
        trace,
    )
    from nexus.interfaces.cli.commands.package import package_group  # noqa: PLC0415

    main.add_command(query.query_group)
    main.add_command(ask.ask_command)
    main.add_command(index.index_group)
    main.add_command(init.init_command)
    main.add_command(profile.profile_group)
    main.add_command(doctor.doctor_command)
    main.add_command(cache.cache_group)
    main.add_command(mcp.mcp_group)
    main.add_command(hooks.install_hooks_command)
    main.add_command(trace.trace_group)
    main.add_command(package_group)


_register_subcommands()

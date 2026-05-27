"""``nexus profile`` - inspect built-in profiles and auto-detection.

Three subcommands:

* ``list``   - print every built-in profile name (or a rich table).
* ``detect`` - run auto-detection on a directory and show ranked matches.
* ``show``   - print the full definition of one built-in profile.

These are read-only inspection commands; they never write to disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from nexus.interfaces.cli.output import print_error, render

if TYPE_CHECKING:
    from nexus.interfaces.cli.context import CliContext


@click.group(
    name="profile",
    help="Inspect built-in profiles and auto-detection results.",
)
def profile_group() -> None:
    """Parent group for profile subcommands."""


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@profile_group.command("list", help="List every built-in profile.")
@click.pass_obj
def list_command(cli_ctx: CliContext) -> None:
    """Print the name and display name of every built-in profile."""
    from nexus.profiles import load_builtin_profiles  # noqa: PLC0415

    profiles = list(load_builtin_profiles())
    payload = [
        {
            "name": p.name,
            "display_name": p.display_name,
            "description": (p.description or "").splitlines()[0],
        }
        for p in profiles
    ]
    render(cli_ctx, payload)


# ---------------------------------------------------------------------------
# detect
# ---------------------------------------------------------------------------


@profile_group.command("detect", help="Auto-detect the best profile for a directory.")
@click.option(
    "--project-path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Project root to inspect. Defaults to the current directory.",
)
@click.option(
    "--top",
    "top_n",
    type=int,
    default=3,
    show_default=True,
    help="How many top matches to show.",
)
@click.pass_obj
def detect_command(cli_ctx: CliContext, project_path: Path | None, top_n: int) -> None:
    """Run profile auto-detection and show the ranked matches."""
    from nexus.profiles import ProfileDetector, load_builtin_profiles  # noqa: PLC0415

    path = (project_path or cli_ctx.project_path).resolve()
    builtins = load_builtin_profiles()
    detector = ProfileDetector(builtins=builtins)
    matches = detector.detect(path)

    if not matches:
        render(cli_ctx, {"matches": [], "project_path": str(path)})
        return

    top = matches[:top_n]
    payload = {
        "project_path": str(path),
        "best_match": top[0].profile.name,
        "matches": [
            {
                "rank": i + 1,
                "name": m.profile.name,
                "display_name": m.profile.display_name,
                "score": m.score,
            }
            for i, m in enumerate(top)
        ],
    }
    render(cli_ctx, payload)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@profile_group.command("show", help="Print the full definition of one built-in profile.")
@click.argument("name")
@click.pass_obj
def show_command(cli_ctx: CliContext, name: str) -> None:
    """Show metadata and detection signals for the named profile."""
    from nexus.profiles import load_builtin_profiles  # noqa: PLC0415

    builtins = {p.name: p for p in load_builtin_profiles()}
    profile = builtins.get(name)
    if profile is None:
        available = ", ".join(sorted(builtins))
        print_error(
            cli_ctx,
            f"unknown profile: {name!r}",
            hint=f"available profiles: {available}",
        )
        raise click.exceptions.Exit(1)

    signals = [
        {
            k: v
            for k, v in {
                "kind": sig.kind,
                "weight": sig.weight,
                "path": sig.path,
                "package": sig.package,
                "suffix": sig.suffix,
                "threshold": sig.threshold,
                "interface": sig.interface,
            }.items()
            if v is not None
        }
        for sig in profile.signals
    ]

    payload = {
        "name": profile.name,
        "display_name": profile.display_name,
        "description": profile.description,
        "signals": signals,
        "custom_bases": profile.custom_bases,
        "custom_suffixes": profile.custom_suffixes,
    }
    render(cli_ctx, payload)

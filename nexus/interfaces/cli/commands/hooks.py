"""``nexus install-hooks`` - write a Git post-commit hook that keeps the index fresh.

The hook runs ``nexus index sync --quiet`` in the background after every
commit so the Nexus index never falls more than one commit behind the
working tree.

Design decisions:

* The hook runs in the background (``&``) so it never blocks ``git commit``.
* A pre-existing post-commit hook is not overwritten unless ``--force``
  is given; instead the command exits with code 1 and tells the user
  what to do.
* The written hook script is POSIX-compatible (no Bash-isms) so it works
  on macOS and any POSIX Linux environment.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import TYPE_CHECKING

import click

from nexus.interfaces.cli.output import print_error, render

if TYPE_CHECKING:
    from nexus.interfaces.cli.context import CliContext


# The content of the post-commit hook we write.
# nexus is run with the absolute project path so the hook works even
# when git is invoked from a subdirectory.
_HOOK_TEMPLATE = """\
#!/bin/sh
# Installed by `nexus install-hooks`. Do not edit by hand.
# Runs nexus index sync in the background after every commit.
nexus index sync --project-path "$(git rev-parse --show-toplevel)" --quiet &
"""


@click.command(
    "install-hooks",
    help="Install a Git post-commit hook that auto-syncs the Nexus index.",
)
@click.option(
    "--project-path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Laravel project root (must contain .git/). Defaults to the current directory.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite an existing post-commit hook without prompting.",
)
@click.pass_obj
def install_hooks_command(
    cli_ctx: CliContext,
    project_path: Path | None,
    force: bool,
) -> None:
    """Write a post-commit hook that runs ``nexus index sync`` after every commit.

    The hook is written to ``.git/hooks/post-commit`` inside the project
    directory and is made executable. If a hook already exists the command
    refuses to overwrite it unless ``--force`` (or ``--yes``) is given.
    """
    root = (project_path or cli_ctx.project_path).resolve()
    git_dir = root / ".git"

    if not git_dir.is_dir():
        print_error(
            cli_ctx,
            f"no .git directory found at {root}",
            hint="run this command from inside a git repository",
        )
        raise click.exceptions.Exit(1)

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)

    hook_path = hooks_dir / "post-commit"

    if hook_path.exists() and not (force or cli_ctx.yes):
        print_error(
            cli_ctx,
            f"post-commit hook already exists at {hook_path}",
            hint="run with --force to overwrite it",
        )
        raise click.exceptions.Exit(1)

    hook_path.write_text(_HOOK_TEMPLATE, encoding="utf-8")

    # Make the hook executable by owner, group, and world - same
    # permissions Git itself sets on sample hooks.
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    render(
        cli_ctx,
        {
            "status": "installed",
            "hook_path": str(hook_path),
            "project_path": str(root),
        },
    )

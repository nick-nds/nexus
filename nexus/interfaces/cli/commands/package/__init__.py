"""Click subcommand group for `nexus package ...`."""

from __future__ import annotations

import click

from nexus.interfaces.cli.commands.package.index import index_command


@click.group("package")
def package_group() -> None:
    """Manage Composer-package indexes (Phase 5.5)."""


package_group.add_command(index_command, name="index")

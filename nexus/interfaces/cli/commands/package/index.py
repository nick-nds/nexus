"""`nexus package index <path>` - index a Composer package."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from nexus.adapters.package.composer_metadata import (
    ComposerMetadataError,
    read_composer_metadata,
)
from nexus.interfaces.cli.embedder import build_embedder_from_config
from nexus.pipeline.package_indexer import PackageIndexer, PackageIndexError

if TYPE_CHECKING:
    from nexus.interfaces.cli.context import CliContext


def _extractor_root() -> Path:
    """Locate the bundled nexus-extractor-php Composer package.

    In a developer checkout the tree is::

        nexus-v2/
            nexus/interfaces/cli/commands/package/index.py   (this file, 5 levels deep)
            packages/nexus-extractor-php/

    Walking six parents up from this file yields the repo root.
    In a pip-install scenario the layout differs, but developer mode
    is the only environment exercised by integration tests (tracked as
    a follow-up for production pip-install resolution).
    """
    here = Path(__file__).resolve()
    # parents[0] = package/  (the commands/package dir)
    # parents[1] = commands/
    # parents[2] = cli/
    # parents[3] = interfaces/
    # parents[4] = nexus/
    # parents[5] = repo root
    repo_root = here.parents[5]
    return repo_root / "packages" / "nexus-extractor-php"


@click.command("index")
@click.argument("path", type=click.Path(exists=False, file_okay=False, dir_okay=True))
@click.option("--name", default=None, help="Override <vendor>/<name> from composer.json.")
@click.option("--version", default=None, help="Override version detection.")
@click.option(
    "--timeout",
    type=int,
    default=300,
    show_default=True,
    help="Extraction timeout in seconds.",
)
@click.pass_obj
def index_command(
    cli_ctx: CliContext,
    path: str,
    name: str | None,
    version: str | None,
    timeout: int,
) -> None:
    """Index a Composer package at PATH.

    Detects whether to use in-repo extraction (when the package already
    has ``vendor/bin/testbench`` and ``vendor/nick-nds/nexus-extractor``) or
    Nexus-driven extraction (builds an isolated scratch directory under
    ``~/.nexus/cache/``).

    The indexed data is stored under ``~/.nexus/projects/<vendor>--<name>/``
    and is immediately queryable via ``nexus query`` and the MCP server.
    """
    target = Path(path)
    if not target.is_dir():
        click.echo(f"package_path_missing: {target} does not exist or is not a directory", err=True)
        sys.exit(2)

    try:
        meta = read_composer_metadata(
            target.resolve(), name_override=name, version_override=version
        )
    except ComposerMetadataError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)

    nexus_root = cli_ctx.storage_root
    cache_root = nexus_root / "cache"
    extractor_root = _extractor_root()

    embedder = build_embedder_from_config(nexus_root)
    if embedder is None:
        # Mirror project-mode behaviour: indexing still proceeds (graph
        # is useful on its own), but the user should know semantic
        # search will be unavailable until they configure an embedder.
        click.echo(
            "WARNING: no embedder configured in ~/.nexus/config.yml - "
            "indexing graph only, semantic_search will be unavailable.",
            err=True,
        )

    indexer = PackageIndexer(
        cache_root=cache_root,
        nexus_root=nexus_root,
        extractor_root=extractor_root,
        timeout_s=timeout,
        embedder=embedder,
    )

    try:
        result = indexer.index(meta)
    except PackageIndexError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    click.echo(
        f"Indexed package {meta.full_name}@{meta.version} as project "
        f"{result.slug} (mode={result.mode.value})"
    )

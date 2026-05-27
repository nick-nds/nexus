"""``nexus index`` - drive the Phase 3 indexing pipeline from the CLI.

Four subcommands:

* ``rebuild`` - clear any existing storage and run the full pipeline.
* ``sync`` - re-run the pipeline, relying on Phase 3's content-hash
  embedding cache to skip unchanged chunks. (True incremental graph
  updates are a Phase 3.5 follow-up; ``sync`` today is "rebuild with
  cache", which matches the warm-run measurements in STATUS.md.)
* ``status`` - print the persisted ``meta.json``: last-indexed time,
  node/edge counts, embedder id.
* ``clear`` - wipe the project's storage directory after confirmation.

Pipeline assembly, LSP resolution, cost estimation, profile detection,
progress-reporter selection, and vectors-directory reset live in
``_index_helpers.py`` so this module stays focused on the four
command surfaces.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import click

from nexus.interfaces.cli.commands._index_helpers import _compute_changed_files, run_pipeline
from nexus.interfaces.cli.output import print_error, render

if TYPE_CHECKING:
    from nexus.interfaces.cli.context import CliContext


@click.group(
    name="index",
    help="Build, refresh, inspect, or clear the project's Nexus index.",
)
def index_group() -> None:
    """Parent group for index subcommands."""


# ---------------------------------------------------------------------------
# rebuild / sync - the two pipeline-driving commands
# ---------------------------------------------------------------------------


@index_group.command("rebuild", help="Drop the existing index and run the full pipeline.")
@click.option(
    "--project-path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Project root to index. Defaults to the current directory.",
)
@click.option(
    "--include-tests",
    is_flag=True,
    default=False,
    help="Pass --include-tests through to the PHP extractor.",
)
@click.option(
    "--php",
    "php_binary",
    default=None,
    metavar="CMD",
    help=(
        "PHP binary or command used to invoke the extractor. "
        "Multi-word values are shell-split, so you can pass a Docker "
        "wrapper: --php 'docker exec my-app php'"
    ),
)
@click.option(
    "--container-project-path",
    "container_project_path",
    default=None,
    metavar="PATH",
    type=click.Path(path_type=Path),
    help=(
        "Path where the Laravel project is mounted inside the container. "
        "Required when --php uses docker exec or a similar wrapper so that "
        "artisan and the output file are resolved to their in-container paths. "
        "Example: --container-project-path /var/www"
    ),
)
@click.option(
    "--lsp",
    "lsp_choice",
    default="auto",
    metavar="CHOICE",
    help=(
        "LSP server selection: 'auto' (default - detect intelephense or "
        "phpactor on PATH or in Mason), 'none' (skip CALLS enrichment), or "
        "an explicit binary name/absolute path. With 'auto', the pipeline "
        "still succeeds when no LSP is found, but the graph will not "
        "contain CALLS edges."
    ),
)
@click.pass_obj
def rebuild_command(
    cli_ctx: CliContext,
    project_path: Path | None,
    include_tests: bool,
    php_binary: str | None,
    container_project_path: Path | None,
    lsp_choice: str,
) -> None:
    """Run the full pipeline against the project, replacing any prior index."""
    path = (project_path or cli_ctx.project_path).resolve()
    run_pipeline(
        cli_ctx,
        project_path=path,
        include_tests=include_tests,
        reset=True,
        php_binary=php_binary,
        container_project_path=container_project_path,
        lsp_choice=lsp_choice,
    )


@index_group.command("sync", help="Re-run the pipeline, reusing the embedding cache.")
@click.option(
    "--project-path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Project root to index. Defaults to the current directory.",
)
@click.option(
    "--include-tests",
    is_flag=True,
    default=False,
    help="Pass --include-tests through to the PHP extractor.",
)
@click.option(
    "--php",
    "php_binary",
    default=None,
    metavar="CMD",
    help=(
        "PHP binary or command used to invoke the extractor. "
        "Multi-word values are shell-split, so you can pass a Docker "
        "wrapper: --php 'docker exec my-app php'"
    ),
)
@click.option(
    "--container-project-path",
    "container_project_path",
    default=None,
    metavar="PATH",
    type=click.Path(path_type=Path),
    help=(
        "Path where the Laravel project is mounted inside the container. "
        "Required when --php uses docker exec or a similar wrapper so that "
        "artisan and the output file are resolved to their in-container paths. "
        "Example: --container-project-path /var/www"
    ),
)
@click.option(
    "--lsp",
    "lsp_choice",
    default="auto",
    metavar="CHOICE",
    help=(
        "LSP server selection: 'auto' (default - detect intelephense or "
        "phpactor on PATH or in Mason), 'none' (skip CALLS enrichment), or "
        "an explicit binary name/absolute path. With 'auto', the pipeline "
        "still succeeds when no LSP is found, but the graph will not "
        "contain CALLS edges."
    ),
)
@click.option(
    "--full",
    "force_full",
    is_flag=True,
    default=False,
    help="Force full LSP enrichment, ignoring incremental optimization.",
)
@click.pass_obj
def sync_command(
    cli_ctx: CliContext,
    project_path: Path | None,
    include_tests: bool,
    php_binary: str | None,
    container_project_path: Path | None,
    lsp_choice: str,
    force_full: bool,
) -> None:
    """Run the pipeline without dropping existing storage.

    Uses incremental LSP enrichment when a previous indexed commit is
    available: only methods in files changed since that commit are
    re-queried. Pass --full to force full enrichment (e.g., after a
    rebase that touched many files).
    """
    path = (project_path or cli_ctx.project_path).resolve()

    # Compute changed files for incremental LSP enrichment
    changed_files: set[Path] | None = None
    if not force_full:
        meta = cli_ctx.storage().read_meta()
        last_commit = meta.last_indexed_commit if meta else None
        changed_files = _compute_changed_files(path, last_commit)

    run_pipeline(
        cli_ctx,
        project_path=path,
        include_tests=include_tests,
        reset=False,
        php_binary=php_binary,
        container_project_path=container_project_path,
        lsp_choice=lsp_choice,
        changed_files=changed_files,
    )


# ---------------------------------------------------------------------------
# status / clear - read-side + destructive
# ---------------------------------------------------------------------------


@index_group.command("status", help="Print the project's stored meta.json.")
@click.pass_obj
def status_command(cli_ctx: CliContext) -> None:
    """Show the stored metadata for this project's index."""
    storage = cli_ctx.storage()
    meta = storage.read_meta()
    if meta is None:
        print_error(
            cli_ctx,
            f"no index found under {storage.project_dir}",
            hint="run `nexus index rebuild` to create one",
        )
        raise click.exceptions.Exit(1)
    render(cli_ctx, meta)


@index_group.command("clear", help="Delete the project's index from disk.")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt.",
)
@click.pass_obj
def clear_command(cli_ctx: CliContext, force: bool) -> None:
    """Wipe the project's storage directory after a confirmation prompt."""
    storage = cli_ctx.storage()
    project_dir = storage.project_dir
    if not project_dir.exists():
        render(cli_ctx, {"status": "nothing to clear", "project_dir": str(project_dir)})
        return

    if not (force or cli_ctx.yes):
        click.confirm(
            f"Delete the entire index at {project_dir}?",
            abort=True,
        )

    # Close the store before removing files so SQLite releases the
    # file handle on platforms where open files block deletion.
    cli_ctx.close()
    shutil.rmtree(project_dir)
    render(cli_ctx, {"status": "cleared", "project_dir": str(project_dir)})
